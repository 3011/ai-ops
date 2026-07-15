from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.investigation.budget import BudgetLedger
from app.investigation.catalog import build_default_registry
from app.investigation.contracts import DiagnosisResultContract, InvestigationBudget
from app.investigation.enums import InvestigationStatus, StopReason, ToolStatus
from app.investigation.resolver import resolve_target_context
from app.investigation.tool_runtime import ToolRuntime
from app.models import (
    AlertInstance,
    Incident,
    IncidentAlert,
    InvestigationAnalysisRun,
    InvestigationDiagnosisResult,
)

ENGINE = "deterministic_oom_v1"
ENGINE_VERSION = "1.1.0"
_OOM_TOKEN = re.compile(r"\b(?:oomkilled|oom[\s_-]?kill(?:ed)?|out[\s_-]+of[\s_-]+memory)\b", re.IGNORECASE)
_OOM_ALERTNAMES = {
    "containeroomkilled",
    "kubecontaineroomkilled",
    "aiopsoomkilledscenario",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _snapshot_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def is_oom_candidate(incident: Incident, alerts: list[AlertInstance]) -> bool:
    for alert in alerts:
        alertname = str(alert.alertname or "").replace("_", "").replace("-", "").lower()
        if alertname in _OOM_ALERTNAMES:
            return True
        reason = str((alert.labels or {}).get("reason") or "")
        if reason == "OOMKilled":
            return True
    values = [incident.title]
    for alert in alerts:
        values.extend([
            str((alert.annotations or {}).get("summary") or ""),
            str((alert.annotations or {}).get("description") or ""),
        ])
    return bool(_OOM_TOKEN.search(" ".join(values)))


def _diagnosis_for_unresolved(status: ToolStatus, message: str) -> DiagnosisResultContract:
    reason = {
        ToolStatus.TARGET_UNCERTAIN: "TARGET_UNCERTAIN",
        ToolStatus.NOT_FOUND: "TARGET_NOT_FOUND",
        ToolStatus.DENIED: "KUBERNETES_ACCESS_DENIED",
        ToolStatus.PARTIAL: "KUBERNETES_PARTIAL_RESPONSE",
    }.get(status, "KUBERNETES_API_UNAVAILABLE")
    return DiagnosisResultContract(
        summary="未生成 OOMKilled 确定性事实。",
        missing_evidence=[message],
        recommended_checks=["确认告警包含 namespace、pod、uid 和 container 标签。"],
        risk_notes=["目标未可靠固定时，禁止输出对象级确认事实。"],
        degradation_reasons=[reason, "AGENT_NOT_ENABLED"],
    )


def _stop_reason_for_resolution(status: ToolStatus) -> StopReason:
    if status == ToolStatus.TARGET_UNCERTAIN or status == ToolStatus.NOT_FOUND:
        return StopReason.TARGET_UNCERTAIN
    if status == ToolStatus.DENIED:
        return StopReason.VALIDATION_FAILED
    return StopReason.TOOL_UNAVAILABLE


def _stop_reason_for_tool(error_code: str | None) -> StopReason:
    if error_code == "DEADLINE_EXCEEDED":
        return StopReason.DEADLINE_EXCEEDED
    if error_code == "MAX_STEPS_REACHED":
        return StopReason.MAX_STEPS_REACHED
    if error_code == "NO_PROGRESS":
        return StopReason.NO_PROGRESS
    if error_code in {"MAX_TOOL_CALLS_REACHED", "MAX_SAME_TOOL_CALLS_REACHED", "COST_BUDGET_EXHAUSTED"}:
        return StopReason.BUDGET_EXHAUSTED
    return StopReason.AGENT_NOT_ENABLED


async def run_oom_investigation(incident_id: int) -> int | None:
    """Run the no-agent OOMKilled vertical slice through the generic ToolRuntime."""
    async with SessionLocal() as session:
        incident = await session.get(Incident, incident_id)
        if incident is None:
            return None
        alerts = (
            await session.scalars(
                select(AlertInstance)
                .join(IncidentAlert, IncidentAlert.alert_instance_id == AlertInstance.id)
                .where(IncidentAlert.incident_id == incident_id)
                .order_by(AlertInstance.starts_at)
            )
        ).all()
        if not is_oom_candidate(incident, list(alerts)):
            return None

        started_at = utcnow()
        input_snapshot = {
            "incident_id": incident.id,
            "incident_labels": incident.labels or {},
            "first_seen_at": incident.first_seen_at.isoformat(),
            "alerts": [
                {
                    "id": alert.id,
                    "alertname": alert.alertname,
                    "labels": alert.labels or {},
                    "starts_at": alert.starts_at.isoformat(),
                }
                for alert in alerts
            ],
        }
        budget = InvestigationBudget(deadline_at=started_at + timedelta(minutes=2))
        ledger = BudgetLedger(budget)
        run = InvestigationAnalysisRun(
            incident_id=incident_id,
            status=InvestigationStatus.RUNNING.value,
            degradation_reasons=[],
            engine=ENGINE,
            engine_version=ENGINE_VERSION,
            input_snapshot_hash=_snapshot_hash(input_snapshot),
            budget_json=budget.model_dump(mode="json"),
            budget_usage_json=ledger.snapshot().model_dump(mode="json"),
            started_at=started_at,
        )
        session.add(run)
        await session.flush()

        resolution = await resolve_target_context(incident, list(alerts))
        if resolution.context is None:
            run.target_context_json = {
                "resolution_status": resolution.status.value,
                "resolution_message": resolution.message,
                "resolution_details": resolution.details,
            }
            diagnosis = _diagnosis_for_unresolved(resolution.status, resolution.message)
            run.status = InvestigationStatus.COMPLETED_PARTIAL.value
            run.stop_reason = _stop_reason_for_resolution(resolution.status).value
            run.degradation_reasons = diagnosis.degradation_reasons
            run.budget_usage_json = ledger.snapshot().model_dump(mode="json")
            run.completed_at = utcnow()
            session.add(
                InvestigationDiagnosisResult(
                    analysis_run_id=run.id,
                    summary=diagnosis.summary,
                    fact_refs_json=diagnosis.fact_refs,
                    hypotheses_json=diagnosis.hypotheses,
                    missing_evidence_json=diagnosis.missing_evidence,
                    recommended_checks_json=diagnosis.recommended_checks,
                    risk_notes_json=diagnosis.risk_notes,
                    degradation_reasons_json=diagnosis.degradation_reasons,
                    validated_output_json=diagnosis.model_dump(mode="json"),
                )
            )
            await session.commit()
            return run.id

        target = resolution.context
        run.target_context_json = {
            **target.model_dump(mode="json"),
            "resolution_message": resolution.message,
            "resolution_details": resolution.details,
        }
        runtime = ToolRuntime(
            session,
            registry=build_default_registry(),
            budget=ledger,
        )
        tool_result = await runtime.execute(
            analysis_run_id=run.id,
            sequence_number=1,
            target=target,
            tool_name="get_container_termination_status",
            arguments={"scope": "both"},
        )
        fact_refs = list(tool_result.finding_ids)
        missing: list[str] = []
        risk_notes: list[str] = []
        degradation = ["AGENT_NOT_ENABLED"]
        if fact_refs:
            summary = "Kubernetes ContainerStatus 已确认目标容器发生 OOMKilled。"
        else:
            summary = "未生成 OOMKilled 确定性事实。"
            if target.resolution_quality.value != "high":
                missing.append("目标定位质量不是 high，不能生成对象级 confirmed Finding。")
            if tool_result.status == ToolStatus.UNAVAILABLE:
                missing.append("Kubernetes ContainerStatus 数据源不可用。")
                degradation.append("KUBERNETES_API_UNAVAILABLE")
            elif tool_result.status == ToolStatus.DENIED:
                missing.append("Kubernetes ContainerStatus 查询被权限策略拒绝。")
                degradation.append("KUBERNETES_ACCESS_DENIED")
            elif tool_result.status == ToolStatus.TARGET_UNCERTAIN:
                missing.append("工具返回的 Pod UID 或 Container 与 TargetContext 不一致。")
                degradation.append("TARGET_UNCERTAIN")
            elif tool_result.status == ToolStatus.NOT_FOUND:
                missing.append("查询成功但未观察到 terminated 状态；这不等同于确认未发生 OOMKilled。")
            elif tool_result.status == ToolStatus.PARTIAL:
                missing.append("工具仅获得部分结果，不能确认 OOMKilled。")
                degradation.append("TOOL_RESULT_PARTIAL")
            elif tool_result.status == ToolStatus.BUDGET_EXCEEDED:
                missing.append("调查预算在访问数据源前已被 Runtime 拒绝。")
                degradation.append(tool_result.error_code or "BUDGET_EXHAUSTED")
            elif tool_result.status == ToolStatus.INVALID_REQUEST:
                missing.append("工具请求未通过注册或参数校验。")
                degradation.append(tool_result.error_code or "INVALID_REQUEST")
            else:
                missing.append("ContainerStatus 未满足 container_oom_killed_v1 确认规则。")
            risk_notes.append("没有 DeterministicFinding 时，页面不得显示已确认 OOMKilled。")

        diagnosis = DiagnosisResultContract(
            summary=summary,
            fact_refs=fact_refs,
            hypotheses=[],
            missing_evidence=missing,
            recommended_checks=[] if fact_refs else ["核对 Pod UID、Container 和终止时间窗口。"],
            risk_notes=risk_notes,
            degradation_reasons=degradation,
        )
        run.status = InvestigationStatus.COMPLETED_PARTIAL.value
        run.stop_reason = _stop_reason_for_tool(tool_result.error_code).value
        run.degradation_reasons = degradation
        run.budget_usage_json = ledger.snapshot().model_dump(mode="json")
        run.completed_at = utcnow()
        session.add(
            InvestigationDiagnosisResult(
                analysis_run_id=run.id,
                summary=diagnosis.summary,
                fact_refs_json=diagnosis.fact_refs,
                hypotheses_json=diagnosis.hypotheses,
                missing_evidence_json=diagnosis.missing_evidence,
                recommended_checks_json=diagnosis.recommended_checks,
                risk_notes_json=diagnosis.risk_notes,
                degradation_reasons_json=diagnosis.degradation_reasons,
                validated_output_json=diagnosis.model_dump(mode="json"),
            )
        )
        await session.commit()
        return run.id
