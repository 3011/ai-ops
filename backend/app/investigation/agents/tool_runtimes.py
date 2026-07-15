from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.agents.contracts import AgentToolObservation, AgentToolSpec
from app.investigation.agents.runtime import model_safe_value
from app.investigation.budget import BudgetLedger
from app.investigation.contracts import TargetContext
from app.investigation.enums import ToolStatus
from app.investigation.registry import ToolRegistry
from app.investigation.tool_runtime import ToolRuntime, stable_hash
from app.models import InvestigationToolExecution


def utcnow() -> datetime:
    return datetime.now(UTC)


def _spec_from_catalog(row: dict[str, Any]) -> AgentToolSpec:
    return AgentToolSpec(
        name=str(row["name"]),
        version=str(row["version"]),
        cost_units=int(row.get("cost_units") or 0),
        arguments_schema=dict(row.get("arguments_schema") or {}),
    )


def _observation_from_result(result: Any) -> AgentToolObservation:
    return AgentToolObservation(
        execution_id=str(result.execution_id),
        tool_name=result.tool_name,
        tool_version=result.tool_version,
        status=result.status.value if hasattr(result.status, "value") else str(result.status),
        summary=result.model_visible_summary,
        finding_ids=list(result.finding_ids),
        data=model_safe_value(result.data),
        error_code=result.error_code,
        cost_units=result.cost_units,
        reused_execution_id=result.reused_execution_id,
    )


class LiveAgentToolRuntime:
    source_data_access_count: int

    def __init__(
        self,
        session: AsyncSession,
        *,
        analysis_run_id: int,
        target: TargetContext | None,
        registry: ToolRegistry,
        budget: BudgetLedger,
    ) -> None:
        self.analysis_run_id = analysis_run_id
        self.target = target
        self.registry = registry
        self.runtime = ToolRuntime(session, registry=registry, budget=budget)
        self.sequence = 0
        self.source_data_access_count = 0

    def catalog(self) -> list[AgentToolSpec]:
        return [_spec_from_catalog(row) for row in self.registry.catalog()]

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> AgentToolObservation:
        self.sequence += 1
        if self.target is None:
            return AgentToolObservation(
                execution_id=f"invalid-{self.sequence}",
                tool_name=tool_name,
                tool_version="unknown",
                status=ToolStatus.TARGET_UNCERTAIN.value,
                summary="目标未可靠解析，实时 Shadow 不访问数据源。",
                finding_ids=[],
                data={},
                error_code="TARGET_UNCERTAIN",
                cost_units=0,
            )
        before = self.runtime.budget.tool_calls_used
        result = await self.runtime.execute(
            analysis_run_id=self.analysis_run_id,
            sequence_number=self.sequence,
            target=self.target,
            tool_name=tool_name,
            arguments=arguments,
        )
        if self.runtime.budget.tool_calls_used > before and result.reused_execution_id is None and result.status not in {
            ToolStatus.BUDGET_EXCEEDED, ToolStatus.INVALID_REQUEST
        }:
            self.source_data_access_count += 1
        return _observation_from_result(result)


