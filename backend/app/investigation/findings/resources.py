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


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _quality(result: ToolResult) -> FindingQuality:
    return FindingQuality.HIGH if result.status == ToolStatus.FOUND and not result.is_truncated else FindingQuality.MEDIUM


def _finding(
    *,
    result: ToolResult,
    target: TargetContext,
    finding_type: str,
    value: dict[str, object],
    event_time: datetime | None,
    rule: str,
) -> DeterministicFinding:
    return DeterministicFinding(
        id=f"F-{uuid4().hex}",
        finding_type=finding_type,
        subject=target.ref(),
        value=value,
        polarity=FindingPolarity.POSITIVE,
        quality=_quality(result),
        event_time=event_time,
        tool_execution_id=result.execution_id,
        parser_version=PARSER_VERSION,
        confirmation_rule=rule,
    )


def parse_memory_usage_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status not in {ToolStatus.FOUND, ToolStatus.PARTIAL}:
        return []
    if target.resolution_quality != ResolutionQuality.HIGH:
        return []
    peak = result.data.get("observed_peak_bytes")
    ratio = result.data.get("peak_limit_ratio")
    peak_at = _parse_time(result.data.get("observed_peak_at"))
    if not isinstance(peak, (int, float)) or peak <= 0 or peak_at is None:
        return []
    if not (target.window_start <= peak_at <= target.window_end):
        return []

    findings: list[DeterministicFinding] = []
    if isinstance(ratio, (int, float)) and ratio >= 0.98:
        findings.append(_finding(
            result=result,
            target=target,
            finding_type="memory_limit_reached",
            value={
                "observed_peak_bytes": peak,
                "memory_limit_bytes": result.data.get("memory_limit_bytes"),
                "peak_limit_ratio": ratio,
                "peak_source": result.data.get("peak_source"),
                "sampling_warning": result.data.get("sampling_warning"),
            },
            event_time=peak_at,
            rule="memory_limit_reached_v1",
        ))
    elif isinstance(ratio, (int, float)) and ratio >= 0.90:
        findings.append(_finding(
            result=result,
            target=target,
            finding_type="memory_near_limit",
            value={
                "observed_peak_bytes": peak,
                "memory_limit_bytes": result.data.get("memory_limit_bytes"),
                "peak_limit_ratio": ratio,
                "peak_source": result.data.get("peak_source"),
                "sampling_warning": result.data.get("sampling_warning"),
            },
            event_time=peak_at,
            rule="memory_near_limit_v1",
        ))

    first_value = result.data.get("first_working_set_bytes")
    if isinstance(first_value, (int, float)) and first_value >= 0:
        delta = peak - first_value
        multiplier = peak / max(first_value, 1024 * 1024)
        if delta >= 16 * 1024 * 1024 and multiplier >= 1.5:
            findings.append(_finding(
                result=result,
                target=target,
                finding_type="memory_usage_increased",
                value={
                    "first_working_set_bytes": first_value,
                    "observed_peak_bytes": peak,
                    "absolute_increase_bytes": delta,
                    "increase_multiplier": multiplier,
                },
                event_time=peak_at,
                rule="memory_usage_increased_v1",
            ))
    return findings


def parse_cpu_usage_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status not in {ToolStatus.FOUND, ToolStatus.PARTIAL}:
        return []
    if target.resolution_quality != ResolutionQuality.HIGH:
        return []
    peak = result.data.get("peak_cores")
    peak_at = _parse_time(result.data.get("peak_at"))
    if not isinstance(peak, (int, float)) or peak <= 0 or peak_at is None:
        return []
    if not (target.window_start <= peak_at <= target.window_end):
        return []

    findings: list[DeterministicFinding] = []
    if result.data.get("spike_detected") is True:
        findings.append(_finding(
            result=result,
            target=target,
            finding_type="container_cpu_spike",
            value={
                "baseline_median_cores": result.data.get("baseline_median_cores"),
                "peak_cores": peak,
                "baseline_multiplier": result.data.get("baseline_multiplier"),
                "absolute_delta_cores": result.data.get("absolute_delta_cores"),
                "elevated_duration_seconds": result.data.get("elevated_duration_seconds"),
            },
            event_time=peak_at,
            rule="container_cpu_spike_v1",
        ))
    request_ratio = result.data.get("peak_request_ratio")
    if isinstance(request_ratio, (int, float)) and request_ratio >= 1.0:
        findings.append(_finding(
            result=result,
            target=target,
            finding_type="cpu_request_saturated",
            value={
                "peak_cores": peak,
                "cpu_request_cores": result.data.get("cpu_request_cores"),
                "peak_request_ratio": request_ratio,
            },
            event_time=peak_at,
            rule="cpu_request_saturated_v1",
        ))
    limit_ratio = result.data.get("peak_limit_ratio")
    if isinstance(limit_ratio, (int, float)) and limit_ratio >= 0.90:
        findings.append(_finding(
            result=result,
            target=target,
            finding_type="cpu_near_limit",
            value={
                "peak_cores": peak,
                "cpu_limit_cores": result.data.get("cpu_limit_cores"),
                "peak_limit_ratio": limit_ratio,
            },
            event_time=peak_at,
            rule="cpu_near_limit_v1",
        ))
    return findings


def parse_cpu_throttling_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status not in {ToolStatus.FOUND, ToolStatus.PARTIAL}:
        return []
    if target.resolution_quality != ResolutionQuality.HIGH:
        return []
    peak_ratio = result.data.get("period_ratio_peak")
    peak_at = _parse_time(result.data.get("peak_at"))
    if not isinstance(peak_ratio, (int, float)) or peak_ratio < 0.05 or peak_at is None:
        return []
    if not (target.window_start <= peak_at <= target.window_end):
        return []
    if result.data.get("throttling_sustained") is True:
        return [_finding(
            result=result,
            target=target,
            finding_type="cpu_throttling_sustained",
            value={
                "period_ratio_peak": peak_ratio,
                "period_ratio_average": result.data.get("period_ratio_average"),
                "sustained_duration_seconds": result.data.get("sustained_duration_seconds"),
                "seconds_metric_available": result.data.get("seconds_metric_available"),
            },
            event_time=peak_at,
            rule="cpu_throttling_sustained_v1",
        )]
    if result.data.get("throttling_observed") is True:
        return [_finding(
            result=result,
            target=target,
            finding_type="cpu_throttling_observed",
            value={
                "period_ratio_peak": peak_ratio,
                "period_ratio_average": result.data.get("period_ratio_average"),
                "observed_sample_count": result.data.get("observed_sample_count"),
                "seconds_metric_available": result.data.get("seconds_metric_available"),
            },
            event_time=peak_at,
            rule="cpu_throttling_observed_v1",
        )]
    return []
