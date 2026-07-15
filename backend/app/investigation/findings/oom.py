from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.investigation.contracts import DeterministicFinding, TargetContext, ToolResult
from app.investigation.enums import (
    FindingPolarity,
    FindingQuality,
    ResolutionQuality,
    ToolStatus,
)

PARSER_VERSION = "1.0.0"
CONFIRMATION_RULE = "container_oom_killed_v1"


def parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def extract_oom_killed_finding(
    result: ToolResult,
    target: TargetContext,
) -> DeterministicFinding | None:
    """Only code can confirm OOMKilled; failures and uncertainty never become findings."""
    if result.status != ToolStatus.FOUND:
        return None
    if target.resolution_quality != ResolutionQuality.HIGH:
        return None
    termination = result.data.get("termination") or {}
    if str(termination.get("reason") or "") != "OOMKilled":
        return None
    event_time = parse_time(termination.get("finishedAt"))
    if event_time is None or not (target.window_start <= event_time <= target.window_end):
        return None
    return DeterministicFinding(
        id=f"F-{uuid4().hex}",
        finding_type="container_oom_killed",
        subject=target.ref(),
        value={
            "reason": "OOMKilled",
            "exit_code": termination.get("exitCode"),
            "signal": termination.get("signal"),
            "restart_count": result.data.get("restart_count"),
            "source": termination.get("source"),
        },
        polarity=FindingPolarity.POSITIVE,
        quality=FindingQuality.HIGH,
        event_time=event_time,
        tool_execution_id=result.execution_id,
        parser_version=PARSER_VERSION,
        confirmation_rule=CONFIRMATION_RULE,
    )
