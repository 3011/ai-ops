from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.investigation.agents.contracts import (
    AgentDiagnosisOutput,
    AgentToolObservation,
    InvestigationContext,
)
from app.investigation.agents.evaluation import persist_agent_evaluation
from app.investigation.agents.model_runtime import OpenAICompatibleStructuredModel, StructuredModel
from app.investigation.agents.runtime import StructuredInvestigationAgent
from app.investigation.agents.tool_runtimes import LiveAgentToolRuntime, SnapshotAgentToolRuntime
from app.investigation.agents.validator import AgentResultValidator
from app.investigation.budget import BudgetLedger
from app.investigation.catalog import build_default_registry
from app.investigation.contracts import InvestigationBudget, TargetContext
from app.investigation.replay import create_replay_snapshot
from app.investigation.run_input import resolve_run_input
from app.investigation.tool_runtime import stable_hash
from app.model_config import load_runtime_model_config
from app.models import (
    Incident,
    InvestigationAnalysisRun,
    InvestigationDiagnosisResult,
    InvestigationFinding,
    InvestigationReplaySnapshot,
    InvestigationToolExecution,
)

AgentRunMode = Literal["offline_replay", "realtime_shadow"]
AGENT_ENGINE_VERSION = "0.9.0"
settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(UTC)


def _mode(parent: InvestigationAnalysisRun) -> Literal["oom", "cpu"]:
    return "oom" if "oom" in parent.engine else "cpu"


def _target_from_json(value: dict[str, Any] | None) -> TargetContext | None:
    if not value or not value.get("pod_uid") or not value.get("container_name"):
        return None
    allowed = set(TargetContext.model_fields)
    return TargetContext.model_validate({key: item for key, item in value.items() if key in allowed})


def _finding_payload(row: InvestigationFinding) -> dict[str, Any]:
    return {
        "id": row.id,
        "finding_type": row.finding_type,
        "subject": row.subject_ref_json or {},
        "value": row.value_json or {},
        "polarity": row.polarity,
        "quality": row.quality,
        "event_time": row.event_time.isoformat() if row.event_time else None,
        "confirmation_rule": row.confirmation_rule,
    }


def _tool_observation(row: InvestigationToolExecution) -> AgentToolObservation:
    visible = row.model_visible_output_json or {}
    return AgentToolObservation(
        execution_id=str(row.id),
        tool_name=row.tool_name,
        tool_version=row.tool_version,
        status=row.status,
        summary=str(visible.get("summary") or row.error_message or ""),
        finding_ids=[str(item) for item in (visible.get("finding_ids") or [])],
        data=dict(row.structured_output_json or {}),
        error_code=row.error_code,
        cost_units=row.cost_units,
        reused_execution_id=str(row.reused_execution_id) if row.reused_execution_id else None,
    )


async def _latest_snapshot(session: AsyncSession, parent_run_id: int) -> InvestigationReplaySnapshot:
    row = await session.scalar(
        select(InvestigationReplaySnapshot)
        .where(InvestigationReplaySnapshot.analysis_run_id == parent_run_id)
        .order_by(InvestigationReplaySnapshot.created_at.desc(), InvestigationReplaySnapshot.id.desc())
        .limit(1)
    )
    if row is None:
        row = await create_replay_snapshot(session, parent_run_id)
    return row


