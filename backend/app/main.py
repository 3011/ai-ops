from contextlib import asynccontextmanager
import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import secrets
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, make_asgi_app
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_session, init_database
from app.auth import principal_from_request, record_audit, seed_auth_and_release
from app.governance import router as governance_router
from app.model_config import (
    encrypt_api_key,
    normalize_base_url,
    public_model_settings,
    runtime_from_row,
)
from app.models import (
    AlertInstance,
    AnalysisRun,
    ChangeEvent,
    EvidenceSnapshot,
    Incident,
    IncidentAlert,
    ModelSettings,
    OutboxJob,
    TraceSettings,
    WebhookDelivery,
    ReleaseNote,
    User,
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
    if labels.get("aiops_test") is not None:
        selected["aiops_test"] = str(labels.get("aiops_test"))
    if labels.get("aiops_enabled") is not None:
        selected["aiops_enabled"] = str(labels.get("aiops_enabled"))
    return "|".join(selected[key] for key in ("cluster", "namespace", "service", "environment")), selected


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
    is_test = str(labels.get("aiops_test") or "").lower() == "true" or any(
        marker in searchable
        for marker in (
            "devtest",
            "demo-service",
            "integration-test",
            "aiopsintegrationtest",
            "链路测试",
            "演示服务",
            "aiops-scenario",
            "场景测试",
            "降级测试",
            "自动发现测试",
            "自动规划测试",
            "生命周期回归测试",
            "fingerprint 生命周期回归",
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


def incident_is_test(row: Incident) -> bool:
    return bool(classify_origin(title=row.title, labels=row.labels).get("is_test"))


def alert_payload(row: AlertInstance) -> dict[str, Any]:
    return {
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
        **classify_origin(title=row.alertname, labels=row.labels, annotations=row.annotations, fingerprint=row.fingerprint),
    }


def delivery_payload(row: WebhookDelivery) -> dict[str, Any]:
    payload = row.payload or {}
    first_alert = (payload.get("alerts") or [{}])[0]
    return {
        "id": row.id,
        "receiver": row.receiver,
        "status": row.status,
        "group_key": row.group_key,
        "alert_count": len(payload.get("alerts") or []),
        "incidents": (row.processing_result or {}).get("incidents", []),
        "received_at": row.received_at,
        **classify_origin(
            title=str((payload.get("commonAnnotations") or {}).get("summary") or ""),
            labels=payload.get("commonLabels") or first_alert.get("labels") or {},
            annotations=payload.get("commonAnnotations") or first_alert.get("annotations") or {},
            fingerprint=str(first_alert.get("fingerprint") or ""),
            receiver=row.receiver or "",
        ),
    }


def change_event_payload(row: ChangeEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "event_type": row.event_type,
        "is_test": row.is_test,
        "cluster": row.cluster,
        "namespace": row.namespace,
        "service": row.service,
        "environment": row.environment,
        "workload_kind": row.workload_kind,
        "workload_name": row.workload_name,
        "version": row.version,
        "commit_sha": row.commit_sha,
        "image": row.image,
        "actor": row.actor,
        "url": row.url,
        "title": row.title,
        "description": row.description,
        "metadata": row.details or {},
        "occurred_at": row.occurred_at,
        "received_at": row.received_at,
    }


def trace_settings_payload(row: TraceSettings | None) -> dict[str, Any]:
    if row is None:
        return {
            "provider": "tempo",
            "base_url": "",
            "enabled": False,
            "service_tag": "service.name",
            "last_tested_at": None,
            "last_test_status": None,
            "last_test_message": "尚未配置 Trace 数据源",
        }
    return {
        "provider": row.provider,
        "base_url": row.base_url or "",
        "enabled": row.enabled,
        "service_tag": row.service_tag,
        "last_tested_at": row.last_tested_at,
        "last_test_status": row.last_test_status,
        "last_test_message": row.last_test_message,
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
    async with SessionLocal() as session:
        await seed_auth_and_release(session)
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.mount("/metrics", make_asgi_app())

PUBLIC_API_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/webhooks/alertmanager",
    "/api/v1/webhooks/deployment-events",
}


def route_permission(method: str, path: str) -> str | None:
    if path.startswith("/api/v1/auth/"):
        return None
    if path.startswith("/api/v1/dashboard"):
        return "dashboard.view"
    if path.startswith("/api/v1/incidents"):
        return "incidents.analyze" if method != "GET" else "incidents.view"
    if path.startswith(("/api/v1/alerts", "/api/v1/webhook-deliveries", "/api/v1/analysis-jobs")):
        return "incidents.view"
    if path.startswith("/api/v1/change-events"):
        return "changes.view"
    if path.startswith("/api/v1/settings"):
        return "settings.view" if method == "GET" else "settings.manage"
    if path.startswith(("/api/v1/users", "/api/v1/roles", "/api/v1/permissions")):
        return "users.view" if method == "GET" else "users.manage"
    if path.startswith("/api/v1/audit-logs"):
        return "audit.view"
    if path.startswith("/api/v1/releases"):
        return "versions.view" if method == "GET" else "versions.manage"
    return "dashboard.view"


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/v1/") or path in PUBLIC_API_PATHS:
        return await call_next(request)
    async with SessionLocal() as session:
        principal = await principal_from_request(request, session)
    if principal is None:
        return JSONResponse({"detail": "未登录或会话已失效"}, status_code=401)
    request.state.principal = principal
    if principal.must_change_password and path not in {
        "/api/v1/auth/me", "/api/v1/auth/logout", "/api/v1/auth/change-password"
    }:
        return JSONResponse({"detail": "首次登录必须先修改密码"}, status_code=428)
    permission = route_permission(request.method, path)
    if permission and permission not in principal.permissions:
        return JSONResponse({"detail": f"缺少权限：{permission}"}, status_code=403)
    return await call_next(request)


app.include_router(governance_router)


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def ready(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(select(1))
    return {"status": "ready"}




class DeploymentEventInput(BaseModel):
    source: str = Field(default="cicd", max_length=64)
    event_type: str = Field(default="deployment", max_length=64)
    is_test: bool = False
    cluster: str | None = Field(default=None, max_length=255)
    namespace: str = Field(min_length=1, max_length=255)
    service: str = Field(min_length=1, max_length=255)
    environment: str | None = Field(default=None, max_length=128)
    workload_kind: str | None = Field(default=None, max_length=64)
    workload_name: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=255)
    commit_sha: str | None = Field(default=None, max_length=255)
    image: str | None = Field(default=None, max_length=1000)
    actor: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceSettingsUpdate(BaseModel):
    provider: str = Field(default="tempo", max_length=64)
    base_url: str | None = Field(default=None, max_length=1000)
    enabled: bool = False
    service_tag: str = Field(default="service.name", max_length=255)


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
    request: Request,
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

    await record_audit(
        session,
        principal=request.state.principal,
        action="model_settings_updated",
        resource_type="settings",
        resource_id="model",
        details={"provider": row.provider, "base_url": row.base_url, "model": row.model, "enabled": row.enabled, "api_key_changed": bool(payload.api_key or payload.clear_api_key)},
        request=request,
    )
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


@app.post("/api/v1/webhooks/deployment-events", status_code=202)
async def deployment_event_webhook(
    payload: DeploymentEventInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    configured_token = settings.release_webhook_token or ""
    supplied_token = request.headers.get("x-aiops-token", "")
    if configured_token and not secrets.compare_digest(configured_token, supplied_token):
        raise HTTPException(401, "invalid deployment webhook token")
    namespace = payload.namespace.strip()
    service = payload.service.strip()
    occurred_at = payload.occurred_at or now()
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    normalized = payload.model_dump(mode="json")
    normalized["occurred_at"] = occurred_at.isoformat()
    content_hash = stable_hash(normalized)
    existing = await session.scalar(select(ChangeEvent).where(ChangeEvent.content_hash == content_hash))
    if existing is not None:
        return {"accepted": True, "duplicate": True, "event": change_event_payload(existing)}
    title = (payload.title or f"{service} {payload.event_type}").strip()
    event = ChangeEvent(
        content_hash=content_hash,
        source=payload.source.strip() or "cicd",
        event_type=payload.event_type.strip() or "deployment",
        is_test=payload.is_test or service.startswith("aiops-scenario-") or "测试" in title,
        cluster=(payload.cluster or "").strip() or None,
        namespace=namespace,
        service=service,
        environment=(payload.environment or "").strip() or None,
        workload_kind=(payload.workload_kind or "").strip() or None,
        workload_name=(payload.workload_name or "").strip() or None,
        version=(payload.version or "").strip() or None,
        commit_sha=(payload.commit_sha or "").strip() or None,
        image=(payload.image or "").strip() or None,
        actor=(payload.actor or "").strip() or None,
        url=(payload.url or "").strip() or None,
        title=title,
        description=payload.description,
        details=payload.metadata or {},
        occurred_at=occurred_at,
    )
    session.add(event)
    await session.flush()
    related_incidents = (
        await session.scalars(
            select(Incident).where(
                Incident.status == "open",
                Incident.labels["namespace"].astext == namespace,
                Incident.labels["service"].astext == service,
                Incident.first_seen_at >= occurred_at - timedelta(hours=4),
                Incident.first_seen_at <= occurred_at + timedelta(hours=4),
            )
        )
    ).all()
    queued: list[int] = []
    for incident in related_incidents:
        key = f"change:{event.id}:reanalyze:{incident.id}"
        session.add(OutboxJob(
            job_type="analyze_incident",
            payload={"incident_id": incident.id, "change_event_id": event.id},
            idempotency_key=key,
            priority=40,
            max_attempts=settings.worker_max_attempts,
        ))
        queued.append(incident.id)
    await session.commit()
    await session.refresh(event)
    return {"accepted": True, "duplicate": False, "event": change_event_payload(event), "reanalyze_incidents": queued}


@app.get("/api/v1/change-events")
async def list_change_events(
    namespace: str | None = None,
    service: str | None = None,
    source: str | None = None,
    include_test: bool = False,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if namespace:
        filters.append(ChangeEvent.namespace == namespace)
    if service:
        filters.append(ChangeEvent.service == service)
    if source:
        filters.append(ChangeEvent.source == source)
    rows = (await session.scalars(select(ChangeEvent).where(*filters).order_by(ChangeEvent.occurred_at.desc()).limit(2000))).all()
    hidden = sum(1 for row in rows if row.is_test)
    if not include_test:
        rows = [row for row in rows if not row.is_test]
    limit = max(1, min(limit, 500))
    return {"items": [change_event_payload(row) for row in rows[:limit]], "total": len(rows), "hidden_test_count": 0 if include_test else hidden, "include_test": include_test}


@app.get("/api/v1/settings/traces")
async def get_trace_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return trace_settings_payload(await session.get(TraceSettings, 1))


@app.put("/api/v1/settings/traces")
async def update_trace_settings(
    payload: TraceSettingsUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    provider = payload.provider.strip().lower()
    if provider not in {"tempo", "jaeger"}:
        raise HTTPException(422, "provider 仅支持 tempo 或 jaeger")
    base_url = (payload.base_url or "").strip().rstrip("/")
    if payload.enabled and not base_url:
        raise HTTPException(422, "启用 Trace 前必须填写 Base URL")
    row = await session.get(TraceSettings, 1)
    if row is None:
        row = TraceSettings(id=1, provider=provider, base_url=base_url or None, enabled=payload.enabled, service_tag=payload.service_tag.strip() or "service.name")
        session.add(row)
    else:
        row.provider = provider
        row.base_url = base_url or None
        row.enabled = payload.enabled
        row.service_tag = payload.service_tag.strip() or "service.name"
    await record_audit(
        session,
        principal=request.state.principal,
        action="trace_settings_updated",
        resource_type="settings",
        resource_id="traces",
        details={"provider": row.provider, "base_url": row.base_url, "enabled": row.enabled, "service_tag": row.service_tag},
        request=request,
    )
    await session.commit()
    await session.refresh(row)
    return trace_settings_payload(row)


@app.post("/api/v1/settings/traces/test")
async def test_trace_settings(
    payload: TraceSettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    provider = payload.provider.strip().lower()
    base_url = (payload.base_url or "").strip().rstrip("/")
    if provider not in {"tempo", "jaeger"} or not base_url:
        raise HTTPException(422, "请填写有效的 Tempo/Jaeger 配置")
    started = time.perf_counter()
    status = "failed"
    detail = ""
    try:
        path = "/api/search" if provider == "tempo" else "/api/services"
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0)) as client:
            response = await client.get(base_url + path, params={"limit": 1} if provider == "tempo" else None)
            response.raise_for_status()
        status = "success"
        detail = "连接成功"
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:1000]
    latency_ms = int((time.perf_counter() - started) * 1000)
    row = await session.get(TraceSettings, 1)
    if row is None:
        row = TraceSettings(id=1, provider=provider, base_url=base_url, enabled=payload.enabled, service_tag=payload.service_tag.strip() or "service.name")
        session.add(row)
    row.last_tested_at = now()
    row.last_test_status = status
    row.last_test_message = f"{detail}，耗时 {latency_ms} ms"
    await session.commit()
    if status != "success":
        raise HTTPException(502, detail)
    return {"ok": True, "message": detail, "latency_ms": latency_ms}


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
        annotations = dict(alert.get("annotations") or {})
        if alert.get("generatorURL"):
            annotations["_generator_url"] = str(alert.get("generatorURL"))[:4000]
        fingerprint = alert.get("fingerprint") or stable_hash(labels)[:64]
        starts_at = parse_time(alert.get("startsAt"))
        status = str(alert.get("status") or payload.get("status") or "firing")
        instance = await session.scalar(
            select(AlertInstance).where(
                AlertInstance.fingerprint == fingerprint,
                AlertInstance.starts_at == starts_at,
            )
        )
        resolved_at = parse_time(alert.get("endsAt")) if status == "resolved" else None
        if status == "resolved":
            # Alertmanager/Prometheus can restart an alert lifecycle with the same
            # fingerprint but a different startsAt. Close every stale firing row for
            # this fingerprint so an old lifecycle cannot keep an incident open forever.
            stale_firing = (
                await session.scalars(
                    select(AlertInstance)
                    .where(
                        AlertInstance.fingerprint == fingerprint,
                        AlertInstance.status == "firing",
                    )
                    .order_by(AlertInstance.starts_at.desc())
                )
            ).all()
            for stale in stale_firing:
                stale.status = "resolved"
                stale.ends_at = resolved_at
                stale.labels = labels
                stale.annotations = annotations
                stale.last_seen_at = now()
            if stale_firing:
                stale_incident_ids = (
                    await session.scalars(
                        select(IncidentAlert.incident_id).where(
                            IncidentAlert.alert_instance_id.in_([row.id for row in stale_firing])
                        )
                    )
                ).all()
                incident_ids.update(stale_incident_ids)
                if instance is None:
                    instance = stale_firing[0]
        if instance is None:
            instance = AlertInstance(
                fingerprint=fingerprint,
                status=status,
                alertname=str(labels.get("alertname") or "UnknownAlert"),
                severity=str(labels.get("severity") or "warning"),
                labels=labels,
                annotations=annotations,
                starts_at=starts_at,
                ends_at=resolved_at,
                last_seen_at=now(),
            )
            session.add(instance)
            await session.flush()
        else:
            instance.status = status
            instance.labels = labels
            instance.annotations = annotations
            instance.last_seen_at = now()
            if resolved_at is not None:
                instance.ends_at = resolved_at

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
    include_test: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    since = now() - timedelta(hours=24)
    all_incidents = (
        await session.scalars(select(Incident).order_by(Incident.last_seen_at.desc()))
    ).all()
    test_count = sum(1 for row in all_incidents if incident_is_test(row))
    incidents = all_incidents if include_test else [row for row in all_incidents if not incident_is_test(row)]
    incident_ids = {row.id for row in incidents}
    analyses = (
        await session.scalars(select(AnalysisRun).order_by(AnalysisRun.created_at.desc()))
    ).all()
    analyses = [row for row in analyses if row.incident_id in incident_ids]
    jobs = (
        await session.scalars(select(OutboxJob).order_by(OutboxJob.created_at.desc()))
    ).all()
    jobs = [row for row in jobs if int((row.payload or {}).get("incident_id") or 0) in incident_ids]
    all_changes = (await session.scalars(select(ChangeEvent).where(ChangeEvent.occurred_at >= since))).all()
    changes = all_changes if include_test else [row for row in all_changes if not row.is_test]
    recent = incidents[:8]
    model_row = await session.get(ModelSettings, 1)
    model_public = public_model_settings(model_row)
    probes = await asyncio.gather(
        probe_http("Prometheus", f"{settings.prometheus_url.rstrip('/')}/-/ready"),
        probe_http("Loki", f"{settings.loki_url.rstrip('/')}/ready"),
        probe_http("Alertmanager", f"{settings.alertmanager_url.rstrip('/')}/-/ready"),
    )
    probes.append({
        "name": "AI 模型",
        "status": "healthy" if model_public.get("enabled") and model_public.get("api_key_configured") and model_public.get("last_test_status") == "success" else "warning",
        "latency_ms": None,
        "message": model_public.get("last_test_message") or "尚未完成连接测试",
    })
    analysis_success = sum(1 for row in analyses if row.status == "succeeded")
    users = (await session.scalars(select(User))).all()
    latest_release = await session.scalar(select(ReleaseNote).where(ReleaseNote.is_current.is_(True)).order_by(ReleaseNote.released_at.desc()))
    return {
        "open_incidents": sum(1 for row in incidents if row.status == "open"),
        "critical_open": sum(1 for row in incidents if row.status == "open" and row.severity == "critical"),
        "warning_open": sum(1 for row in incidents if row.status == "open" and row.severity == "warning"),
        "incidents_24h": sum(1 for row in incidents if row.first_seen_at >= since),
        "analysis_success_rate": round(analysis_success * 100 / len(analyses), 1) if analyses else 0,
        "analysis_total": len(analyses),
        "pending_jobs": sum(1 for row in jobs if row.status in ("pending", "retry", "processing")),
        "failed_jobs": sum(1 for row in jobs if row.status == "dead"),
        "changes_24h": len(changes),
        "hidden_test_change_count": 0 if include_test else sum(1 for row in all_changes if row.is_test),
        "users_total": len(users),
        "users_active": sum(1 for row in users if row.is_active),
        "current_release": ({
            "version": latest_release.version,
            "title": latest_release.title,
            "released_at": latest_release.released_at,
            "commit_sha": latest_release.commit_sha,
        } if latest_release else None),
        "recent_incidents": [incident_payload(row) for row in recent],
        "hidden_test_count": 0 if include_test else test_count,
        "include_test": include_test,
        "data_sources": probes,
        "model": model_public,
        "generated_at": now(),
    }


@app.get("/api/v1/dashboard/trend")
async def dashboard_trend(
    hours: int = 24,
    include_test: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    hours = max(6, min(hours, 168))
    end = now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = end - timedelta(hours=hours)
    all_rows = (
        await session.scalars(select(Incident).where(Incident.first_seen_at >= start).order_by(Incident.first_seen_at))
    ).all()
    hidden_test_count = sum(1 for row in all_rows if incident_is_test(row))
    rows = all_rows if include_test else [row for row in all_rows if not incident_is_test(row)]
    buckets = []
    cursor = start
    while cursor < end:
        bucket_rows = [row for row in rows if cursor <= row.first_seen_at < cursor + timedelta(hours=1)]
        buckets.append({
            "time": cursor, "total": len(bucket_rows),
            "critical": sum(1 for row in bucket_rows if row.severity == "critical"),
            "warning": sum(1 for row in bucket_rows if row.severity == "warning"),
            "info": sum(1 for row in bucket_rows if row.severity == "info"),
        })
        cursor += timedelta(hours=1)
    severity = {level: sum(1 for row in rows if row.severity == level) for level in ("critical", "warning", "info")}
    service_counts: dict[str, int] = {}
    for row in rows:
        service = str((row.labels or {}).get("service") or "unknown")
        service_counts[service] = service_counts.get(service, 0) + 1
    top_services = [{"service": service, "count": count} for service, count in sorted(service_counts.items(), key=lambda item: item[1], reverse=True)[:8]]
    return {"hours": hours, "buckets": buckets, "severity": severity, "top_services": top_services, "hidden_test_count": 0 if include_test else hidden_test_count, "include_test": include_test}


@app.get("/api/v1/alerts")
async def list_alerts(
    status: str | None = None,
    severity: str | None = None,
    namespace: str | None = None,
    q: str | None = None,
    include_test: bool = False,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if status: filters.append(AlertInstance.status == status)
    if severity: filters.append(AlertInstance.severity == severity)
    if namespace: filters.append(AlertInstance.labels["namespace"].astext == namespace)
    if q:
        keyword = f"%{q.strip()}%"
        filters.append(or_(AlertInstance.alertname.ilike(keyword), AlertInstance.fingerprint.ilike(keyword)))
    rows = (await session.scalars(select(AlertInstance).where(*filters).order_by(AlertInstance.last_seen_at.desc()).limit(1000))).all()
    payloads = [alert_payload(row) for row in rows]
    hidden = sum(1 for item in payloads if item["is_test"])
    if not include_test: payloads = [item for item in payloads if not item["is_test"]]
    limit = max(1, min(limit, 500))
    return {"items": payloads[:limit], "total": len(payloads), "hidden_test_count": 0 if include_test else hidden, "include_test": include_test}


@app.get("/api/v1/webhook-deliveries")
async def list_webhook_deliveries(
    status: str | None = None,
    include_test: bool = False,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = [WebhookDelivery.status == status] if status else []
    rows = (await session.scalars(select(WebhookDelivery).where(*filters).order_by(WebhookDelivery.received_at.desc()).limit(1000))).all()
    payloads = [delivery_payload(row) for row in rows]
    hidden = sum(1 for item in payloads if item["is_test"])
    if not include_test: payloads = [item for item in payloads if not item["is_test"]]
    limit = max(1, min(limit, 500))
    return {"items": payloads[:limit], "total": len(payloads), "hidden_test_count": 0 if include_test else hidden, "include_test": include_test}


@app.get("/api/v1/analysis-jobs")
async def list_analysis_jobs(
    status: str | None = None,
    include_test: bool = False,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = [OutboxJob.status == status] if status else []
    rows = (await session.scalars(select(OutboxJob).where(*filters).order_by(OutboxJob.created_at.desc()).limit(1000))).all()
    incident_ids = {int((row.payload or {}).get("incident_id") or 0) for row in rows}
    incident_rows = (await session.scalars(select(Incident).where(Incident.id.in_(incident_ids)))).all() if incident_ids else []
    test_ids = {row.id for row in incident_rows if incident_is_test(row)}
    hidden = sum(1 for row in rows if int((row.payload or {}).get("incident_id") or 0) in test_ids)
    if not include_test:
        rows = [row for row in rows if int((row.payload or {}).get("incident_id") or 0) not in test_ids]
    limit = max(1, min(limit, 500))
    items = [{
        "id": row.id, "job_type": row.job_type, "incident_id": (row.payload or {}).get("incident_id"),
        "status": row.status, "priority": row.priority, "attempts": row.attempts,
        "max_attempts": row.max_attempts, "last_error": row.last_error,
        "created_at": row.created_at, "finished_at": row.finished_at,
    } for row in rows[:limit]]
    return {"items": items, "total": len(rows), "hidden_test_count": 0 if include_test else hidden, "include_test": include_test}


@app.get("/api/v1/incidents")
async def list_incidents(
    status: str | None = None,
    severity: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    service: str | None = None,
    environment: str | None = None,
    q: str | None = None,
    include_test: bool = False,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if status: filters.append(Incident.status == status)
    if severity: filters.append(Incident.severity == severity)
    if cluster: filters.append(Incident.labels["cluster"].astext == cluster)
    if namespace: filters.append(Incident.labels["namespace"].astext == namespace)
    if service: filters.append(Incident.labels["service"].astext == service)
    if environment: filters.append(Incident.labels["environment"].astext == environment)
    if q:
        keyword = f"%{q.strip()}%"
        filters.append(or_(Incident.title.ilike(keyword), Incident.grouping_key.ilike(keyword)))
    rows = (await session.scalars(select(Incident).where(*filters).order_by(Incident.last_seen_at.desc()).limit(2000))).all()
    hidden = sum(1 for row in rows if incident_is_test(row))
    if not include_test: rows = [row for row in rows if not incident_is_test(row)]
    limit = max(1, min(limit, 500)); offset = max(0, offset)
    page = rows[offset:offset + limit]
    return {"items": [incident_payload(row) for row in page], "total": len(rows), "hidden_test_count": 0 if include_test else hidden, "include_test": include_test}


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
    scope_namespace = str((incident.labels or {}).get("namespace") or "")
    scope_service = str((incident.labels or {}).get("service") or "")
    change_start = incident.first_seen_at - timedelta(minutes=settings.change_lookback_minutes)
    change_end = (incident.resolved_at or incident.last_seen_at or now()) + timedelta(minutes=30)
    change_filters = [ChangeEvent.occurred_at >= change_start, ChangeEvent.occurred_at <= change_end]
    if scope_namespace:
        change_filters.append(ChangeEvent.namespace == scope_namespace)
    if scope_service:
        change_filters.append(ChangeEvent.service == scope_service)
    changes = (await session.scalars(select(ChangeEvent).where(*change_filters).order_by(ChangeEvent.occurred_at.desc()).limit(100))).all()
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
        "change_events": [change_event_payload(row) for row in changes],
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
    request: Request,
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
    await session.flush()
    await record_audit(
        session,
        principal=request.state.principal,
        action="incident_reanalysis_requested",
        resource_type="incident",
        resource_id=str(incident_id),
        details={"job_id": job.id},
        request=request,
    )
    await session.commit()
    return {"accepted": True, "job_id": job.id, "incident_id": incident_id}
