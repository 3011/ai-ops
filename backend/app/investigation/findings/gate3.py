from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.investigation.contracts import DeterministicFinding, TargetContext, ToolResult
from app.investigation.enums import FindingPolarity, FindingQuality, ResolutionQuality, ToolStatus

PARSER_VERSION = "1.0.0"


def _time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _finding(
    result: ToolResult,
    target: TargetContext,
    finding_type: str,
    value: dict[str, object],
    *,
    rule: str,
    event_time: datetime | None = None,
    polarity: FindingPolarity = FindingPolarity.POSITIVE,
) -> DeterministicFinding:
    quality = FindingQuality.HIGH if result.status == ToolStatus.FOUND and not result.is_truncated else FindingQuality.MEDIUM
    return DeterministicFinding(
        id=f"F-{uuid4().hex}",
        finding_type=finding_type,
        subject=target.ref(),
        value=value,
        polarity=polarity,
        quality=quality,
        event_time=event_time,
        tool_execution_id=result.execution_id,
        parser_version=PARSER_VERSION,
        confirmation_rule=rule,
    )


def parse_restart_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status not in {ToolStatus.FOUND, ToolStatus.PARTIAL} or target.resolution_quality != ResolutionQuality.HIGH:
        return []
    delta = result.data.get("window_restart_delta")
    if not isinstance(delta, (int, float)):
        return []
    event_time = None
    events = result.data.get("restart_events") or []
    if events:
        event_time = _time(events[-1].get("time"))
    if delta >= 3 or result.data.get("repeated") is True:
        return [_finding(result, target, "container_repeatedly_restarted", {
            "window_restart_delta": delta,
            "current_restart_count": result.data.get("current_restart_count"),
            "restart_event_count": result.data.get("restart_event_count"),
        }, rule="container_repeatedly_restarted_v1", event_time=event_time)]
    if delta > 0:
        return [_finding(result, target, "container_restart_increased", {
            "window_restart_delta": delta,
            "current_restart_count": result.data.get("current_restart_count"),
            "restart_event_count": result.data.get("restart_event_count"),
        }, rule="container_restart_increased_v1", event_time=event_time)]
    if result.status == ToolStatus.FOUND and result.completeness.value == "complete":
        return [_finding(result, target, "container_restart_stable", {
            "window_restart_delta": 0,
            "current_restart_count": result.data.get("current_restart_count"),
        }, rule="container_restart_stable_v1", polarity=FindingPolarity.NEGATIVE)]
    return []


def parse_rollout_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status != ToolStatus.FOUND or target.resolution_quality != ResolutionQuality.HIGH:
        return []
    findings: list[DeterministicFinding] = []
    event_time = _time(result.data.get("nearest_rollout_before_incident"))
    if result.data.get("rollout_preceded_incident") is True:
        findings.append(_finding(result, target, "rollout_preceded_incident", {
            "minutes_before_incident": result.data.get("minutes_before_incident"),
            "nearest_rollout_before_incident": result.data.get("nearest_rollout_before_incident"),
            "recent_replicaset_count": result.data.get("recent_replicaset_count"),
            "recent_change_count": result.data.get("recent_change_count"),
        }, rule="rollout_preceded_incident_v1", event_time=event_time))
    if result.data.get("revision_changed") is True:
        findings.append(_finding(result, target, "revision_changed", {
            "previous_revision": result.data.get("previous_revision"),
            "current_revision": result.data.get("current_revision"),
        }, rule="revision_changed_v1", event_time=event_time))
    if result.data.get("image_changed") is True:
        findings.append(_finding(result, target, "image_changed", {
            "previous_images": result.data.get("previous_images"),
            "current_images": result.data.get("current_images"),
        }, rule="image_changed_v1", event_time=event_time))
    if result.data.get("no_recent_rollout") is True:
        findings.append(_finding(result, target, "no_recent_rollout", {
            "query_start": result.data.get("query_start"),
            "query_end": result.data.get("query_end"),
        }, rule="no_recent_rollout_v1", polarity=FindingPolarity.NEGATIVE))
    return findings


