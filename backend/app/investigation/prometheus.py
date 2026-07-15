from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings
from app.investigation.contracts import TargetContext
from app.investigation.enums import ToolStatus

settings = get_settings()


@dataclass(slots=True)
class PrometheusReadError(RuntimeError):
    status: ToolStatus
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def escape_promql_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def target_scope_selector(target: TargetContext) -> str:
    return ",".join(
        [
            f'namespace="{escape_promql_label(target.namespace)}"',
            f'pod="{escape_promql_label(target.pod_name)}"',
            f'container="{escape_promql_label(target.container_name)}"',
        ]
    )


def resource_scope_selector(target: TargetContext, *, resource: str, unit: str) -> str:
    return ",".join(
        [
            target_scope_selector(target),
            f'uid="{escape_promql_label(target.pod_uid)}"',
            f'resource="{resource}"',
            f'unit="{unit}"',
        ]
    )


class PrometheusReadClient:
    """Bounded Prometheus reader for trusted tools; it is not exposed as a free-query tool."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        max_query_chars: int = 2_000,
        max_range_seconds: int = 4 * 60 * 60,
        max_points: int = 2_000,
        max_series: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.prometheus_url).rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(4.0, timeout_seconds))
        self.max_query_chars = max_query_chars
        self.max_range_seconds = max_range_seconds
        self.max_points = max_points
        self.max_series = max_series
        self.transport = transport

    def _validate_query(self, query: str, target: TargetContext) -> None:
        if not query or len(query) > self.max_query_chars:
            raise PrometheusReadError(
                ToolStatus.INVALID_REQUEST,
                "PROMETHEUS_QUERY_INVALID",
                "Prometheus 查询为空或超过长度限制。",
            )
        required = [
            f'namespace="{escape_promql_label(target.namespace)}"',
            f'pod="{escape_promql_label(target.pod_name)}"',
            f'container="{escape_promql_label(target.container_name)}"',
        ]
        if any(item not in query for item in required):
            raise PrometheusReadError(
                ToolStatus.DENIED,
                "PROMETHEUS_SCOPE_VIOLATION",
                "Prometheus 查询未绑定 TargetContext 的 namespace、Pod 和 Container。",
            )

    def _validate_range(self, start: datetime, end: datetime, step_seconds: int) -> None:
        seconds = (end - start).total_seconds()
        if seconds <= 0 or seconds > self.max_range_seconds:
            raise PrometheusReadError(
                ToolStatus.INVALID_REQUEST,
                "PROMETHEUS_RANGE_INVALID",
                "Prometheus 查询时间范围无效或超过上限。",
            )
        if step_seconds < 10 or step_seconds > 300:
            raise PrometheusReadError(
                ToolStatus.INVALID_REQUEST,
                "PROMETHEUS_STEP_INVALID",
                "Prometheus step 必须在 10 到 300 秒之间。",
            )
        points = int(seconds // step_seconds) + 1
        if points > self.max_points:
            raise PrometheusReadError(
                ToolStatus.INVALID_REQUEST,
                "PROMETHEUS_TOO_MANY_POINTS",
                "Prometheus 查询数据点预算超限。",
            )

    @staticmethod
    def _map_http_error(response: httpx.Response) -> PrometheusReadError:
        code = response.status_code
        if code in {401, 403}:
            return PrometheusReadError(ToolStatus.DENIED, "PROMETHEUS_ACCESS_DENIED", f"Prometheus 返回 HTTP {code}。")
        if code == 429:
            return PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_RATE_LIMITED", "Prometheus 请求被限流。", True)
        if code >= 500:
            return PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_SERVER_ERROR", f"Prometheus 返回 HTTP {code}。", True)
        return PrometheusReadError(ToolStatus.INVALID_REQUEST, "PROMETHEUS_HTTP_ERROR", f"Prometheus 返回 HTTP {code}。")

    def _validate_payload(self, payload: Any, *, expected_result_type: str) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("status") != "success":
            error_type = str((payload or {}).get("errorType") or "") if isinstance(payload, dict) else ""
            message = str((payload or {}).get("error") or "Prometheus 响应格式无效") if isinstance(payload, dict) else "Prometheus 响应不是 JSON 对象"
            status = ToolStatus.INVALID_REQUEST if error_type in {"bad_data", "execution"} else ToolStatus.PARTIAL
            raise PrometheusReadError(status, "PROMETHEUS_INVALID_RESPONSE", message[:1000], False)
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("resultType") != expected_result_type or not isinstance(data.get("result"), list):
            raise PrometheusReadError(ToolStatus.PARTIAL, "PROMETHEUS_RESPONSE_SCHEMA_MISMATCH", "Prometheus resultType 或 result 结构不符合预期。")
        result = data["result"]
        if len(result) > self.max_series:
            raise PrometheusReadError(ToolStatus.INVALID_REQUEST, "PROMETHEUS_HIGH_CARDINALITY", f"Prometheus 返回 {len(result)} 条序列，超过可信工具上限。")
        point_count = sum(len(item.get("values") or []) for item in result if isinstance(item, dict))
        if point_count > self.max_points * self.max_series:
            raise PrometheusReadError(ToolStatus.INVALID_REQUEST, "PROMETHEUS_RESPONSE_TOO_LARGE", "Prometheus 返回的数据点总量超过可信工具上限。")
        return payload

    async def query_range(
        self,
        *,
        query: str,
        target: TargetContext,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> dict[str, Any]:
        self._validate_query(query, target)
        self._validate_range(start, end, step_seconds)
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(
                    "/api/v1/query_range",
                    params={
                        "query": query,
                        "start": start.timestamp(),
                        "end": end.timestamp(),
                        "step": f"{step_seconds}s",
                    },
                )
                if response.status_code >= 400:
                    raise self._map_http_error(response)
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise PrometheusReadError(ToolStatus.PARTIAL, "PROMETHEUS_INVALID_JSON", "Prometheus 返回非 JSON 响应。") from exc
        except PrometheusReadError:
            raise
        except httpx.TimeoutException as exc:
            raise PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_TIMEOUT", "Prometheus 查询超时。", True) from exc
        except httpx.RequestError as exc:
            raise PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_CONNECTION_ERROR", f"Prometheus 连接失败：{type(exc).__name__}", True) from exc
        return self._validate_payload(payload, expected_result_type="matrix")

    async def query_instant(
        self,
        *,
        query: str,
        target: TargetContext,
        at: datetime,
    ) -> dict[str, Any]:
        self._validate_query(query, target)
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(
                    "/api/v1/query",
                    params={"query": query, "time": at.timestamp()},
                )
                if response.status_code >= 400:
                    raise self._map_http_error(response)
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise PrometheusReadError(ToolStatus.PARTIAL, "PROMETHEUS_INVALID_JSON", "Prometheus 返回非 JSON 响应。") from exc
        except PrometheusReadError:
            raise
        except httpx.TimeoutException as exc:
            raise PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_TIMEOUT", "Prometheus 查询超时。", True) from exc
        except httpx.RequestError as exc:
            raise PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_CONNECTION_ERROR", f"Prometheus 连接失败：{type(exc).__name__}", True) from exc
        return self._validate_payload(payload, expected_result_type="vector")

    def _validate_application_query(
        self,
        query: str,
        *,
        namespace: str,
        service: str,
        allowed_metrics: set[str],
    ) -> None:
        if not query or len(query) > self.max_query_chars:
            raise PrometheusReadError(ToolStatus.INVALID_REQUEST, "PROMETHEUS_QUERY_INVALID", "应用指标查询为空或过长。")
        namespace_token = f'namespace="{escape_promql_label(namespace)}"'
        service_tokens = {
            f'service="{escape_promql_label(service)}"',
            f'service_name="{escape_promql_label(service)}"',
            f'job="{escape_promql_label(service)}"',
            f'app="{escape_promql_label(service)}"',
            f'application="{escape_promql_label(service)}"',
        }
        if namespace_token not in query or not any(token in query for token in service_tokens):
            raise PrometheusReadError(
                ToolStatus.DENIED,
                "PROMETHEUS_SCOPE_VIOLATION",
                "应用指标查询未绑定 TargetContext namespace 和 service。",
            )
        import re
        metric_tokens = set(re.findall(r"(?<![A-Za-z0-9_:])([A-Za-z_:][A-Za-z0-9_:]*)\s*(?:\{|\[)", query))
        ignored = {"rate", "sum", "histogram_quantile", "clamp_min"}
        metric_tokens -= ignored
        if not metric_tokens or not metric_tokens.issubset(allowed_metrics):
            raise PrometheusReadError(
                ToolStatus.DENIED,
                "PROMETHEUS_METRIC_NOT_ALLOWED",
                "应用指标查询包含未注册指标。",
            )

    async def query_range_application(
        self,
        *,
        query: str,
        namespace: str,
        service: str,
        allowed_metrics: set[str],
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> dict[str, Any]:
        self._validate_application_query(
            query,
            namespace=namespace,
            service=service,
            allowed_metrics=allowed_metrics,
        )
        self._validate_range(start, end, step_seconds)
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(
                    "/api/v1/query_range",
                    params={
                        "query": query,
                        "start": start.timestamp(),
                        "end": end.timestamp(),
                        "step": f"{step_seconds}s",
                    },
                )
                if response.status_code >= 400:
                    raise self._map_http_error(response)
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise PrometheusReadError(ToolStatus.PARTIAL, "PROMETHEUS_INVALID_JSON", "Prometheus 返回非 JSON 响应。") from exc
        except PrometheusReadError:
            raise
        except httpx.TimeoutException as exc:
            raise PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_TIMEOUT", "Prometheus 查询超时。", True) from exc
        except httpx.RequestError as exc:
            raise PrometheusReadError(ToolStatus.UNAVAILABLE, "PROMETHEUS_CONNECTION_ERROR", f"Prometheus 连接失败：{type(exc).__name__}", True) from exc
        return self._validate_payload(payload, expected_result_type="matrix")