class SnapshotAgentToolRuntime:
    """Replays only model-visible ToolResults already stored in one Snapshot."""

    source_data_access_count = 0

    def __init__(
        self,
        session: AsyncSession,
        *,
        analysis_run_id: int,
        snapshot: dict[str, Any],
        registry: ToolRegistry,
    ) -> None:
        self.session = session
        self.analysis_run_id = analysis_run_id
        self.snapshot = snapshot
        self.registry = registry
        self.sequence = 0
        self._used: set[str] = set()
        self._rows = list(snapshot.get("tool_executions") or [])

    def catalog(self) -> list[AgentToolSpec]:
        names = list(dict.fromkeys(str((row.get("tool") or {}).get("name") or "") for row in self._rows))
        specs: list[AgentToolSpec] = []
        current = {row["name"]: row for row in self.registry.catalog()}
        for name in names:
            if not name:
                continue
            row = current.get(name)
            if row:
                specs.append(_spec_from_catalog(row))
            else:
                source = next(item for item in self._rows if str((item.get("tool") or {}).get("name")) == name)
                specs.append(AgentToolSpec(
                    name=name,
                    version=str((source.get("tool") or {}).get("version") or "unknown"),
                    cost_units=0,
                    arguments_schema={},
                ))
        return specs

    def _match(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [
            row for row in self._rows
            if str((row.get("tool") or {}).get("name") or "") == tool_name
            and str(row.get("execution_id")) not in self._used
        ]
        if not candidates:
            return None
        if not arguments:
            return candidates[0]
        for row in candidates:
            saved = ((row.get("input") or {}).get("arguments") or {})
            if stable_hash(saved) == stable_hash(arguments):
                return row
        return None

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> AgentToolObservation:
        self.sequence += 1
        source = self._match(tool_name, arguments)
        started = utcnow()
        if source is None:
            visible = {
                "summary": "Snapshot 中没有该工具及参数组合；未访问实时数据源。",
                "status": ToolStatus.UNAVAILABLE.value,
                "completeness": "unknown",
                "finding_ids": [],
                "error_code": "SNAPSHOT_TOOL_NOT_AVAILABLE",
                "is_truncated": False,
            }
            row = InvestigationToolExecution(
                analysis_run_id=self.analysis_run_id,
                sequence_number=self.sequence,
                tool_name=tool_name,
                tool_version=(self.registry.get(tool_name).version if self.registry.get(tool_name) else "unknown"),
                normalized_input_hash=stable_hash({"tool_name": tool_name, "arguments": arguments}),
                input_json={"arguments": arguments, "snapshot_only": True},
                status=ToolStatus.UNAVAILABLE.value,
                structured_output_json={},
                model_visible_output_json=visible,
                raw_output_json=None,
                raw_artifact_uri=None,
                raw_artifact_hash=None,
                cost_units=0,
                is_truncated=False,
                error_code="SNAPSHOT_TOOL_NOT_AVAILABLE",
                error_message=visible["summary"],
                retryable=False,
                started_at=started,
                completed_at=utcnow(),
            )
            self.session.add(row)
            await self.session.flush()
            return AgentToolObservation(
                execution_id=str(row.id),
                tool_name=tool_name,
                tool_version=row.tool_version,
                status=row.status,
                summary=visible["summary"],
                finding_ids=[],
                data={},
                error_code=row.error_code,
                cost_units=0,
            )

        source_id = str(source.get("execution_id"))
        self._used.add(source_id)
        result = dict(source.get("result") or {})
        source_tool = dict(source.get("tool") or {})
        source_input = dict(source.get("input") or {})
        finding_ids = [str(value) for value in (result.get("finding_ids") or [])]
        visible = {
            "summary": str(result.get("summary") or "Snapshot ToolResult replayed."),
            "status": str(source.get("status") or result.get("status") or ToolStatus.UNAVAILABLE.value),
            "completeness": str(result.get("completeness") or "unknown"),
            "finding_ids": finding_ids,
            "error_code": result.get("error_code"),
            "is_truncated": bool(result.get("is_truncated")),
            "snapshot_replay": True,
            "reused_execution_id": source_id,
        }
        reused_id = int(source_id) if source_id.isdigit() else None
        row = InvestigationToolExecution(
            analysis_run_id=self.analysis_run_id,
            sequence_number=self.sequence,
            tool_name=str(source_tool.get("name") or tool_name),
            tool_version=str(source_tool.get("version") or "unknown"),
            normalized_input_hash=stable_hash({"source_execution_id": source_id, "arguments": arguments}),
            input_json={**source_input, "snapshot_only": True, "requested_arguments": arguments},
            status=visible["status"],
            structured_output_json=model_safe_value(result.get("structured_data") or {}),
            model_visible_output_json=visible,
            raw_output_json=None,
            raw_artifact_uri=result.get("raw_artifact_uri"),
            raw_artifact_hash=result.get("raw_artifact_hash"),
            cost_units=0,
            is_truncated=bool(result.get("is_truncated")),
            error_code=result.get("error_code"),
            error_message=None,
            retryable=False,
            reused_execution_id=reused_id,
            started_at=started,
            completed_at=utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return AgentToolObservation(
            execution_id=str(row.id),
            tool_name=row.tool_name,
            tool_version=row.tool_version,
            status=row.status,
            summary=visible["summary"],
            finding_ids=finding_ids,
            data=model_safe_value(result.get("structured_data") or {}),
            error_code=row.error_code,
            cost_units=0,
            reused_execution_id=source_id,
        )
