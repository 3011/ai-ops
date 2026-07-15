from contextlib import asynccontextmanager
import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from prometheus_client import Counter, make_asgi_app
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, init_database
from app.model_config import (
    encrypt_api_key,
    normalize_base_url,
    public_model_settings,
    runtime_from_row,
)
from app.models import (
    AlertInstance,
    AnalysisRun,
    EvidenceSnapshot,
    Incident,
    IncidentAlert,
    ModelSettings,
    OutboxJob,
    WebhookDelivery,
)

settings = get_settings()
WEBHOOKS = Counter("aiops_webhook_received_total", "Alertmanager webhook deliveries")
INCIDENTS = Counter("aiops_incidents_created_total", "Incidents created")


def now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | None) -> datetime:
    if not value or value.startswith("0001-01-01"):
        return now()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def grouping(labels: dict[str, Any]) -> tuple[str, dict[str, str]]:
    selected = {
        "cluster": str(labels.get("cluster") or "default"),
        "namespace": str(labels.get("namespace") or "default"),
        "service": str(
            labels.get("service")
            or labels.get("app")
            or labels.get("job")
            or labels.get("alertname")
            or "unknown"
        ),
        "environment": str(labels.get("environment") or labels.get("env") or "unknown"),
    }
    return "|".join(selected.values()), selected


def classify_origin(
    *,
    title: str = "",
    labels: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
    fingerprint: str = "",
    receiver: str = "",
) -> dict[str, Any]:
    labels = labels or {}
    annotations = annotations or {}
    searchable = " ".join(
        [
            title,
            fingerprint,
            str(labels.get("service") or ""),
            str(labels.get("alertname") or ""),
            str(labels.get("namespace") or ""),
            str(annotations.get("summary") or ""),
            str(annotations.get("description") or ""),
        ]
    ).lower()
    is_test = any(
        marker in searchable
        for marker in (
            "devtest",
            "demo-service",
            "integration-test",
            "aiopsintegrationtest",
            "链路测试",
            "演示服务",
        )
    )
    automated = bool(
        labels.get("prometheus")
        or labels.get("aiops_enabled") == "true"
        or receiver.startswith("aiops-dev/")
        or "integration-test" in searchable
        or "aiopsintegrationtest" in searchable
    )
    source = "alertmanager" if automated else "manual_webhook"
    return {
        "source": source,
        "source_label": "Alertmanager 自动投递" if automated else "手工 Webhook 测试",
        "is_test": is_test,
    }


def incident_payload(row: Incident) -> dict[str, Any]:
    origin = classify_origin(title=row.title, labels=row.labels)
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "severity": row.severity,
        "labels": row.labels,
        "alert_count": row.alert_count,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "resolved_at": row.resolved_at,
        **origin,
    }


def extract_prometheus_series(raw_response: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_response, dict):
        return []
    result = ((raw_response.get("data") or {}).get("result") or [])
    series: list[dict[str, Any]] = []
    for item in result[:6]:
        metric = item.get("metric") or {}
        values = item.get("values") or []
        if not values and item.get("value"):
            values = [item["value"]]
        points = []
        for value in values[-180:]:
            try:
                points.append({"timestamp": float(value[0]), "value": float(value[1])})
            except (TypeError, ValueError, IndexError):
                continue
        series.append(
            {
                "name": metric.get("pod") or metric.get("container") or metric.get("instance") or "total",
                "labels": metric,
                "points": points,
            }
        )
    return series


async def probe_http(name: str, url: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=2.0)) as client:
            response = await client.get(url)
            response.raise_for_status()
        return {
            "name": name,
            "status": "healthy",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "message": "连接正常",
        }
    except Exception as exc:
        return {
            "name": name,
            "status": "unhealthy",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "message": f"{type(exc).__name__}: {exc}"[:500],
        }


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    yield


