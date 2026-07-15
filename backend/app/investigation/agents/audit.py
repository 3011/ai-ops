from __future__ import annotations

from datetime import UTC, datetime
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.artifacts import PostgresArtifactStorage, redact_sensitive
from app.investigation.tool_runtime import stable_hash
from app.models import InvestigationModelInvocation


def utcnow() -> datetime:
    return datetime.now(UTC)


class ModelInvocationAudit:
    def __init__(self, session: AsyncSession, analysis_run_id: int) -> None:
        self.session = session
        self.analysis_run_id = analysis_run_id
        self.storage = PostgresArtifactStorage(session)

    async def next_sequence(self) -> int:
        current = await self.session.scalar(
            select(func.max(InvestigationModelInvocation.sequence_number)).where(
                InvestigationModelInvocation.analysis_run_id == self.analysis_run_id
            )
        )
        return int(current or 0) + 1

    async def start(
        self,
        *,
        invocation_type: str,
        runtime_name: str,
        runtime_version: str,
        provider: str,
        model: str,
        model_parameters: dict[str, Any],
        prompt_version: str,
        request_payload: dict[str, Any],
    ) -> tuple[InvestigationModelInvocation, float]:
        sanitized = redact_sensitive(request_payload)
        artifact = await self.storage.save_json(sanitized)
        row = InvestigationModelInvocation(
            analysis_run_id=self.analysis_run_id,
            sequence_number=await self.next_sequence(),
            invocation_type=invocation_type,
            runtime_name=runtime_name,
            runtime_version=runtime_version,
            provider=provider,
            model=model,
            model_parameters_json=redact_sensitive(model_parameters),
            prompt_version=prompt_version,
            request_snapshot_uri=artifact.uri,
            request_hash=stable_hash(sanitized),
            status="RUNNING",
            started_at=utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return row, time.perf_counter()

    async def complete(
        self,
        row: InvestigationModelInvocation,
        started: float,
        *,
        response_payload: dict[str, Any],
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        sanitized = redact_sensitive(response_payload)
        artifact = await self.storage.save_json(sanitized)
        row.response_snapshot_uri = artifact.uri
        row.response_hash = stable_hash(sanitized)
        row.status = "SUCCEEDED"
        row.input_tokens = input_tokens
        row.output_tokens = output_tokens
        row.latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        row.completed_at = utcnow()
        await self.session.flush()

    async def fail(
        self,
        row: InvestigationModelInvocation,
        started: float,
        *,
        error_code: str,
        error_message: str,
        response_payload: dict[str, Any] | None = None,
    ) -> None:
        if response_payload is not None:
            sanitized = redact_sensitive(response_payload)
            artifact = await self.storage.save_json(sanitized)
            row.response_snapshot_uri = artifact.uri
            row.response_hash = stable_hash(sanitized)
        row.status = "FAILED"
        row.error_code = error_code[:128]
        row.error_message = error_message[:4000]
        row.latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        row.completed_at = utcnow()
        await self.session.flush()
