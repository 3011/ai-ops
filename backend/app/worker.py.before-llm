import asyncio
import os
import re
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal, init_database
from app.models import (
    AlertInstance,
    AnalysisRun,
    EvidenceSnapshot,
    Incident,
    IncidentAlert,
    OutboxJob,
)

settings = get_settings()
worker_id = f"{socket.gethostname()}:{os.getpid()}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def sanitize_log_line(value: str) -> str:
    value = re.sub(
        r"(?i)(authorization|bearer|token|password|passwd|secret|cookie|api[_-]?key)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        value,
    )
    return value.replace("\x00", "")[:1000]


def prometheus_summary(payload: dict[str, Any], query_name: str) -> dict[str, Any]:
    result = ((payload.get("data") or {}).get("result") or [])
    samples: list[float] = []
    for series in result:
        values = series.get("values") or []
        if not values and series.get("value"):
            values = [series["value"]]
        for item in values:
            try:
                samples.append(float(item[1]))
            except (TypeError, ValueError, IndexError):
                continue
    return {
        "query_name": query_name,
        "series_count": len(result),
        "sample_count": len(samples),
        "min_value": min(samples) if samples else None,
        "max_value": max(samples) if samples else None,
        "latest_value": samples[-1] if samples else None,
    }


def loki_summary(payload: dict[str, Any]) -> dict[str, Any]:
    result = ((payload.get("data") or {}).get("result") or [])
    lines: list[str] = []
    for stream in result:
        for item in stream.get("values") or []:
            if len(item) > 1:
                lines.append(sanitize_log_line(str(item[1])))
    return {
        "stream_count": len(result),
        "line_count": len(lines),
        "sample_lines": lines[:20],
    }


async def query_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
) -> tuple[dict[str, Any] | None, int, str | None]:
    started = time.perf_counter()
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in (None, "success"):
            raise RuntimeError(str(payload.get("error") or payload.get("status")))
        return payload, int((time.perf_counter() - started) * 1000), None
    except Exception as exc:  # noqa: BLE001 - evidence errors are persisted, not fatal
        return None, int((time.perf_counter() - started) * 1000), f"{type(exc).__name__}: {exc}"[:4000]


