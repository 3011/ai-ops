from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any, Literal

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.investigation.agents.service import run_agent_shadow
from app.investigation.budget import BudgetLedger
from app.investigation.catalog import TOOL_CATALOG_VERSION, build_default_registry
from app.investigation.contracts import DiagnosisResultContract, InvestigationBudget, ToolResult
from app.investigation.enums import InvestigationStatus, StopReason, ToolStatus
from app.investigation.resolver import resolve_target_context
from app.investigation.run_input import freeze_run_input
from app.investigation.replay import create_replay_snapshot
from app.investigation.tool_runtime import ToolRuntime
from app.models import (
    AlertInstance,
    Incident,
    IncidentAlert,
    InvestigationAnalysisRun,
    InvestigationDiagnosisResult,
    InvestigationFinding,
)

OOM_ENGINE = "deterministic_oom_v2"
CPU_ENGINE = "deterministic_cpu_v1"
ENGINE_VERSION = "0.9.0"
_OOM_TOKEN = re.compile(r"\b(?:oomkilled|oom[\s_-]?kill(?:ed)?|out[\s_-]+of[\s_-]+memory)\b", re.IGNORECASE)
_OOM_ALERTNAMES = {
    "containeroomkilled",
    "kubecontaineroomkilled",
    "aiopsoomkilledscenario",
}
_CPU_TOKEN = re.compile(
    r"\b(?:high[\s_-]+cpu|cpu[\s_-]+(?:spike|usage|saturation|throttling|high)|cpu[\s_-]*pressure)\b",
    re.IGNORECASE,
)
settings = get_settings()
logger = logging.getLogger("aiops.investigation")

