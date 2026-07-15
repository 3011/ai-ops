from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

K8S_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
K8S_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
K8S_API = "https://kubernetes.default.svc"


class KubernetesReadClient:
    """Small read-only Kubernetes client used by trusted tools."""

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        token = Path(K8S_TOKEN_PATH).read_text().strip()
        self._headers = {"Authorization": f"Bearer {token}"}
        self._verify: str | bool = K8S_CA_PATH if Path(K8S_CA_PATH).exists() else True
        self._timeout = httpx.Timeout(timeout_seconds, connect=3.0)

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=K8S_API,
            headers=self._headers,
            verify=self._verify,
            timeout=self._timeout,
        ) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    async def list_pods(self, namespace: str) -> list[dict[str, Any]]:
        payload = await self.get_json(
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods",
            {"limit": 500},
        )
        return list(payload.get("items") or [])

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