async def execute_agent_run(
    session: AsyncSession,
    parent_run_id: int,
    *,
    run_mode: AgentRunMode,
    model: StructuredModel | None = None,
) -> int:
    parent = await session.get(InvestigationAnalysisRun, parent_run_id)
    if parent is None:
        raise LookupError(f"analysis run not found: {parent_run_id}")
    if parent.run_kind != "deterministic" and not parent.engine.startswith("deterministic_"):
        raise ValueError("agent parent must be a deterministic run")
    incident = await session.get(Incident, parent.incident_id)
    if incident is None:
        raise LookupError(f"incident not found: {parent.incident_id}")
    source_snapshot = await _latest_snapshot(session, parent.id)
    resolved_input = await resolve_run_input(session, parent, incident)
    mode = _mode(parent)
    started = utcnow()
    budget = InvestigationBudget(
        max_steps=8,
        max_tool_calls=8,
        max_total_cost_units=24,
        max_same_tool_calls=2,
        max_no_progress_rounds=3,
        deadline_at=started + timedelta(minutes=3),
    )
    ledger = BudgetLedger(budget)
    run_kind = "agent_offline" if run_mode == "offline_replay" else "agent_shadow"
    engine = f"agent_{mode}_{'offline_replay_v1' if run_mode == 'offline_replay' else 'shadow_v1'}"
    child = InvestigationAnalysisRun(
        incident_id=parent.incident_id,
        parent_run_id=parent.id,
        run_kind=run_kind,
        source_snapshot_id=source_snapshot.id,
        status="RUNNING",
        stop_reason=None,
        degradation_reasons=[],
        target_context_json=dict(parent.target_context_json or {}),
        engine=engine,
        engine_version=AGENT_ENGINE_VERSION,
        input_snapshot_hash=resolved_input.source_hash,
        run_input_json=resolved_input.payload,
        run_input_schema_version=resolved_input.schema_version,
        run_input_source_hash=resolved_input.source_hash,
        run_input_source_mode="native_frozen",
        budget_json=budget.model_dump(mode="json"),
        budget_usage_json=ledger.snapshot().model_dump(mode="json"),
        started_at=started,
    )
    session.add(child)
    await session.flush()

    parent_findings = list((await session.scalars(
        select(InvestigationFinding)
        .where(InvestigationFinding.analysis_run_id == parent.id)
        .order_by(InvestigationFinding.created_at, InvestigationFinding.id)
    )).all())
    target = _target_from_json(parent.target_context_json)
    registry = build_default_registry()
    if run_mode == "offline_replay":
        tools = SnapshotAgentToolRuntime(
            session,
            analysis_run_id=child.id,
            snapshot=dict(source_snapshot.snapshot_json or {}),
            registry=registry,
        )
    else:
        tools = LiveAgentToolRuntime(
            session,
            analysis_run_id=child.id,
            target=target,
            registry=registry,
            budget=ledger,
        )
    context = InvestigationContext(
        analysis_run_id=child.id,
        parent_run_id=parent.id,
        incident_summary={
            "id": incident.id,
            "title": incident.title,
            "severity": incident.severity,
            "status": incident.status,
            "labels": incident.labels or {},
            "first_seen_at": incident.first_seen_at.isoformat(),
            "last_seen_at": incident.last_seen_at.isoformat(),
        },
        target_context=target,
        initial_finding_ids=[row.id for row in parent_findings],
        initial_findings=[_finding_payload(row) for row in parent_findings],
        available_tools=tools.catalog(),
        budget_snapshot=ledger.snapshot(),
        investigation_mode=mode,
        run_mode=run_mode,
        source_snapshot_id=source_snapshot.id,
        source_snapshot_hash=source_snapshot.snapshot_hash,
    )

    model_error: str | None = None
    try:
        if model is None:
            runtime = await load_runtime_model_config(session)
            model = OpenAICompatibleStructuredModel(
                session, child.id, runtime, timeout_seconds=settings.agent_model_timeout_seconds
            )
        remaining_seconds = max(1.0, (budget.deadline_at - utcnow()).total_seconds())
        async with asyncio.timeout(remaining_seconds):
            diagnosis = await StructuredInvestigationAgent(model).investigate(context, tools, budget)
    except Exception as exc:  # model failure must never modify or fail the parent run
        model_error = f"{type(exc).__name__}: {exc}"[:2000]
        diagnosis = AgentDiagnosisOutput(
            summary="Agent 模型不可用或输出未通过结构化契约；确定性调查结果保持不变。",
            fact_refs=list(context.initial_finding_ids),
            hypotheses=[],
            missing_evidence=["Agent 模型调用失败，详见 ModelInvocation 审计。"],
            recommended_checks=["检查模型配置、调用状态和响应 Schema。"],
            risk_notes=["模型失败不得影响父级确定性 Run。"],
        )

    tool_rows = list((await session.scalars(
        select(InvestigationToolExecution)
        .where(InvestigationToolExecution.analysis_run_id == child.id)
        .order_by(InvestigationToolExecution.sequence_number)
    )).all())
    observations = [_tool_observation(row) for row in tool_rows]
    child_findings = list((await session.scalars(
        select(InvestigationFinding).where(InvestigationFinding.analysis_run_id == child.id)
    )).all())
    allowed_ids = [row.id for row in parent_findings] + [row.id for row in child_findings]
    report = AgentResultValidator().validate(
        diagnosis,
        allowed_finding_ids=allowed_ids,
        observations=observations,
        allowed_tool_names=[item.name for item in tools.catalog()],
        target_resolved=target is not None,
    )
    output = diagnosis.model_dump(mode="json")
    output.update({
        "analysis_mode": engine,
        "parent_run_id": parent.id,
        "run_mode": run_mode,
        "source_snapshot_id": source_snapshot.id,
        "source_snapshot_hash": source_snapshot.snapshot_hash,
        "agent_validation": report.model_dump(mode="json"),
        "model_error": model_error,
    })
    degradation: list[str] = []
    if model_error:
        degradation.append("AGENT_MODEL_UNAVAILABLE")
    if report.status == "INVALID":
        degradation.append("AGENT_OUTPUT_INVALID")
    elif report.status == "VALID_WITH_WARNINGS":
        degradation.append("AGENT_OUTPUT_WARNINGS")
    if run_mode == "offline_replay":
        degradation.append("SNAPSHOT_ONLY")
    child.agent_validation_status = report.status
    child.agent_validation_report_json = report.model_dump(mode="json")
    child.degradation_reasons = degradation
    child.budget_usage_json = ledger.snapshot().model_dump(mode="json")
    child.completed_at = utcnow()
    if model_error:
        child.status = "FAILED"
        child.stop_reason = "TOOL_UNAVAILABLE"
    elif report.status == "INVALID":
        child.status = "INCONCLUSIVE"
        child.stop_reason = "VALIDATION_FAILED"
    else:
        child.status = "COMPLETED" if report.status == "VALID" else "COMPLETED_PARTIAL"
        child.stop_reason = "NO_MORE_USEFUL_TOOLS"
    if report.status != "INVALID":
        session.add(InvestigationDiagnosisResult(
            analysis_run_id=child.id,
            summary=diagnosis.summary,
            fact_refs_json=diagnosis.fact_refs,
            hypotheses_json=[item.model_dump(mode="json") for item in diagnosis.hypotheses],
            missing_evidence_json=diagnosis.missing_evidence,
            recommended_checks_json=diagnosis.recommended_checks,
            risk_notes_json=diagnosis.risk_notes,
            degradation_reasons_json=degradation,
            validated_output_json=output,
        ))
    await session.flush()
    await create_replay_snapshot(session, child.id)
    await persist_agent_evaluation(
        session,
        child.id,
        offline_source_access_count=tools.source_data_access_count,
    )
    await session.flush()
    return child.id


async def run_agent_for_parent(
    parent_run_id: int,
    *,
    run_mode: AgentRunMode,
    model: StructuredModel | None = None,
) -> int:
    async with SessionLocal() as session:
        run_id = await execute_agent_run(session, parent_run_id, run_mode=run_mode, model=model)
        await session.commit()
        return run_id


async def run_agent_shadow(parent_run_id: int) -> int:
    return await run_agent_for_parent(parent_run_id, run_mode="realtime_shadow")


async def run_offline_agent_replay(parent_run_id: int) -> int:
    return await run_agent_for_parent(parent_run_id, run_mode="offline_replay")