_CPU_ALERTNAMES = {
    "highcpu",
    "containerhighcpu",
    "containercpuusagehigh",
    "containercpuspike",
    "cputhrottlinghigh",
    "aiopscpuspikescenario",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _normalized_alertname(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def is_oom_candidate(incident: Incident, alerts: list[AlertInstance]) -> bool:
    for alert in alerts:
        if _normalized_alertname(str(alert.alertname or "")) in _OOM_ALERTNAMES:
            return True
        if str((alert.labels or {}).get("reason") or "") == "OOMKilled":
            return True
    values = [incident.title]
    for alert in alerts:
        values.extend([
            str((alert.annotations or {}).get("summary") or ""),
            str((alert.annotations or {}).get("description") or ""),
        ])
    return bool(_OOM_TOKEN.search(" ".join(values)))


def is_cpu_candidate(incident: Incident, alerts: list[AlertInstance]) -> bool:
    for alert in alerts:
        normalized = _normalized_alertname(str(alert.alertname or ""))
        if normalized in _CPU_ALERTNAMES:
            return True
        if "cpu" in normalized and any(token in normalized for token in ("high", "spike", "usage", "thrott", "saturat")):
            return True
    values = [incident.title]
    for alert in alerts:
        values.extend([
            str((alert.annotations or {}).get("summary") or ""),
            str((alert.annotations or {}).get("description") or ""),
        ])
    return bool(_CPU_TOKEN.search(" ".join(values)))


def _diagnosis_for_unresolved(status: ToolStatus, message: str, *, mode: Literal["oom", "cpu"]) -> DiagnosisResultContract:
    reason = {
        ToolStatus.TARGET_UNCERTAIN: "TARGET_UNCERTAIN",
        ToolStatus.NOT_FOUND: "TARGET_NOT_FOUND",
        ToolStatus.DENIED: "KUBERNETES_ACCESS_DENIED",
        ToolStatus.PARTIAL: "KUBERNETES_PARTIAL_RESPONSE",
    }.get(status, "KUBERNETES_API_UNAVAILABLE")
    label = "OOMKilled" if mode == "oom" else "CPU Spike"
    return DiagnosisResultContract(
        summary=f"未生成 {label} 确定性事实。",
        missing_evidence=[message],
        recommended_checks=["确认告警包含 namespace、pod、uid 和 container 标签。"],
        risk_notes=["目标未可靠固定时，禁止输出对象级确认事实。"],
        degradation_reasons=[reason],
        analysis_mode="deterministic_oom_v2" if mode == "oom" else "deterministic_cpu_v1",
    )


def _stop_reason_for_resolution(status: ToolStatus) -> StopReason:
    if status in {ToolStatus.TARGET_UNCERTAIN, ToolStatus.NOT_FOUND}:
        return StopReason.TARGET_UNCERTAIN
    if status == ToolStatus.DENIED:
        return StopReason.VALIDATION_FAILED
    return StopReason.TOOL_UNAVAILABLE


def _stop_reason_for_results(results: list[ToolResult]) -> StopReason:
    for result in results:
        if result.error_code == "DEADLINE_EXCEEDED":
            return StopReason.DEADLINE_EXCEEDED
        if result.error_code == "MAX_STEPS_REACHED":
            return StopReason.MAX_STEPS_REACHED
        if result.error_code == "NO_PROGRESS":
            return StopReason.NO_PROGRESS
        if result.error_code in {"MAX_TOOL_CALLS_REACHED", "MAX_SAME_TOOL_CALLS_REACHED", "COST_BUDGET_EXHAUSTED"}:
            return StopReason.BUDGET_EXHAUSTED
    return StopReason.NO_MORE_USEFUL_TOOLS


def _tool_degradation(result: ToolResult) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    degradation: list[str] = []
    label = result.tool_name
    if result.status == ToolStatus.UNAVAILABLE:
        missing.append(f"{label} 数据源不可用。")
        degradation.append(result.error_code or "TOOL_UNAVAILABLE")
    elif result.status == ToolStatus.DENIED:
        missing.append(f"{label} 被权限策略拒绝。")
        degradation.append(result.error_code or "TOOL_ACCESS_DENIED")
    elif result.status == ToolStatus.TARGET_UNCERTAIN:
        missing.append(f"{label} 返回的对象身份与 TargetContext 不一致。")
        degradation.append(result.error_code or "TARGET_UNCERTAIN")
    elif result.status == ToolStatus.NOT_FOUND:
        missing.append(f"{label} 查询成功但没有样本；不能据此确认历史上不存在异常。")
    elif result.status == ToolStatus.PARTIAL:
        missing.append(f"{label} 只获得部分数据，Finding 质量已相应降低。")
        degradation.append(result.error_code or "TOOL_RESULT_PARTIAL")
    elif result.status == ToolStatus.BUDGET_EXCEEDED:
        missing.append(f"{label} 在访问数据源前被预算拒绝。")
        degradation.append(result.error_code or "BUDGET_EXHAUSTED")
    elif result.status == ToolStatus.INVALID_REQUEST:
        missing.append(f"{label} 请求未通过注册、作用域或参数校验。")
        degradation.append(result.error_code or "INVALID_REQUEST")
    return missing, degradation


async def _load_incident(session, incident_id: int) -> tuple[Incident | None, list[AlertInstance]]:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        return None, []
    alerts = list(
        (
            await session.scalars(
                select(AlertInstance)
                .join(IncidentAlert, IncidentAlert.alert_instance_id == AlertInstance.id)
                .where(IncidentAlert.incident_id == incident_id)
                .order_by(AlertInstance.starts_at)
            )
        ).all()
    )
    return incident, alerts


async def _finding_types(session, finding_ids: list[str]) -> dict[str, InvestigationFinding]:
    if not finding_ids:
        return {}
    rows = list(
        (
            await session.scalars(
                select(InvestigationFinding).where(InvestigationFinding.id.in_(finding_ids))
            )
        ).all()
    )
    return {row.finding_type: row for row in rows}


async def _run_investigation(incident_id: int, *, mode: Literal["oom", "cpu"]) -> int | None:
    async with SessionLocal() as session:
        incident, alerts = await _load_incident(session, incident_id)
        if incident is None:
            return None
        if mode == "oom" and not is_oom_candidate(incident, alerts):
            return None
        if mode == "cpu" and not is_cpu_candidate(incident, alerts):
            return None

        started_at = utcnow()
        budget = InvestigationBudget(max_steps=10, max_tool_calls=10, max_total_cost_units=30, max_same_tool_calls=3, max_no_progress_rounds=10, deadline_at=started_at + timedelta(minutes=3))
        ledger = BudgetLedger(budget)
        engine = OOM_ENGINE if mode == "oom" else CPU_ENGINE
        run = InvestigationAnalysisRun(
            incident_id=incident_id,
            status=InvestigationStatus.RUNNING.value,
            degradation_reasons=[],
            engine=engine,
            engine_version=ENGINE_VERSION,
            budget_json=budget.model_dump(mode="json"),
            budget_usage_json=ledger.snapshot().model_dump(mode="json"),
            started_at=started_at,
        )
        freeze_run_input(
            run,
            incident,
            alerts,
            candidate_mode=mode,
            tool_catalog_version=TOOL_CATALOG_VERSION,
        )
        session.add(run)
        await session.flush()

        resolution = await resolve_target_context(incident, alerts)
        if resolution.context is None:
            run.target_context_json = {
                "resolution_status": resolution.status.value,
                "resolution_message": resolution.message,
                "resolution_details": resolution.details,
            }
            diagnosis = _diagnosis_for_unresolved(resolution.status, resolution.message, mode=mode)
            run.status = InvestigationStatus.COMPLETED_PARTIAL.value
            run.stop_reason = _stop_reason_for_resolution(resolution.status).value
            run.degradation_reasons = diagnosis.degradation_reasons
            run.budget_usage_json = ledger.snapshot().model_dump(mode="json")
            run.completed_at = utcnow()
            session.add(InvestigationDiagnosisResult(
                analysis_run_id=run.id,
                summary=diagnosis.summary,
                fact_refs_json=diagnosis.fact_refs,
                hypotheses_json=diagnosis.hypotheses,
                missing_evidence_json=diagnosis.missing_evidence,
                recommended_checks_json=diagnosis.recommended_checks,
                risk_notes_json=diagnosis.risk_notes,
                degradation_reasons_json=diagnosis.degradation_reasons,
                validated_output_json=diagnosis.model_dump(mode="json"),
            ))
            await session.flush()
            await create_replay_snapshot(session, run.id)
            await session.commit()
            return run.id

        target = resolution.context
        run.target_context_json = {
            **target.model_dump(mode="json"),
            "resolution_message": resolution.message,
            "resolution_details": resolution.details,
        }
        registry = build_default_registry()
        runtime = ToolRuntime(session, registry=registry, budget=ledger)
        plan: list[tuple[str, dict[str, Any]]]
        if mode == "oom":
            plan = [
                ("get_container_termination_status", {"scope": "both"}),
                ("get_memory_usage_vs_limit", {"step_seconds": 15}),
                ("get_container_restart_history", {"window_minutes": 30, "step_seconds": 15}),
                ("get_recent_rollouts", {"lookback_minutes": 120}),
                ("search_container_logs", {
                    "target": "both",
                    "categories": ["oom", "allocation_failure", "process_termination", "runtime_error"],
                    "relative_window_minutes": 30,
                }),
            ]
        else:
            plan = [
                ("get_cpu_usage_vs_request_limit", {
                    "baseline_minutes": 30,
                    "spike_window_minutes": 10,
                    "step_seconds": 15,
                }),
                ("get_cpu_throttling", {"window_minutes": 15, "step_seconds": 15}),
                ("get_container_restart_history", {"window_minutes": 30, "step_seconds": 15}),
                ("get_recent_rollouts", {"lookback_minutes": 120}),
                ("search_container_logs", {
                    "target": "both",
                    "categories": ["runtime_error", "cpu_hot_loop_hint", "gc_pressure", "request_timeout"],
                    "relative_window_minutes": 30,
                }),
                ("compare_cpu_across_replicas", {"window_minutes": 15, "step_seconds": 15, "max_replicas": 12}),
                ("get_application_red_metrics", {
                    "signals": ["request_rate", "error_rate", "latency"],
                    "route_scope": "all",
                    "baseline_minutes": 30,
                    "incident_minutes": 10,
                    "step_seconds": 30,
                }),
            ]

        results: list[ToolResult] = []
        for sequence, (tool_name, arguments) in enumerate(plan, start=1):
            result = await runtime.execute(
                analysis_run_id=run.id,
                sequence_number=sequence,
                target=target,
                tool_name=tool_name,
                arguments=arguments,
            )
            results.append(result)
            if result.status == ToolStatus.BUDGET_EXCEEDED:
                break

        fact_refs = list(dict.fromkeys(finding_id for result in results for finding_id in result.finding_ids))
        findings = await _finding_types(session, fact_refs)
        missing: list[str] = []
        degradation: list[str] = []
        for result in results:
            tool_missing, tool_degradation = _tool_degradation(result)
            missing.extend(tool_missing)
            degradation.extend(tool_degradation)
        degradation = list(dict.fromkeys(degradation))
        risk_notes: list[str] = []

        if mode == "oom":
            oom_confirmed = "container_oom_killed" in findings
            memory_related = [name for name in ("memory_limit_reached", "memory_near_limit", "memory_usage_increased") if name in findings]
            if oom_confirmed:
                summary = "Kubernetes ContainerStatus 已确认目标容器发生 OOMKilled。"
                if memory_related:
                    summary += " Prometheus 同时观察到退出前内存压力证据。"
                else:
                    summary += " Prometheus 未形成接近 limit 的确定性 Finding，这不能反证 OOMKilled。"
            else:
                summary = "未生成 OOMKilled 确定性事实。"
                risk_notes.append("没有 container_oom_killed Finding 时，页面不得显示已确认 OOMKilled。")
            if any(result.tool_name == "get_memory_usage_vs_limit" for result in results):
                risk_notes.append("Prometheus 抓取间隔可能错过瞬时峰值；内存未达到 limit 不能作为 OOMKilled 反证。")
            supplementary = [name for name in ("container_repeatedly_restarted", "container_restart_increased", "rollout_preceded_incident", "runtime_error_log_observed", "oom_log_observed") if name in findings]
            if supplementary:
                summary += f" 另观察到 {len(supplementary)} 类重启、发布或日志旁证。"
            diagnosis_mode = "deterministic_oom_v2"
        else:
            cpu_spike = "container_cpu_spike" in findings
            throttling = "cpu_throttling_sustained" in findings or "cpu_throttling_observed" in findings
            if cpu_spike:
                summary = "Prometheus 已按历史基线、绝对增量和持续时间确认目标容器发生 CPU Spike。"
                if throttling:
                    summary += " 同一窗口观察到 CPU throttling。"
            else:
                summary = "未生成 CPU Spike 确定性事实。"
                risk_notes.append("未满足 container_cpu_spike_v1 时，不得仅凭单点高 CPU 声称 CPU Spike。")
            supplemental = [name for name in (
                "single_replica_cpu_anomaly", "subset_replicas_cpu_anomaly", "all_replicas_cpu_increased",
                "new_revision_cpu_higher", "request_rate_increased", "request_rate_stable",
                "error_rate_increased", "latency_increased", "rollout_preceded_incident",
            ) if name in findings]
            if supplemental:
                summary += f" 另形成 {len(supplemental)} 条副本、应用信号或发布相关事实。"
            if any(result.tool_name == "get_application_red_metrics" and result.error_code == "APPLICATION_METRICS_UNAVAILABLE" for result in results):
                missing.append("应用 RED 指标未匹配到内置 Profile；这是能力缺口，不是应用信号正常。")
            risk_notes.append("没有 Profile 数据，不能确认具体函数、线程或代码路径导致 CPU 升高。")
            diagnosis_mode = "deterministic_cpu_v1"

        diagnosis = DiagnosisResultContract(
            summary=summary,
            fact_refs=fact_refs,
            hypotheses=[],
            missing_evidence=list(dict.fromkeys(missing)),
            recommended_checks=[] if fact_refs else ["核对目标 UID、指标采样覆盖和告警时间窗口。"],
            risk_notes=list(dict.fromkeys(risk_notes)),
            degradation_reasons=degradation,
            analysis_mode=diagnosis_mode,
        )
        run.status = InvestigationStatus.COMPLETED_PARTIAL.value
        run.stop_reason = _stop_reason_for_results(results).value
        run.degradation_reasons = degradation
        run.budget_usage_json = ledger.snapshot().model_dump(mode="json")
        run.completed_at = utcnow()
        session.add(InvestigationDiagnosisResult(
            analysis_run_id=run.id,
            summary=diagnosis.summary,
            fact_refs_json=diagnosis.fact_refs,
            hypotheses_json=diagnosis.hypotheses,
            missing_evidence_json=diagnosis.missing_evidence,
            recommended_checks_json=diagnosis.recommended_checks,
            risk_notes_json=diagnosis.risk_notes,
            degradation_reasons_json=diagnosis.degradation_reasons,
            validated_output_json=diagnosis.model_dump(mode="json"),
        ))
        await session.flush()
        await create_replay_snapshot(session, run.id)
        await session.commit()
        return run.id


async def run_oom_investigation(incident_id: int) -> int | None:
    return await _run_investigation(incident_id, mode="oom")


async def run_cpu_investigation(incident_id: int) -> int | None:
    return await _run_investigation(incident_id, mode="cpu")


async def run_trusted_investigations(incident_id: int) -> list[int]:
    run_ids: list[int] = []
    for runner in (run_oom_investigation, run_cpu_investigation):
        run_id = await runner(incident_id)
        if run_id is not None:
            run_ids.append(run_id)

    if settings.investigation_mode.casefold() == "shadow":
        for parent_run_id in run_ids:
            try:
                agent_run_id = await run_agent_shadow(parent_run_id)
                logger.info(
                    "agent shadow completed parent_run=%s agent_run=%s incident=%s",
                    parent_run_id, agent_run_id, incident_id,
                )
            except Exception as exc:  # shadow failure must not alter deterministic completion
                logger.exception(
                    "agent shadow failed parent_run=%s incident=%s: %s",
                    parent_run_id, incident_id, exc,
                )
    return run_ids
