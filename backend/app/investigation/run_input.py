from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.artifacts import redact_sensitive
from app.investigation.tool_runtime import stable_hash
from app.models import AlertInstance, Incident, IncidentAlert, InvestigationAnalysisRun

RUN_INPUT_SCHEMA_VERSION = "1.0.0"
RunInputSourceMode = Literal["native_frozen", "historical_reconstructed", "legacy_incomplete"]


@dataclass(frozen=True)
class ResolvedRunInput:
    payload: dict[str, Any]
    schema_version: str
    source_hash: str
    compatibility_hash: str
    source_mode: RunInputSourceMode
    expected_hash: str | None


def _alert_identity(alert: AlertInstance) -> dict[str, Any]:
    return {
        "id": alert.id,
        "alertname": alert.alertname,
        "labels": alert.labels or {},
        "starts_at": alert.starts_at.isoformat(),
    }


def build_native_run_input(
    incident: Incident,
    alerts: list[AlertInstance],
    *,
    candidate_mode: str,
    tool_catalog_version: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": RUN_INPUT_SCHEMA_VERSION,
        "candidate_mode": candidate_mode,
        "tool_catalog_version": tool_catalog_version,
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "status": incident.status,
            "severity": incident.severity,
            "labels": incident.labels or {},
            "first_seen_at": incident.first_seen_at.isoformat(),
            "last_seen_at": incident.last_seen_at.isoformat(),
        },
        "alerts": [
            {
                **_alert_identity(alert),
                "status": alert.status,
                "severity": alert.severity,
                "annotations": alert.annotations or {},
                "ends_at": alert.ends_at.isoformat() if alert.ends_at else None,
            }
            for alert in alerts
        ],
        "target_resolution_input": {
            "incident_labels": incident.labels or {},
            "alert_labels": [alert.labels or {} for alert in alerts],
        },
    }
    return redact_sensitive(payload)


def legacy_v08_input(incident: Incident, alerts: list[AlertInstance]) -> dict[str, Any]:
    return {
        "incident_id": incident.id,
        "incident_labels": incident.labels or {},
        "first_seen_at": incident.first_seen_at.isoformat(),
        "alerts": [_alert_identity(alert) for alert in alerts],
    }


def legacy_v09_input(
    incident: Incident,
    alerts: list[AlertInstance],
    run: InvestigationAnalysisRun,
) -> dict[str, Any]:
    return {
        "mode": "oom" if run.engine.startswith("deterministic_oom") else "cpu",
        "tool_catalog_version": run.engine_version,
        "incident_id": incident.id,
        "incident_labels": incident.labels or {},
        "first_seen_at": incident.first_seen_at.isoformat(),
        "alerts": [_alert_identity(alert) for alert in alerts],
    }


def freeze_run_input(
    run: InvestigationAnalysisRun,
    incident: Incident,
    alerts: list[AlertInstance],
    *,
    candidate_mode: str,
    tool_catalog_version: str,
) -> dict[str, Any]:
    payload = build_native_run_input(
        incident,
        alerts,
        candidate_mode=candidate_mode,
        tool_catalog_version=tool_catalog_version,
    )
    source_hash = stable_hash(payload)
    run.run_input_json = payload
    run.run_input_schema_version = RUN_INPUT_SCHEMA_VERSION
    run.run_input_source_hash = source_hash
    run.run_input_source_mode = "native_frozen"
    run.input_snapshot_hash = source_hash
    return payload


async def _historical_alerts(
    session: AsyncSession,
    run: InvestigationAnalysisRun,
) -> list[AlertInstance]:
    # IncidentAlert has no historical created_at in legacy schemas. starts_at is the
    # strongest immutable cutoff available and prevents later lifecycle alerts from
    # entering an earlier Run in normal Alertmanager flows.
    return list((await session.scalars(
        select(AlertInstance)
        .join(IncidentAlert, IncidentAlert.alert_instance_id == AlertInstance.id)
        .where(
            IncidentAlert.incident_id == run.incident_id,
            AlertInstance.starts_at <= run.started_at,
        )
        .order_by(AlertInstance.starts_at, AlertInstance.id)
    )).all())


async def resolve_run_input(
    session: AsyncSession,
    run: InvestigationAnalysisRun,
    incident: Incident,
) -> ResolvedRunInput:
    if run.run_input_json is not None:
        payload = dict(run.run_input_json)
        source_hash = run.run_input_source_hash or stable_hash(payload)
        return ResolvedRunInput(
            payload=payload,
            schema_version=run.run_input_schema_version or RUN_INPUT_SCHEMA_VERSION,
            source_hash=source_hash,
            compatibility_hash=source_hash,
            source_mode=(run.run_input_source_mode or "native_frozen"),
            expected_hash=run.run_input_source_hash or run.input_snapshot_hash,
        )

    alerts = await _historical_alerts(session, run)
    candidates: list[tuple[str, dict[str, Any]]] = []
    if run.engine_version in {"1.0.0", "1.1.0", "1"}:
        candidates.append(("legacy-v0.8", legacy_v08_input(incident, alerts)))
        candidates.append(("legacy-v0.9", legacy_v09_input(incident, alerts, run)))
    else:
        candidates.append(("legacy-v0.9", legacy_v09_input(incident, alerts, run)))
        candidates.append(("legacy-v0.8", legacy_v08_input(incident, alerts)))

    expected = run.input_snapshot_hash
    for schema_version, payload in candidates:
        source_hash = stable_hash(payload)
        if expected and source_hash == expected:
            sanitized = redact_sensitive(payload)
            return ResolvedRunInput(
                payload=sanitized,
                schema_version=schema_version,
                source_hash=stable_hash(sanitized),
                compatibility_hash=source_hash,
                source_mode="historical_reconstructed",
                expected_hash=expected,
            )

    schema_version, payload = candidates[0]
    payload = redact_sensitive(payload)
    source_hash = stable_hash(payload)
    return ResolvedRunInput(
        payload=payload,
        schema_version=schema_version,
        source_hash=source_hash,
        compatibility_hash=source_hash,
        source_mode="legacy_incomplete",
        expected_hash=expected,
    )


def incident_context_from_run_input(run_input: dict[str, Any]) -> dict[str, Any]:
    if isinstance(run_input.get("incident"), dict):
        return {
            **dict(run_input["incident"]),
            "alerts": list(run_input.get("alerts") or []),
        }
    return {
        "id": run_input.get("incident_id"),
        "labels": run_input.get("incident_labels") or {},
        "first_seen_at": run_input.get("first_seen_at"),
        "alerts": list(run_input.get("alerts") or []),
        "legacy_context_limited": True,
    }