_LOG_FINDING = {
    "oom": "oom_log_observed",
    "allocation_failure": "allocation_failure_log_observed",
    "process_termination": "process_termination_log_observed",
    "runtime_error": "runtime_error_log_observed",
    "cpu_hot_loop_hint": "cpu_hot_loop_hint_log_observed",
    "gc_pressure": "gc_pressure_log_observed",
    "request_timeout": "request_timeout_log_observed",
}


def parse_log_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status not in {ToolStatus.FOUND, ToolStatus.PARTIAL} or target.resolution_quality != ResolutionQuality.HIGH:
        return []
    counts = result.data.get("category_counts") or {}
    findings = []
    for category, finding_type in _LOG_FINDING.items():
        count = counts.get(category)
        if isinstance(count, int) and count > 0:
            findings.append(_finding(result, target, finding_type, {
                "category": category,
                "matched_line_count": count,
                "untrusted_input": True,
                "prompt_injection_redacted_count": result.data.get("prompt_injection_redacted_count"),
            }, rule=f"{finding_type}_v1", event_time=target.incident_time))
    return findings


def parse_replica_cpu_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status not in {ToolStatus.FOUND, ToolStatus.PARTIAL} or target.resolution_quality != ResolutionQuality.HIGH:
        return []
    findings: list[DeterministicFinding] = []
    common = {
        "replica_count": result.data.get("replica_count"),
        "anomalous_pods": result.data.get("anomalous_pods"),
        "median_incident_cpu_cores": result.data.get("median_incident_cpu_cores"),
        "max_incident_cpu_cores": result.data.get("max_incident_cpu_cores"),
    }
    if result.data.get("single_replica_anomaly") is True:
        findings.append(_finding(result, target, "single_replica_cpu_anomaly", common,
            rule="single_replica_cpu_anomaly_v1", event_time=target.incident_time))
    if result.data.get("subset_replicas_anomaly") is True:
        findings.append(_finding(result, target, "subset_replicas_cpu_anomaly", common,
            rule="subset_replicas_cpu_anomaly_v1", event_time=target.incident_time))
    if result.data.get("all_replicas_increased") is True:
        findings.append(_finding(result, target, "all_replicas_cpu_increased", common,
            rule="all_replicas_cpu_increased_v1", event_time=target.incident_time))
    if result.data.get("new_revision_cpu_higher") is True:
        findings.append(_finding(result, target, "new_revision_cpu_higher", {
            **common,
            "latest_revision": result.data.get("latest_revision"),
            "revision_means": result.data.get("revision_means"),
        }, rule="new_revision_cpu_higher_v1", event_time=target.incident_time))
    return findings


def parse_red_findings(result: ToolResult, target: TargetContext) -> list[DeterministicFinding]:
    if result.status not in {ToolStatus.FOUND, ToolStatus.PARTIAL} or target.resolution_quality != ResolutionQuality.HIGH:
        return []
    findings: list[DeterministicFinding] = []
    signals = result.data.get("signals") or {}
    if result.data.get("request_rate_increased") is True:
        findings.append(_finding(result, target, "request_rate_increased", signals.get("request_rate") or {},
            rule="request_rate_increased_v1", event_time=target.incident_time))
    elif result.data.get("request_rate_stable") is True:
        findings.append(_finding(result, target, "request_rate_stable", signals.get("request_rate") or {},
            rule="request_rate_stable_v1", event_time=target.incident_time, polarity=FindingPolarity.NEGATIVE))
    if result.data.get("error_rate_increased") is True:
        findings.append(_finding(result, target, "error_rate_increased", signals.get("error_rate") or {},
            rule="error_rate_increased_v1", event_time=target.incident_time))
    if result.data.get("latency_increased") is True:
        findings.append(_finding(result, target, "latency_increased", signals.get("latency") or {},
            rule="latency_increased_v1", event_time=target.incident_time))
    return findings
