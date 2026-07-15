from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.contracts import TargetContext, ToolResult
from app.investigation.tools.container_status import (
    TOOL_NAME,
    TOOL_VERSION,
    get_container_termination_status,
)
from app.models import InvestigationToolExecution


def utcnow() -> datetime:
    return datetime.now(UTC)


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class ToolRuntime:
    """Minimal trusted runtime: bind target, validate scope, execute, persist."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute_container_termination_status(
        self,
        *,
        analysis_run_id: int,
        sequence_number: int,
        target: TargetContext,
    ) -> ToolResult:
        started_at = utcnow()
        input_json = {
            "target": target.ref().model_dump(mode="json"),
            "scope": {"allowed_namespaces": target.allowed_namespaces},
        }
        observation = await get_container_termination_status(target)
        completed_at = utcnow()
        raw_hash = stable_hash(observation.raw_output) if observation.raw_output is not None else None
        visible_output = {
            "summary": observation.summary,
            "status": observation.status.value,
            "completeness": observation.completeness.value,
        }
        row = InvestigationToolExecution(
            analysis_run_id=analysis_run_id,
            sequence_number=sequence_number,
            tool_name=TOOL_NAME,
            tool_version=TOOL_VERSION,
            normalized_input_hash=stable_hash(input_json),
            input_json=input_json,
            status=observation.status.value,
            structured_output_json=observation.data,
            model_visible_output_json=visible_output,
            raw_output_json=observation.raw_output,
            raw_artifact_uri=None,
            raw_artifact_hash=raw_hash,
            cost_units=1,
            is_truncated=False,
            error_code=observation.error_code,
            error_message=observation.error_message,
            retryable=observation.retryable,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.session.add(row)
        await self.session.flush()
        return ToolResult(
            execution_id=str(row.id),
            status=observation.status,
            target=target.ref(),
            data=observation.data,
            finding_ids=[],
            completeness=observation.completeness,
            model_visible_summary=observation.summary,
            tool_name=TOOL_NAME,
            tool_version=TOOL_VERSION,
            cost_units=1,
            started_at=started_at,
            completed_at=completed_at,
            error_code=observation.error_code,
            retryable=observation.retryable,
        )
