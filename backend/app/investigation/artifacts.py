from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol
from uuid import uuid4
import zlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.contracts import ArtifactRef
from app.models import InvestigationArtifact

_SENSITIVE_KEY = re.compile(
    r"(^|_)(authorization|bearer|token|api[_-]?key|password|passwd|cookie|secret|connection[_-]?string)($|_)",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class ArtifactPolicy:
    max_inline_raw_bytes: int = 65_536
    max_collection_items: int = 200
    max_string_chars: int = 4_000
    max_depth: int = 10
    max_model_summary_chars: int = 2_000


@dataclass(slots=True)
class PreparedArtifact:
    inline_value: dict[str, Any] | list[Any] | None
    artifact_ref: ArtifactRef | None
    sha256: str | None
    size_bytes: int
    is_truncated: bool


class ArtifactStorage(Protocol):
    async def save_json(self, value: dict[str, Any] | list[Any]) -> ArtifactRef: ...


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact_sensitive(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def crop_json(value: Any, policy: ArtifactPolicy, *, depth: int = 0) -> tuple[Any, bool]:
    if depth >= policy.max_depth:
        return "[TRUNCATED_DEPTH]", True
    if isinstance(value, dict):
        items = list(value.items())
        truncated = len(items) > policy.max_collection_items
        cropped: dict[str, Any] = {}
        for key, item in items[: policy.max_collection_items]:
            child, child_truncated = crop_json(item, policy, depth=depth + 1)
            cropped[str(key)] = child
            truncated = truncated or child_truncated
        if len(items) > policy.max_collection_items:
            cropped["_truncated_keys"] = len(items) - policy.max_collection_items
        return cropped, truncated
    if isinstance(value, list):
        truncated = len(value) > policy.max_collection_items
        cropped_list = []
        for item in value[: policy.max_collection_items]:
            child, child_truncated = crop_json(item, policy, depth=depth + 1)
            cropped_list.append(child)
            truncated = truncated or child_truncated
        if len(value) > policy.max_collection_items:
            cropped_list.append({"_truncated_items": len(value) - policy.max_collection_items})
        return cropped_list, truncated
    if isinstance(value, str) and len(value) > policy.max_string_chars:
        return value[: policy.max_string_chars] + "...[TRUNCATED]", True
    return value, False


class PostgresArtifactStorage:
    """Stores complete sanitized large artifacts compressed in PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_json(self, value: dict[str, Any] | list[Any]) -> ArtifactRef:
        raw = canonical_json_bytes(value)
        sha256 = hashlib.sha256(raw).hexdigest()
        existing = await self.session.scalar(
            select(InvestigationArtifact).where(InvestigationArtifact.sha256 == sha256)
        )
        if existing is None:
            artifact_id = f"A-{uuid4().hex}"
            self.session.add(
                InvestigationArtifact(
                    id=artifact_id,
                    sha256=sha256,
                    content_type="application/json",
                    size_bytes=len(raw),
                    compression="zlib",
                    content=zlib.compress(raw, level=6),
                )
            )
            await self.session.flush()
        else:
            artifact_id = existing.id
        return ArtifactRef(
            uri=f"db://investigation_artifacts/{artifact_id}",
            sha256=sha256,
            size_bytes=len(raw),
        )


async def prepare_tool_artifact(
    *,
    structured_output: dict[str, Any],
    raw_output: dict[str, Any] | list[Any] | None,
    storage: ArtifactStorage,
    policy: ArtifactPolicy,
) -> PreparedArtifact:
    """Persist a complete sanitized tool artifact when any visible part is cropped."""
    sanitized_structured = redact_sensitive(structured_output)
    sanitized_raw = redact_sensitive(raw_output) if raw_output is not None else None
    full_artifact = {
        "structured_output": sanitized_structured,
        "raw_output": sanitized_raw,
    }
    full_bytes = canonical_json_bytes(full_artifact)
    sha256 = hashlib.sha256(full_bytes).hexdigest()
    cropped_raw, raw_cropped = crop_json(sanitized_raw, policy) if sanitized_raw is not None else (None, False)
    _, structured_cropped = crop_json(sanitized_structured, policy)
    exceeds_inline = len(full_bytes) > policy.max_inline_raw_bytes
    needs_external = bool(exceeds_inline or raw_cropped or structured_cropped)
    artifact_ref = await storage.save_json(full_artifact) if needs_external else None
    return PreparedArtifact(
        inline_value=cropped_raw if (raw_cropped or exceeds_inline) else sanitized_raw,
        artifact_ref=artifact_ref,
        sha256=sha256,
        size_bytes=len(full_bytes),
        is_truncated=needs_external,
    )


# Backward-compatible helper for callers that only have a raw object.
async def prepare_raw_artifact(
    value: dict[str, Any] | list[Any] | None,
    *,
    storage: ArtifactStorage,
    policy: ArtifactPolicy,
) -> PreparedArtifact:
    return await prepare_tool_artifact(
        structured_output={},
        raw_output=value,
        storage=storage,
        policy=policy,
    )
