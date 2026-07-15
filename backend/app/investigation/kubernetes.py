from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.investigation.enums import ToolStatus

K8S_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
K8S_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
K8S_API = "https://kubernetes.default.svc"


@dataclass(slots=True)
class KubernetesReadError(Exception):
    status: ToolStatus
    error_code: str
    message: str
    retryable: bool = False
    http_status: int | None = None

    def __str__(self) -> str:
        return self.message


def classify_http_error(status_code: int, message: str = "") -> KubernetesReadError:
    if status_code == 404:
        return KubernetesReadError(ToolStatus.NOT_FOUND, "KUBERNETES_NOT_FOUND", message or "Kubernetes object not found", False, status_code)
    if status_code in {401, 403}:
        return KubernetesReadError(ToolStatus.DENIED, "KUBERNETES_ACCESS_DENIED", message or "Kubernetes access denied", False, status_code)
    if status_code == 429:
        return KubernetesReadError(ToolStatus.UNAVAILABLE, "RATE_LIMITED", message or "Kubernetes rate limited", True, status_code)
    if status_code >= 500:
        return KubernetesReadError(ToolStatus.UNAVAILABLE, "KUBERNETES_SERVER_ERROR", message or "Kubernetes server error", True, status_code)
    if 400 <= status_code < 500:
        return KubernetesReadError(ToolStatus.INVALID_REQUEST, "KUBERNETES_INVALID_REQUEST", message or "Kubernetes invalid request", False, status_code)
    return KubernetesReadError(ToolStatus.UNAVAILABLE, "KUBERNETES_HTTP_ERROR", message or "Kubernetes HTTP error", True, status_code)


class KubernetesReadClient:
    """Small read-only Kubernetes client with pagination and precise failure semantics."""

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        token = Path(K8S_TOKEN_PATH).read_text().strip() if Path(K8S_TOKEN_PATH).exists() else ""
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._verify: str | bool = K8S_CA_PATH if Path(K8S_CA_PATH).exists() else True
        self._timeout = httpx.Timeout(timeout_seconds, connect=3.0)

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=K8S_API,
                headers=self._headers,
                verify=self._verify,
                timeout=self._timeout,
            ) as client:
                response = await client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise KubernetesReadError(ToolStatus.UNAVAILABLE, "KUBERNETES_TIMEOUT", str(exc) or "Kubernetes timeout", True) from exc
        except httpx.RequestError as exc:
            raise KubernetesReadError(ToolStatus.UNAVAILABLE, "KUBERNETES_CONNECTION_ERROR", str(exc), True) from exc

        if response.status_code >= 400:
            message = response.text[:1000]
            raise classify_http_error(response.status_code, message)
        try:
            payload = response.json()
        except ValueError as exc:
            raise KubernetesReadError(ToolStatus.PARTIAL, "KUBERNETES_MALFORMED_RESPONSE", "Kubernetes response is not valid JSON", True, response.status_code) from exc
        if not isinstance(payload, dict):
            raise KubernetesReadError(ToolStatus.PARTIAL, "KUBERNETES_MALFORMED_RESPONSE", "Kubernetes response is not an object", True, response.status_code)
        return payload

    async def list_pods_page(
        self,
        namespace: str,
        *,
        label_selector: str | None = None,
        continue_token: str | None = None,
        limit: int = 500,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 500))}
        if label_selector:
            params["labelSelector"] = label_selector
        if continue_token:
            params["continue"] = continue_token
        payload = await self.get_json(
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods",
            params,
        )
        token = str((payload.get("metadata") or {}).get("continue") or "") or None
        return list(payload.get("items") or []), token

    async def list_pods(self, namespace: str, *, label_selector: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        continue_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            page, next_token = await self.list_pods_page(
                namespace,
                label_selector=label_selector,
                continue_token=continue_token,
            )
            items.extend(page)
            if not next_token:
                return items
            if next_token in seen_tokens:
                raise KubernetesReadError(
                    ToolStatus.PARTIAL,
                    "KUBERNETES_PAGINATION_LOOP",
                    "Kubernetes continue token repeated",
                    True,
                )
            seen_tokens.add(next_token)
            continue_token = next_token

    async def get_pod(self, namespace: str, pod_name: str) -> dict[str, Any]:
        return await self.get_json(
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/{quote(pod_name, safe='')}"
        )

    async def get_replicaset(self, namespace: str, name: str) -> dict[str, Any]:
        return await self.get_json(
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/replicasets/{quote(name, safe='')}"
        )

    async def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return await self.get_json(
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments/{quote(name, safe='')}"
        )

    async def list_replicasets(self, namespace: str, *, label_selector: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": 500}
        if label_selector:
            params["labelSelector"] = label_selector
        payload = await self.get_json(
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/replicasets",
            params,
        )
        return list(payload.get("items") or [])

    async def read_pod_log(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str,
        previous: bool,
        tail_lines: int,
        limit_bytes: int,
        since_seconds: int,
    ) -> str:
        params = {
            "container": container,
            "previous": str(previous).lower(),
            "tailLines": max(1, min(tail_lines, 500)),
            "limitBytes": max(1024, min(limit_bytes, 262144)),
            "sinceSeconds": max(60, min(since_seconds, 14400)),
            "timestamps": "true",
        }
        path = f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/{quote(pod_name, safe='')}/log"
        try:
            async with httpx.AsyncClient(
                base_url=K8S_API,
                headers=self._headers,
                verify=self._verify,
                timeout=self._timeout,
            ) as client:
                response = await client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise KubernetesReadError(ToolStatus.UNAVAILABLE, "KUBERNETES_TIMEOUT", str(exc) or "Kubernetes timeout", True) from exc
        except httpx.RequestError as exc:
            raise KubernetesReadError(ToolStatus.UNAVAILABLE, "KUBERNETES_CONNECTION_ERROR", str(exc), True) from exc
        if response.status_code >= 400:
            raise classify_http_error(response.status_code, response.text[:1000])
        return response.text
