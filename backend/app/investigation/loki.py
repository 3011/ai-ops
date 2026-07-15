from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings
from app.investigation.enums import ToolStatus

settings = get_settings()


@dataclass(slots=True)
class LokiReadError(RuntimeError):
    status: ToolStatus
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def escape_logql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class LokiReadClient:
    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 10.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = (base_url or settings.loki_url).rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(4.0, timeout_seconds))
        self.transport = transport

    async def query_lines(
        self,
        *,
        namespace: str,
        pod: str,
        container: str,
        regex: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> dict[str, Any]:
        if not namespace or not pod or not container:
            raise LokiReadError(ToolStatus.INVALID_REQUEST, "LOKI_SCOPE_INVALID", "日志目标作用域不完整。")
        if len(regex) > 1000 or "`" in regex or "\n" in regex:
            raise LokiReadError(ToolStatus.INVALID_REQUEST, "LOKI_PATTERN_INVALID", "日志类别正则不符合受控规则。")
        selector = (
            f'{{namespace="{escape_logql(namespace)}",pod="{escape_logql(pod)}",'
            f'container="{escape_logql(container)}"}}'
        )
        query = f'{selector} |~ "(?i)({regex})"'
        if (end - start).total_seconds() <= 0 or (end - start).total_seconds() > 4 * 3600:
            raise LokiReadError(ToolStatus.INVALID_REQUEST, "LOKI_RANGE_INVALID", "日志时间范围无效。")
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(
                    "/loki/api/v1/query_range",
                    params={
                        "query": query,
                        "start": int(start.timestamp() * 1_000_000_000),
                        "end": int(end.timestamp() * 1_000_000_000),
                        "limit": max(1, min(limit, 200)),
                        "direction": "backward",
                    },
                )
        except httpx.TimeoutException as exc:
            raise LokiReadError(ToolStatus.UNAVAILABLE, "LOKI_TIMEOUT", "Loki 查询超时。", True) from exc
        except httpx.RequestError as exc:
            raise LokiReadError(ToolStatus.UNAVAILABLE, "LOKI_CONNECTION_ERROR", f"Loki 连接失败：{type(exc).__name__}", True) from exc
        if response.status_code in {401, 403}:
            raise LokiReadError(ToolStatus.DENIED, "LOKI_ACCESS_DENIED", f"Loki 返回 HTTP {response.status_code}。")
        if response.status_code == 429:
            raise LokiReadError(ToolStatus.UNAVAILABLE, "LOKI_RATE_LIMITED", "Loki 请求被限流。", True)
        if response.status_code >= 500:
            raise LokiReadError(ToolStatus.UNAVAILABLE, "LOKI_SERVER_ERROR", f"Loki 返回 HTTP {response.status_code}。", True)
        if response.status_code >= 400:
            raise LokiReadError(ToolStatus.INVALID_REQUEST, "LOKI_HTTP_ERROR", response.text[:500])
        try:
            payload = response.json()
        except ValueError as exc:
            raise LokiReadError(ToolStatus.PARTIAL, "LOKI_INVALID_JSON", "Loki 返回非 JSON 响应。", True) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if payload.get("status") != "success" or not isinstance(data, dict) or not isinstance(data.get("result"), list):
            raise LokiReadError(ToolStatus.PARTIAL, "LOKI_RESPONSE_SCHEMA_MISMATCH", "Loki 响应结构不符合预期。", True)
        if len(data["result"]) > 20:
            raise LokiReadError(ToolStatus.INVALID_REQUEST, "LOKI_HIGH_CARDINALITY", "Loki 日志流数量超过可信工具上限。")
        return {"query": query, "response": payload}
