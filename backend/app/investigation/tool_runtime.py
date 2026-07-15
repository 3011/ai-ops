from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.investigation.artifacts import (
    ArtifactPolicy,
    PostgresArtifactStorage,
    crop_json,
    prepare_tool_artifact,
    redact_sensitive,
)
from app.investigation.budget import BudgetLedger
from app.investigation.contracts import InvestigationBudget, TargetContext, ToolObservation, ToolResult
from app.investigation.enums import Completeness, ToolStatus
from app.investigation.registry import ToolRegistry
from app.models import InvestigationFinding, InvestigationToolExecution

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(UTC)


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class ToolRuntime:
    """Generic trusted execution pipeline for all registered read-only tools."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        registry: ToolRegistry | None = None,
        budget: BudgetLedger | None = None,
        artifact_storage: PostgresArtifactStorage | None = None,
        artifact_policy: ArtifactPolicy | None = None,
    ) -> None:
        self.session = session
        if registry is None:
            from app.investigation.catalog import build_default_registry
            registry = build_default_registry()
        self.registry = registry
        self.budget = budget or BudgetLedger(InvestigationBudget())
        self.artifact_storage = artifact_storage or PostgresArtifactStorage(session)
        self.artifact_policy = artifact_policy or ArtifactPolicy(
            max_inline_raw_bytes=settings.investigation_raw_json_max_bytes,
            max_collection_items=settings.investigation_collection_max_items,
            max_string_chars=settings.investigation_string_max_chars,
        )

    def _input_payload(self, target: TargetContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "target": target.ref().model_dump(mode="json"),
            "target_identity": target.identity_payload(),
            "arguments": arguments,
            "scope": {"allowed_namespaces": list(target.allowed_namespaces)},
        }

    async def _persist_terminal_result(
        self,
        *,
        analysis_run_id: int,
        sequence_number: int,
        target: TargetContext,
        tool_name: str,
        tool_version: str,
        input_json: dict[str, Any],
        normalized_input_hash: str,
        status: ToolStatus,
        summary: str,
        error_code: str,
        retryable: bool = False,
    ) -> ToolResult:
        started_at = utcnow()
        completed_at = utcnow()
        visible = {
            "summary": summary,
            "status": status.value,
            "completeness": Completeness.UNKNOWN.value,
            "finding_ids": [],
            "error_code": error_code,
            "is_truncated": False,
        }
        row = InvestigationToolExecution(
            analysis_run_id=analysis_run_id,
            sequence_number=sequence_number,
            tool_name=tool_name,
            tool_version=tool_version,
            normalized_input_hash=normalized_input_hash,
            input_json=input_json,
            status=status.value,
            structured_output_json={},
            model_visible_output_json=visible,
            raw_output_json=None,
            raw_artifact_uri=None,
            raw_artifact_hash=None,
            cost_units=0,
            is_truncated=False,
            error_code=error_code,
            error_message=summary,
            retryable=retryable,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.session.add(row)
        await self.session.flush()
        return ToolResult(
            execution_id=str(row.id),
            status=status,
            target=target.ref(),
            data={},
            finding_ids=[],
            completeness=Completeness.UNKNOWN,
            model_visible_summary=summary,
            tool_name=tool_name,
            tool_version=tool_version,
            cost_units=0,
            started_at=started_at,
            completed_at=completed_at,
            error_code=error_code,
            retryable=retryable,
        )


    async def _persist_nonexecuting_attempt(
        self,
        *,
        analysis_run_id: int,
        sequence_number: int,
        target: TargetContext,
        tool_name: str,
        tool_version: str,
        input_json: dict[str, Any],
        normalized_input_hash: str,
        status: ToolStatus,
        summary: str,
        error_code: str,
    ) -> ToolResult:
        decision = self.budget.reserve(tool_name, 0)
        if not decision.allowed:
            return await self._persist_terminal_result(
                analysis_run_id=analysis_run_id,
                sequence_number=sequence_number,
                target=target,
                tool_name=tool_name,
                tool_version=tool_version,
                input_json=input_json,
                normalized_input_hash=normalized_input_hash,
                status=ToolStatus.BUDGET_EXCEEDED,
                summary=decision.message or "调查预算不足。",
                error_code=decision.error_code or "BUDGET_EXCEEDED",
            )
        result = await self._persist_terminal_result(
            analysis_run_id=analysis_run_id,
            sequence_number=sequence_number,
            target=target,
            tool_name=tool_name,
            tool_version=tool_version,
            input_json=input_json,
            normalized_input_hash=normalized_input_hash,
            status=status,
            summary=summary,
            error_code=error_code,
        )
        self.budget.record_findings(0)
        return result

    async def _cache_hit(
        self,
        *,
        analysis_run_id: int,
        tool_name: str,
        tool_version: str,
        normalized_input_hash: str,
    ) -> InvestigationToolExecution | None:
        return await self.session.scalar(
            select(InvestigationToolExecution)
            .where(
                InvestigationToolExecution.analysis_run_id == analysis_run_id,
                InvestigationToolExecution.tool_name == tool_name,
                InvestigationToolExecution.tool_version == tool_version,
                InvestigationToolExecution.normalized_input_hash == normalized_input_hash,
                InvestigationToolExecution.reused_execution_id.is_(None),
                InvestigationToolExecution.retryable.is_(False),
                InvestigationToolExecution.status.in_([
                    ToolStatus.FOUND.value,
                    ToolStatus.NOT_FOUND.value,
                    ToolStatus.PARTIAL.value,
                    ToolStatus.TARGET_UNCERTAIN.value,
                    ToolStatus.DENIED.value,
                ]),
            )
            .order_by(InvestigationToolExecution.id)
            .limit(1)
        )

    async def _reuse_cached(
        self,
        *,
        cached: InvestigationToolExecution,
        analysis_run_id: int,
        sequence_number: int,
        target: TargetContext,
        input_json: dict[str, Any],
        normalized_input_hash: str,
    ) -> ToolResult:
        decision = self.budget.reserve(cached.tool_name, 0)
        if not decision.allowed:
            return await self._persist_terminal_result(
                analysis_run_id=analysis_run_id,
                sequence_number=sequence_number,
                target=target,
                tool_name=cached.tool_name,
                tool_version=cached.tool_version,
                input_json=input_json,
                normalized_input_hash=normalized_input_hash,
                status=ToolStatus.BUDGET_EXCEEDED,
                summary=decision.message or "调查预算不足。",
                error_code=decision.error_code or "BUDGET_EXCEEDED",
            )
        finding_ids = list(
            await self.session.scalars(
                select(InvestigationFinding.id).where(
                    InvestigationFinding.tool_execution_id == cached.id
                )
            )
        )
        started_at = utcnow()
        completed_at = utcnow()
        visible = dict(cached.model_visible_output_json or {})
        visible.update({"finding_ids": finding_ids, "cache_hit": True, "reused_execution_id": str(cached.id)})
        row = InvestigationToolExecution(
            analysis_run_id=analysis_run_id,
            sequence_number=sequence_number,
            tool_name=cached.tool_name,
            tool_version=cached.tool_version,
            normalized_input_hash=normalized_input_hash,
            input_json=input_json,
            status=cached.status,
            structured_output_json=cached.structured_output_json or {},
            model_visible_output_json=visible,
            raw_output_json=None,
            raw_artifact_uri=cached.raw_artifact_uri,
            raw_artifact_hash=cached.raw_artifact_hash,
            cost_units=0,
            is_truncated=cached.is_truncated,
            error_code=cached.error_code,
            error_message=cached.error_message,
            retryable=cached.retryable,
            reused_execution_id=cached.id,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.session.add(row)
        await self.session.flush()
        self.budget.record_findings(len(finding_ids))
        return ToolResult(
            execution_id=str(row.id),
            status=ToolStatus(cached.status),
            target=target.ref(),
            data=cached.structured_output_json or {},
            finding_ids=finding_ids,
            completeness=Completeness((cached.model_visible_output_json or {}).get("completeness", Completeness.UNKNOWN.value)),
            model_visible_summary=str((cached.model_visible_output_json or {}).get("summary") or "缓存结果"),
            tool_name=cached.tool_name,
            tool_version=cached.tool_version,
            cost_units=0,
            started_at=started_at,
            completed_at=completed_at,
            error_code=cached.error_code,
            retryable=cached.retryable,
            reused_execution_id=str(cached.id),
            is_truncated=cached.is_truncated,
            raw_artifact_uri=cached.raw_artifact_uri,
            raw_artifact_hash=cached.raw_artifact_hash,
        )

    async def execute(
        self,
        *,
        analysis_run_id: int,
        sequence_number: int,
        target: TargetContext,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        tool = self.registry.get(tool_name)
        tool_version = tool.version if tool else "unknown"
        raw_arguments = redact_sensitive(arguments)
        input_json = self._input_payload(target, raw_arguments)
        normalized_input_hash = stable_hash({
            "target_identity": target.identity_payload(),
            "arguments": raw_arguments,
        })

        if tool is None:
            return await self._persist_nonexecuting_attempt(
                analysis_run_id=analysis_run_id,
                sequence_number=sequence_number,
                target=target,
                tool_name=tool_name,
                tool_version=tool_version,
                input_json=input_json,
                normalized_input_hash=normalized_input_hash,
                status=ToolStatus.INVALID_REQUEST,
                summary=f"未注册工具：{tool_name}",
                error_code="UNKNOWN_TOOL",
            )
        if target.namespace not in target.allowed_namespaces:
            return await self._persist_nonexecuting_attempt(
                analysis_run_id=analysis_run_id,
                sequence_number=sequence_number,
                target=target,
                tool_name=tool.name,
                tool_version=tool.version,
                input_json=input_json,
                normalized_input_hash=normalized_input_hash,
                status=ToolStatus.DENIED,
                summary="TargetContext namespace 超出允许范围。",
                error_code="NAMESPACE_OUT_OF_SCOPE",
            )
        try:
            parsed_arguments = tool.arguments_model.model_validate(arguments)
        except ValidationError:
            return await self._persist_nonexecuting_attempt(
                analysis_run_id=analysis_run_id,
                sequence_number=sequence_number,
                target=target,
                tool_name=tool.name,
                tool_version=tool.version,
                input_json=input_json,
                normalized_input_hash=normalized_input_hash,
                status=ToolStatus.INVALID_REQUEST,
                summary="工具参数未通过 Schema 校验。",
                error_code="ARGUMENT_VALIDATION_FAILED",
            )

        normalized_arguments = parsed_arguments.model_dump(mode="json")
        input_json = self._input_payload(target, normalized_arguments)
        normalized_input_hash = stable_hash({
            "target_identity": target.identity_payload(),
            "arguments": normalized_arguments,
        })
        cached = await self._cache_hit(
            analysis_run_id=analysis_run_id,
            tool_name=tool.name,
            tool_version=tool.version,
            normalized_input_hash=normalized_input_hash,
        )
        if cached is not None:
            return await self._reuse_cached(
                cached=cached,
                analysis_run_id=analysis_run_id,
                sequence_number=sequence_number,
                target=target,
                input_json=input_json,
                normalized_input_hash=normalized_input_hash,
            )

        decision = self.budget.reserve(tool.name, tool.cost_units)
        if not decision.allowed:
            return await self._persist_terminal_result(
                analysis_run_id=analysis_run_id,
                sequence_number=sequence_number,
                target=target,
                tool_name=tool.name,
                tool_version=tool.version,
                input_json=input_json,
                normalized_input_hash=normalized_input_hash,
                status=ToolStatus.BUDGET_EXCEEDED,
                summary=decision.message or "调查预算不足。",
                error_code=decision.error_code or "BUDGET_EXCEEDED",
            )

        started_at = utcnow()
        try:
            observation = await tool.execute(target, parsed_arguments)
        except Exception as exc:
            observation = ToolObservation(
                status=ToolStatus.UNAVAILABLE,
                data={},
                raw_output=None,
                completeness=Completeness.UNKNOWN,
                summary="工具执行发生未处理异常，已保存审计记录。",
                error_code="TOOL_EXECUTION_EXCEPTION",
                error_message=f"{type(exc).__name__}: {exc}"[:1000],
                retryable=False,
            )
        completed_at = utcnow()

        full_data = redact_sensitive(observation.data)
        stored_data, structured_truncated = crop_json(full_data, self.artifact_policy)
        prepared = await prepare_tool_artifact(
            structured_output=full_data,
            raw_output=observation.raw_output,
            storage=self.artifact_storage,
            policy=self.artifact_policy,
        )
        is_truncated = bool(structured_truncated or prepared.is_truncated)
        final_status = observation.status
        final_completeness = observation.completeness
        if structured_truncated and final_status in {ToolStatus.FOUND, ToolStatus.NOT_FOUND}:
            final_status = ToolStatus.PARTIAL
            final_completeness = Completeness.PARTIAL
        summary = observation.summary[: self.artifact_policy.max_model_summary_chars]
        visible = {
            "summary": summary,
            "status": final_status.value,
            "completeness": final_completeness.value,
            "finding_ids": [],
            "error_code": observation.error_code,
            "is_truncated": is_truncated,
            "raw_artifact_uri": prepared.artifact_ref.uri if prepared.artifact_ref else None,
        }
        row = InvestigationToolExecution(
            analysis_run_id=analysis_run_id,
            sequence_number=sequence_number,
            tool_name=tool.name,
            tool_version=tool.version,
            normalized_input_hash=normalized_input_hash,
            input_json=input_json,
            status=final_status.value,
            structured_output_json=stored_data,
            model_visible_output_json=visible,
            raw_output_json=prepared.inline_value,
            raw_artifact_uri=prepared.artifact_ref.uri if prepared.artifact_ref else None,
            raw_artifact_hash=prepared.sha256,
            cost_units=tool.cost_units,
            is_truncated=is_truncated,
            error_code=observation.error_code,
            error_message=observation.error_message,
            retryable=observation.retryable,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.session.add(row)
        await self.session.flush()

        parser_result = ToolResult(
            execution_id=str(row.id),
            status=final_status,
            target=target.ref(),
            data=full_data,
            finding_ids=[],
            completeness=final_completeness,
            model_visible_summary=summary,
            tool_name=tool.name,
            tool_version=tool.version,
            cost_units=tool.cost_units,
            started_at=started_at,
            completed_at=completed_at,
            error_code=observation.error_code,
            retryable=observation.retryable,
            is_truncated=is_truncated,
            raw_artifact_uri=prepared.artifact_ref.uri if prepared.artifact_ref else None,
            raw_artifact_hash=prepared.sha256,
        )
        findings = []
        parser_errors: list[str] = []
        for parser in self.registry.parsers_for(tool.name):
            try:
                findings.extend(parser(parser_result, target))
            except Exception as exc:
                parser_errors.append(f"{getattr(parser, '__name__', 'parser')}: {type(exc).__name__}")
        for finding in findings:
            self.session.add(
                InvestigationFinding(
                    id=finding.id,
                    analysis_run_id=analysis_run_id,
                    tool_execution_id=row.id,
                    finding_type=finding.finding_type,
                    subject_ref_json=finding.subject.model_dump(mode="json"),
                    value_json=finding.value,
                    polarity=finding.polarity.value,
                    quality=finding.quality.value,
                    event_time=finding.event_time,
                    parser_version=finding.parser_version,
                    confirmation_rule=finding.confirmation_rule,
                )
            )
        finding_ids = [finding.id for finding in findings]
        if parser_errors:
            final_status = ToolStatus.PARTIAL
            final_completeness = Completeness.PARTIAL
            row.status = final_status.value
            row.error_code = "FINDING_PARSER_ERROR"
            row.error_message = "; ".join(parser_errors)[:1000]
            row.retryable = False
        visible.update({
            "status": final_status.value,
            "completeness": final_completeness.value,
            "finding_ids": finding_ids,
            "parser_errors": parser_errors,
        })
        row.model_visible_output_json = dict(visible)
        flag_modified(row, "model_visible_output_json")
        await self.session.flush()
        self.budget.record_findings(len(finding_ids))

        return ToolResult(
            execution_id=str(row.id),
            status=final_status,
            target=target.ref(),
            data=stored_data,
            finding_ids=finding_ids,
            completeness=final_completeness,
            model_visible_summary=summary,
            tool_name=tool.name,
            tool_version=tool.version,
            cost_units=tool.cost_units,
            started_at=started_at,
            completed_at=completed_at,
            error_code=row.error_code,
            retryable=row.retryable,
            is_truncated=is_truncated,
            raw_artifact_uri=row.raw_artifact_uri,
            raw_artifact_hash=row.raw_artifact_hash,
        )

    async def execute_container_termination_status(
        self,
        *,
        analysis_run_id: int,
        sequence_number: int,
        target: TargetContext,
    ) -> ToolResult:
        """Compatibility wrapper; all behavior is delegated to execute()."""
        return await self.execute(
            analysis_run_id=analysis_run_id,
            sequence_number=sequence_number,
            target=target,
            tool_name="get_container_termination_status",
            arguments={"scope": "both"},
        )