async def collect_evidence(incident_id: int) -> tuple[Incident, list[dict[str, Any]]]:
    async with SessionLocal() as session:
        incident = await session.get(Incident, incident_id)
        if incident is None:
            raise ValueError(f"incident {incident_id} not found")
        alerts = (
            await session.scalars(
                select(AlertInstance)
                .join(IncidentAlert, IncidentAlert.alert_instance_id == AlertInstance.id)
                .where(IncidentAlert.incident_id == incident_id)
                .order_by(AlertInstance.starts_at)
            )
        ).all()

    query_start = incident.first_seen_at - timedelta(minutes=15)
    query_end = incident.first_seen_at + timedelta(minutes=30)
    namespace = str((incident.labels or {}).get("namespace") or "")
    service = str((incident.labels or {}).get("service") or "")
    alertname = alerts[0].alertname if alerts else ""

    ns_selector = f'namespace="{escape_label_value(namespace)}"' if namespace else ""
    pod_regex = escape_label_value(re.escape(service)) if service else ".*"

    prometheus_queries: list[tuple[str, str]] = []
    if alertname:
        prometheus_queries.append(
            (
                "alert_state",
                f'ALERTS{{alertname="{escape_label_value(alertname)}"}}',
            )
        )
    if namespace and service:
        prometheus_queries.extend(
            [
                (
                    "container_cpu_cores",
                    "sum(rate(container_cpu_usage_seconds_total"
                    f'{{{ns_selector},pod=~".*{pod_regex}.*",container!=""}}[5m]))',
                ),
                (
                    "container_memory_bytes",
                    "sum(container_memory_working_set_bytes"
                    f'{{{ns_selector},pod=~".*{pod_regex}.*",container!=""}})',
                ),
                (
                    "container_restarts",
                    "sum(increase(kube_pod_container_status_restarts_total"
                    f'{{{ns_selector},pod=~".*{pod_regex}.*"}}[30m]))',
                ),
            ]
        )

    evidence: list[dict[str, Any]] = []
    timeout = httpx.Timeout(8.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for query_name, query_text in prometheus_queries:
            payload, duration_ms, error = await query_json(
                client,
                f"{settings.prometheus_url.rstrip('/')}/api/v1/query_range",
                {
                    "query": query_text,
                    "start": query_start.timestamp(),
                    "end": query_end.timestamp(),
                    "step": "60s",
                },
            )
            evidence.append(
                {
                    "source_type": "prometheus",
                    "query_text": query_text,
                    "query_start": query_start,
                    "query_end": query_end,
                    "summary": prometheus_summary(payload or {}, query_name),
                    "raw_response": payload,
                    "duration_ms": duration_ms,
                    "error": error,
                }
            )

        if namespace:
            selector = f'{{namespace="{escape_label_value(namespace)}"}}'
            if service:
                selector = (
                    f'{{namespace="{escape_label_value(namespace)}",'
                    f'pod=~".*{pod_regex}.*"}}'
                )
            logql = f'{selector} |~ "(?i)(error|exception|fatal|panic)"'
            payload, duration_ms, error = await query_json(
                client,
                f"{settings.loki_url.rstrip('/')}/loki/api/v1/query_range",
                {
                    "query": logql,
                    "start": int(query_start.timestamp() * 1_000_000_000),
                    "end": int(query_end.timestamp() * 1_000_000_000),
                    "limit": 100,
                    "direction": "backward",
                },
            )
            evidence.append(
                {
                    "source_type": "loki",
                    "query_text": logql,
                    "query_start": query_start,
                    "query_end": query_end,
                    "summary": loki_summary(payload or {}),
                    "raw_response": payload,
                    "duration_ms": duration_ms,
                    "error": error,
                }
            )

    return incident, evidence


async def claim() -> int | None:
    async with SessionLocal() as session:
        async with session.begin():
            stale_before = utcnow() - timedelta(seconds=settings.worker_lock_timeout_seconds)
            await session.execute(
                update(OutboxJob)
                .where(
                    OutboxJob.status == "processing",
                    OutboxJob.locked_at.is_not(None),
                    OutboxJob.locked_at < stale_before,
                )
                .values(
                    status="retry",
                    available_at=utcnow(),
                    locked_at=None,
                    locked_by=None,
                    last_error="worker lock expired and was reclaimed",
                )
            )
            job = await session.scalar(
                select(OutboxJob)
                .where(
                    OutboxJob.status.in_(["pending", "retry"]),
                    OutboxJob.available_at <= utcnow(),
                )
                .order_by(OutboxJob.priority, OutboxJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "processing"
            job.locked_at = utcnow()
            job.locked_by = worker_id
            job.attempts += 1
            await session.flush()
            return job.id


async def process(job_id: int) -> None:
    async with SessionLocal() as session:
        job = await session.get(OutboxJob, job_id)
        if job is None:
            return
        if job.job_type != "analyze_incident":
            raise ValueError(f"unsupported job type: {job.job_type}")
        incident_id = int(job.payload["incident_id"])

    incident, collected = await collect_evidence(incident_id)

    async with SessionLocal() as session:
        evidence_refs: list[int] = []
        available_sources: set[str] = set()
        missing_evidence: list[str] = []
        recommended_checks: list[str] = []

        for item in collected:
            row = EvidenceSnapshot(incident_id=incident_id, **item)
            session.add(row)
            await session.flush()
            evidence_refs.append(row.id)
            if item["error"]:
                missing_evidence.append(f"{item['source_type']} 查询失败：{item['error']}")
            else:
                available_sources.add(item["source_type"])

        log_evidence = next((x for x in collected if x["source_type"] == "loki"), None)
        if log_evidence and (log_evidence["summary"].get("line_count") or 0) > 0:
            recommended_checks.append("优先查看 Loki 错误日志样本，并与发布或配置变更时间对齐。")
        if any(
            x["source_type"] == "prometheus" and (x["summary"].get("sample_count") or 0) > 0
            for x in collected
        ):
            recommended_checks.append("在 Grafana 中确认告警前后 15 分钟的指标趋势和异常拐点。")
        if not recommended_checks:
            recommended_checks.append("确认告警标签中的 namespace、service 与监控/日志标签是否一致。")

        result = {
            "summary": (
                "证据采集完成："
                + ("、".join(sorted(available_sources)) if available_sources else "未获取到可用数据")
                + "。当前未启用外部大模型，因此不生成无证据根因结论。"
            ),
            "severity_assessment": incident.severity,
            "root_cause_hypotheses": [],
            "recommended_checks": recommended_checks,
            "recommended_actions": [],
            "missing_evidence": missing_evidence,
            "risk_notes": ["所有结论需由运维人员结合原始 Grafana 数据确认。"],
            "evidence_refs": evidence_refs,
        }
        session.add(
            AnalysisRun(
                incident_id=incident_id,
                status="evidence_ready",
                model=None,
                result=result,
                finished_at=utcnow(),
            )
        )
        job = await session.get(OutboxJob, job_id)
        if job is not None:
            job.status = "succeeded"
            job.finished_at = utcnow()
            job.locked_at = None
            job.locked_by = None
        await session.commit()


async def run() -> None:
    await init_database()
    while True:
        job_id = await claim()
        if job_id is None:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        try:
            await process(job_id)
        except Exception as exc:  # noqa: BLE001 - worker retries are intentional
            async with SessionLocal() as session:
                job = await session.get(OutboxJob, job_id)
                if job is not None:
                    job.last_error = f"{type(exc).__name__}: {exc}"[:4000]
                    job.locked_at = None
                    job.locked_by = None
                    if job.attempts >= job.max_attempts:
                        job.status = "dead"
                        job.finished_at = utcnow()
                    else:
                        job.status = "retry"
                        job.available_at = utcnow() + timedelta(seconds=min(300, 2**job.attempts))
                    await session.commit()


if __name__ == "__main__":
    asyncio.run(run())
