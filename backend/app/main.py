from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from prometheus_client import Counter, make_asgi_app
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, init_database
from app.models import (
    AlertInstance,
    AnalysisRun,
    EvidenceSnapshot,
    Incident,
    IncidentAlert,
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    yield


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def ready(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(select(1))
    return {"status": "ready"}


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


@app.get("/api/v1/incidents")
async def list_incidents(
    status: str | None = None,
    severity: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if status:
        filters.append(Incident.status == status)
    if severity:
        filters.append(Incident.severity == severity)
    rows = (
        await session.scalars(
            select(Incident)
            .where(*filters)
            .order_by(Incident.last_seen_at.desc())
            .limit(200)
        )
    ).all()
    total = await session.scalar(select(func.count(Incident.id)).where(*filters))
    return {
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "status": row.status,
                "severity": row.severity,
                "labels": row.labels,
                "alert_count": row.alert_count,
                "first_seen_at": row.first_seen_at,
                "last_seen_at": row.last_seen_at,
                "resolved_at": row.resolved_at,
            }
            for row in rows
        ],
        "total": int(total or 0),
    }


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
