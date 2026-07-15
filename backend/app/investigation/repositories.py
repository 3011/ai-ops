from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InvestigationAnalysisRun,
    InvestigationDiagnosisResult,
    InvestigationFinding,
    InvestigationToolExecution,
)


async def investigation_payloads(
    session: AsyncSession,
    incident_id: int,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    runs = (
        await session.scalars(
            select(InvestigationAnalysisRun)
            .where(InvestigationAnalysisRun.incident_id == incident_id)
            .order_by(InvestigationAnalysisRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    payloads: list[dict[str, Any]] = []
    for run in runs:
        tools = (
            await session.scalars(
                select(InvestigationToolExecution)
                .where(InvestigationToolExecution.analysis_run_id == run.id)
                .order_by(InvestigationToolExecution.sequence_number)
            )
        ).all()
        findings = (
            await session.scalars(
                select(InvestigationFinding)
                .where(InvestigationFinding.analysis_run_id == run.id)
                .order_by(InvestigationFinding.created_at)
            )
        ).all()
        diagnosis = await session.get(InvestigationDiagnosisResult, run.id)
        payloads.append(
            {
                "id": run.id,
                "incident_id": run.incident_id,
                "status": run.status,
                "stop_reason": run.stop_reason,
                "degradation_reasons": run.degradation_reasons or [],
                "target_context": run.target_context_json,
                "engine": run.engine,
                "engine_version": run.engine_version,
                "input_snapshot_hash": run.input_snapshot_hash,
                "budget": run.budget_json or {},
                "budget_usage": run.budget_usage_json or {},
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "created_at": run.created_at,
                "tool_executions": [
                    {
                        "id": tool.id,
                        "sequence_number": tool.sequence_number,
                        "tool_name": tool.tool_name,
                        "tool_version": tool.tool_version,
                        "status": tool.status,
                        "input": tool.input_json,
                        "structured_output": tool.structured_output_json,
                        "result_summary": tool.model_visible_output_json,
                        "raw_artifact_hash": tool.raw_artifact_hash,
                        "cost_units": tool.cost_units,
                        "is_truncated": tool.is_truncated,
                        "error_code": tool.error_code,
                        "error_message": tool.error_message,
                        "retryable": tool.retryable,
                        "reused_execution_id": tool.reused_execution_id,
                        "raw_artifact_uri": tool.raw_artifact_uri,
                        "started_at": tool.started_at,
                        "completed_at": tool.completed_at,
                    }
                    for tool in tools
                ],
                "findings": [
                    {
                        "id": finding.id,
                        "finding_type": finding.finding_type,
                        "subject": finding.subject_ref_json,
                        "value": finding.value_json,
                        "polarity": finding.polarity,
                        "quality": finding.quality,
                        "event_time": finding.event_time,
                        "tool_execution_id": finding.tool_execution_id,
                        "parser_version": finding.parser_version,
                        "confirmation_rule": finding.confirmation_rule,
                    }
                    for finding in findings
                ],
                "diagnosis": (
                    {
                        "summary": diagnosis.summary,
                        "fact_refs": diagnosis.fact_refs_json,
                        "hypotheses": diagnosis.hypotheses_json,
                        "missing_evidence": diagnosis.missing_evidence_json,
                        "recommended_checks": diagnosis.recommended_checks_json,
                        "risk_notes": diagnosis.risk_notes_json,
                        "degradation_reasons": diagnosis.degradation_reasons_json,
                        "validated_output": diagnosis.validated_output_json,
                    }
                    if diagnosis
                    else None
                ),
            }
        )
    return payloads