app = FastAPI(title=settings.app_name, version="0.4.1", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def ready(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(select(1))
    return {"status": "ready"}




class ModelSettingsUpdate(BaseModel):
    provider: str = Field(default="openai-compatible", max_length=64)
    base_url: str = Field(min_length=8, max_length=1000)
    model: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    enabled: bool = True


class ModelConnectionTest(BaseModel):
    base_url: str | None = Field(default=None, max_length=1000)
    model: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=4096)


@app.get("/api/v1/settings/model")
async def get_model_settings(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await session.get(ModelSettings, 1)
    return public_model_settings(row)


@app.put("/api/v1/settings/model")
async def update_model_settings(
    payload: ModelSettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        base_url = normalize_base_url(payload.base_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    model = payload.model.strip()
    if not model:
        raise HTTPException(422, "模型名称不能为空")

    row = await session.get(ModelSettings, 1)
    if row is None:
        row = ModelSettings(
            id=1,
            provider=payload.provider.strip() or "openai-compatible",
            base_url=base_url,
            model=model,
            enabled=payload.enabled,
        )
        session.add(row)
    else:
        row.provider = payload.provider.strip() or "openai-compatible"
        row.base_url = base_url
        row.model = model
        row.enabled = payload.enabled

    if payload.clear_api_key:
        row.api_key_encrypted = None
    elif payload.api_key and payload.api_key.strip():
        try:
            row.api_key_encrypted = encrypt_api_key(payload.api_key.strip())
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    await session.commit()
    await session.refresh(row)
    return public_model_settings(row)


@app.post("/api/v1/settings/model/test")
async def test_model_settings(
    payload: ModelConnectionTest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await session.get(ModelSettings, 1)
    try:
        current = runtime_from_row(row)
        base_url = normalize_base_url(payload.base_url or current.base_url)
        model = (payload.model or current.model).strip()
        api_key = (
            payload.api_key.strip()
            if payload.api_key and payload.api_key.strip()
            else current.api_key
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc

    if not api_key:
        raise HTTPException(400, "请先填写或保存 API Key")
    if not model:
        raise HTTPException(422, "模型名称不能为空")

    started = time.perf_counter()
    status = "failed"
    detail = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 8,
                    "messages": [
                        {
                            "role": "user",
                            "content": "只回复 OK，用于 API 连通性测试。",
                        }
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()
            if not (data.get("choices") or []):
                raise RuntimeError("模型响应缺少 choices")
        status = "success"
        detail = "连接成功"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:1000]

    latency_ms = int((time.perf_counter() - started) * 1000)
    if row is not None:
        row.last_tested_at = now()
        row.last_test_status = status
        row.last_test_message = f"{detail}，耗时 {latency_ms} ms"
        await session.commit()

    if status != "success":
        raise HTTPException(502, detail)
    return {
        "ok": True,
        "message": detail,
        "latency_ms": latency_ms,
        "base_url": base_url,
        "model": model,
    }


@app.post("/api/v1/webhooks/alertmanager", status_code=202)
async def webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    WEBHOOKS.inc()
    payload = await request.json()
    content_hash = stable_hash(payload)
    delivery_id = await session.scalar(
        insert(WebhookDelivery)
        .values(
            content_hash=content_hash,
            receiver=payload.get("receiver"),
            status=str(payload.get("status") or "unknown"),
            group_key=payload.get("groupKey"),
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=[WebhookDelivery.content_hash])
        .returning(WebhookDelivery.id)
    )
    if delivery_id is None:
        duplicate = await session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.content_hash == content_hash)
        )
        await session.rollback()
        return {
            "accepted": True,
            "duplicate": True,
            "delivery_id": duplicate.id if duplicate else None,
            "incidents": ((duplicate.processing_result or {}).get("incidents", []) if duplicate else []),
        }

    delivery = await session.get(WebhookDelivery, delivery_id)
    incident_ids: set[int] = set()
    for alert in payload.get("alerts") or []:
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        fingerprint = alert.get("fingerprint") or stable_hash(labels)[:64]
        starts_at = parse_time(alert.get("startsAt"))
        status = str(alert.get("status") or payload.get("status") or "firing")
        instance = await session.scalar(
            select(AlertInstance).where(
                AlertInstance.fingerprint == fingerprint,
                AlertInstance.starts_at == starts_at,
            )
        )
        if instance is None:
            instance = AlertInstance(
                fingerprint=fingerprint,
                status=status,
                alertname=str(labels.get("alertname") or "UnknownAlert"),
                severity=str(labels.get("severity") or "warning"),
                labels=labels,
                annotations=annotations,
                starts_at=starts_at,
                last_seen_at=now(),
            )
            session.add(instance)
            await session.flush()
        else:
            instance.status = status
            instance.labels = labels
            instance.annotations = annotations
            instance.last_seen_at = now()
        if status == "resolved":
            instance.ends_at = parse_time(alert.get("endsAt"))

        group_key, group_labels = grouping(labels)
        incident = await session.scalar(
            select(Incident)
            .where(
                Incident.grouping_key == group_key,
                Incident.status == "open",
                Incident.last_seen_at >= now() - timedelta(minutes=30),
            )
            .order_by(Incident.last_seen_at.desc())
        )
        if status == "firing":
            if incident is None:
                incident = Incident(
                    grouping_key=group_key,
                    title=(
                        f"[{instance.severity}] {group_labels['service']} - "
                        f"{annotations.get('summary') or instance.alertname}"
                    ),
                    status="open",
                    severity=instance.severity,
                    labels=group_labels,
                    alert_count=0,
                    first_seen_at=starts_at,
                    last_seen_at=now(),
                )
                session.add(incident)
                await session.flush()
                INCIDENTS.inc()
            linked = await session.scalar(
                select(IncidentAlert.id).where(
                    IncidentAlert.incident_id == incident.id,
                    IncidentAlert.alert_instance_id == instance.id,
                )
            )
            if not linked:
                session.add(
                    IncidentAlert(
                        incident_id=incident.id,
                        alert_instance_id=instance.id,
                    )
                )
                incident.alert_count += 1
            incident.last_seen_at = now()
            incident_ids.add(incident.id)
        else:
            linked_ids = (
                await session.scalars(
                    select(IncidentAlert.incident_id).where(
                        IncidentAlert.alert_instance_id == instance.id
                    )
                )
            ).all()
            incident_ids.update(linked_ids)

    await session.flush()
    for incident_id in incident_ids:
        firing_count = await session.scalar(
            select(func.count(AlertInstance.id))
            .join(IncidentAlert, IncidentAlert.alert_instance_id == AlertInstance.id)
            .where(
                IncidentAlert.incident_id == incident_id,
                AlertInstance.status == "firing",
            )
        )
        target = await session.get(Incident, incident_id)
        if target and not firing_count:
            target.status = "resolved"
            target.resolved_at = now()

    await session.flush()
    for incident_id in incident_ids:
        target = await session.get(Incident, incident_id)
        if not target or target.status != "open":
            continue
        key = f"analyze:{incident_id}:{content_hash[:16]}"
        if not await session.scalar(
            select(OutboxJob.id).where(OutboxJob.idempotency_key == key)
        ):
            session.add(
                OutboxJob(
                    job_type="analyze_incident",
                    payload={"incident_id": incident_id},
                    idempotency_key=key,
                    max_attempts=settings.worker_max_attempts,
                )
            )

    if delivery is not None:
        delivery.processing_result = {"incidents": sorted(incident_ids)}
    await session.commit()
    return {
        "accepted": True,
        "duplicate": False,
        "delivery_id": delivery_id,
        "incidents": sorted(incident_ids),
    }


@app.get("/api/v1/dashboard/summary")
async def dashboard_summary(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    since = now() - timedelta(hours=24)
    open_total = await session.scalar(
        select(func.count(Incident.id)).where(Incident.status == "open")
    )
    critical_open = await session.scalar(
        select(func.count(Incident.id)).where(
            Incident.status == "open", Incident.severity == "critical"
        )
    )
    warning_open = await session.scalar(
        select(func.count(Incident.id)).where(
            Incident.status == "open", Incident.severity == "warning"
        )
    )
    incidents_24h = await session.scalar(
        select(func.count(Incident.id)).where(Incident.first_seen_at >= since)
    )
    analysis_total = await session.scalar(select(func.count(AnalysisRun.id)))
    analysis_success = await session.scalar(
        select(func.count(AnalysisRun.id)).where(AnalysisRun.status == "succeeded")
    )
    pending_jobs = await session.scalar(
        select(func.count(OutboxJob.id)).where(
            OutboxJob.status.in_(["pending", "retry", "processing"])
        )
    )
    failed_jobs = await session.scalar(
        select(func.count(OutboxJob.id)).where(OutboxJob.status == "dead")
    )
    recent = (
        await session.scalars(
            select(Incident).order_by(Incident.last_seen_at.desc()).limit(8)
        )
    ).all()
    model_row = await session.get(ModelSettings, 1)
    model_public = public_model_settings(model_row)
    probes = await asyncio.gather(
        probe_http("Prometheus", f"{settings.prometheus_url.rstrip('/')}/-/ready"),
        probe_http("Loki", f"{settings.loki_url.rstrip('/')}/ready"),
        probe_http("Alertmanager", f"{settings.alertmanager_url.rstrip('/')}/-/ready"),
    )
    probes.append(
        {
            "name": "AI 模型",
            "status": (
                "healthy"
                if model_public.get("enabled")
                and model_public.get("api_key_configured")
                and model_public.get("last_test_status") == "success"
                else "warning"
            ),
            "latency_ms": None,
            "message": model_public.get("last_test_message") or "尚未完成连接测试",
        }
    )
    return {
        "open_incidents": int(open_total or 0),
        "critical_open": int(critical_open or 0),
        "warning_open": int(warning_open or 0),
        "incidents_24h": int(incidents_24h or 0),
        "analysis_success_rate": (
            round(int(analysis_success or 0) * 100 / int(analysis_total or 1), 1)
            if analysis_total
            else 0
        ),
        "analysis_total": int(analysis_total or 0),
        "pending_jobs": int(pending_jobs or 0),
        "failed_jobs": int(failed_jobs or 0),
        "recent_incidents": [incident_payload(row) for row in recent],
        "data_sources": probes,
        "model": model_public,
        "generated_at": now(),
    }


@app.get("/api/v1/dashboard/trend")
async def dashboard_trend(
    hours: int = 24,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    hours = max(6, min(hours, 168))
    end = now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = end - timedelta(hours=hours)
    rows = (
        await session.scalars(
            select(Incident).where(Incident.first_seen_at >= start).order_by(Incident.first_seen_at)
        )
    ).all()
    buckets = []
    cursor = start
    while cursor < end:
        bucket_rows = [row for row in rows if cursor <= row.first_seen_at < cursor + timedelta(hours=1)]
        buckets.append(
            {
                "time": cursor,
                "total": len(bucket_rows),
                "critical": sum(1 for row in bucket_rows if row.severity == "critical"),
                "warning": sum(1 for row in bucket_rows if row.severity == "warning"),
                "info": sum(1 for row in bucket_rows if row.severity == "info"),
            }
        )
        cursor += timedelta(hours=1)
    severity = {level: sum(1 for row in rows if row.severity == level) for level in ["critical", "warning", "info"]}
    service_counts: dict[str, int] = {}
    for row in rows:
        service = str((row.labels or {}).get("service") or "unknown")
        service_counts[service] = service_counts.get(service, 0) + 1
    top_services = [
        {"service": service, "count": count}
        for service, count in sorted(service_counts.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    return {"hours": hours, "buckets": buckets, "severity": severity, "top_services": top_services}


@app.get("/api/v1/alerts")
async def list_alerts(
    status: str | None = None,
    severity: str | None = None,
    namespace: str | None = None,
    q: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if status:
        filters.append(AlertInstance.status == status)
    if severity:
        filters.append(AlertInstance.severity == severity)
    if namespace:
        filters.append(AlertInstance.labels["namespace"].astext == namespace)
    if q:
        keyword = f"%{q.strip()}%"
        filters.append(or_(AlertInstance.alertname.ilike(keyword), AlertInstance.fingerprint.ilike(keyword)))
    limit = max(1, min(limit, 500))
    rows = (
        await session.scalars(
            select(AlertInstance).where(*filters).order_by(AlertInstance.last_seen_at.desc()).limit(limit)
        )
    ).all()
    total = await session.scalar(select(func.count(AlertInstance.id)).where(*filters))
    return {
        "items": [
            {
                "id": row.id,
                "fingerprint": row.fingerprint,
                "alertname": row.alertname,
                "status": row.status,
                "severity": row.severity,
                "labels": row.labels,
                "annotations": row.annotations,
                "starts_at": row.starts_at,
                "ends_at": row.ends_at,
                "last_seen_at": row.last_seen_at,
                **classify_origin(
                    title=row.alertname,
                    labels=row.labels,
                    annotations=row.annotations,
                    fingerprint=row.fingerprint,
                ),
            }
            for row in rows
        ],
        "total": int(total or 0),
    }


@app.get("/api/v1/webhook-deliveries")
async def list_webhook_deliveries(
    status: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = [WebhookDelivery.status == status] if status else []
    limit = max(1, min(limit, 500))
    rows = (
        await session.scalars(
            select(WebhookDelivery).where(*filters).order_by(WebhookDelivery.received_at.desc()).limit(limit)
        )
    ).all()
    total = await session.scalar(select(func.count(WebhookDelivery.id)).where(*filters))
    return {
        "items": [
            {
                "id": row.id,
                "receiver": row.receiver,
                "status": row.status,
                "group_key": row.group_key,
                "alert_count": len((row.payload or {}).get("alerts") or []),
                "incidents": (row.processing_result or {}).get("incidents", []),
                "received_at": row.received_at,
                **classify_origin(
                    title=str(((row.payload or {}).get("commonAnnotations") or {}).get("summary") or ""),
                    labels=(row.payload or {}).get("commonLabels") or (((row.payload or {}).get("alerts") or [{}])[0].get("labels") or {}),
                    annotations=(row.payload or {}).get("commonAnnotations") or (((row.payload or {}).get("alerts") or [{}])[0].get("annotations") or {}),
                    fingerprint=str((((row.payload or {}).get("alerts") or [{}])[0].get("fingerprint") or "")),
                    receiver=row.receiver or "",
                ),
            }
            for row in rows
        ],
        "total": int(total or 0),
    }


@app.get("/api/v1/analysis-jobs")
async def list_analysis_jobs(
    status: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = [OutboxJob.status == status] if status else []
    limit = max(1, min(limit, 500))
    rows = (
        await session.scalars(
            select(OutboxJob).where(*filters).order_by(OutboxJob.created_at.desc()).limit(limit)
        )
    ).all()
    total = await session.scalar(select(func.count(OutboxJob.id)).where(*filters))
    return {
        "items": [
            {
                "id": row.id,
                "job_type": row.job_type,
                "incident_id": (row.payload or {}).get("incident_id"),
                "status": row.status,
                "priority": row.priority,
                "attempts": row.attempts,
                "max_attempts": row.max_attempts,
                "last_error": row.last_error,
                "created_at": row.created_at,
                "finished_at": row.finished_at,
            }
            for row in rows
        ],
        "total": int(total or 0),
    }


@app.get("/api/v1/incidents")
async def list_incidents(
    status: str | None = None,
    severity: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    service: str | None = None,
    environment: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if status:
        filters.append(Incident.status == status)
    if severity:
        filters.append(Incident.severity == severity)
    if cluster:
        filters.append(Incident.labels["cluster"].astext == cluster)
    if namespace:
        filters.append(Incident.labels["namespace"].astext == namespace)
    if service:
        filters.append(Incident.labels["service"].astext == service)
    if environment:
        filters.append(Incident.labels["environment"].astext == environment)
    if q:
        keyword = f"%{q.strip()}%"
        filters.append(or_(Incident.title.ilike(keyword), Incident.grouping_key.ilike(keyword)))
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    rows = (
        await session.scalars(
            select(Incident)
            .where(*filters)
            .order_by(Incident.last_seen_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total = await session.scalar(select(func.count(Incident.id)).where(*filters))
    return {"items": [incident_payload(row) for row in rows], "total": int(total or 0)}


@app.get("/api/v1/incidents/{incident_id}")
async def incident_detail(
    incident_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    incident = await session.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "incident not found")
    alerts = (
        await session.execute(
            select(AlertInstance)
            .join(IncidentAlert, IncidentAlert.alert_instance_id == AlertInstance.id)
            .where(IncidentAlert.incident_id == incident_id)
            .order_by(AlertInstance.starts_at)
        )
    ).scalars().all()
    analyses = (
        await session.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.incident_id == incident_id)
            .order_by(AnalysisRun.created_at.desc())
        )
    ).all()
    evidence = (
        await session.scalars(
            select(EvidenceSnapshot)
            .where(EvidenceSnapshot.incident_id == incident_id)
            .order_by(EvidenceSnapshot.created_at.desc())
        )
    ).all()
    detail_origin = classify_origin(title=incident.title, labels=incident.labels)
    if alerts:
        alert_origins = [
            classify_origin(
                title=alert.alertname,
                labels=alert.labels,
                annotations=alert.annotations,
                fingerprint=alert.fingerprint,
            )
            for alert in alerts
        ]
        detail_origin["is_test"] = any(item["is_test"] for item in alert_origins)
        if any(item["source"] == "alertmanager" for item in alert_origins):
            detail_origin["source"] = "alertmanager"
            detail_origin["source_label"] = "Alertmanager 自动投递"
    return {
        "id": incident.id,
        "title": incident.title,
        "status": incident.status,
        "severity": incident.severity,
        "labels": incident.labels,
        "alert_count": incident.alert_count,
        "first_seen_at": incident.first_seen_at,
        "last_seen_at": incident.last_seen_at,
        "resolved_at": incident.resolved_at,
        **detail_origin,
        "alerts": [
            {
                "id": alert.id,
                "alertname": alert.alertname,
                "status": alert.status,
                "severity": alert.severity,
                "labels": alert.labels,
                "annotations": alert.annotations,
                "starts_at": alert.starts_at,
                "ends_at": alert.ends_at,
                **classify_origin(
                    title=alert.alertname,
                    labels=alert.labels,
                    annotations=alert.annotations,
                    fingerprint=alert.fingerprint,
                ),
            }
            for alert in alerts
        ],
        "analyses": [
            {
                "id": analysis.id,
                "status": analysis.status,
                "model": analysis.model,
                "result": analysis.result,
                "error": analysis.error,
                "created_at": analysis.created_at,
                "finished_at": analysis.finished_at,
            }
            for analysis in analyses
        ],
        "evidence": [
            {
                "id": item.id,
                "source_type": item.source_type,
                "query_text": item.query_text,
                "query_start": item.query_start,
                "query_end": item.query_end,
                "summary": item.summary,
                "series": (
                    extract_prometheus_series(item.raw_response)
                    if item.source_type == "prometheus"
                    else []
                ),
                "duration_ms": item.duration_ms,
                "error": item.error,
                "created_at": item.created_at,
            }
            for item in evidence
        ],
    }


@app.post("/api/v1/incidents/{incident_id}/reanalyze", status_code=202)
async def reanalyze_incident(
    incident_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "incident not found")
    job = OutboxJob(
        job_type="analyze_incident",
        payload={"incident_id": incident_id},
        idempotency_key=f"reanalyze:{incident_id}:{now().isoformat()}",
        priority=50,
        max_attempts=settings.worker_max_attempts,
    )
    session.add(job)
    await session.commit()
    return {"accepted": True, "job_id": job.id, "incident_id": incident_id}
