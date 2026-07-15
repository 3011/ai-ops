from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.artifacts import redact_sensitive
from app.investigation.catalog import TOOL_CATALOG_VERSION, build_default_registry
from app.investigation.run_input import incident_context_from_run_input, resolve_run_input
from app.investigation.tool_runtime import stable_hash
from app.models import (
    Incident,
    InvestigationAnalysisRun,
    InvestigationDiagnosisResult,
    InvestigationFinding,
    InvestigationReplaySnapshot,
    InvestigationToolExecution,
)

SNAPSHOT_SCHEMA_VERSION = "1.1.0"
VALIDATOR_VERSION = "1.1.0"
MAX_SNAPSHOT_BYTES = 512 * 1024

ValidationSeverity = Literal["error", "warning"]
ValidationStatus = Literal["VALID", "VALID_WITH_WARNINGS", "INVALID"]


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: ValidationSeverity
    path: str
    message: str


class ReplayValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    validator_version: str = VALIDATOR_VERSION
    checks: dict[str, bool] = Field(default_factory=dict)
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    legacy_contract_violation: bool = False


TOOL_FINDING_TYPES: dict[str, set[str]] = {
    "get_container_termination_status": {"container_oom_killed"},
    "get_memory_usage_vs_limit": {"memory_limit_reached", "memory_near_limit", "memory_usage_increased"},
    "get_cpu_usage_vs_request_limit": {"container_cpu_spike", "cpu_request_saturated", "cpu_near_limit"},
    "get_cpu_throttling": {"cpu_throttling_sustained", "cpu_throttling_observed"},
    "get_container_restart_history": {
        "container_repeatedly_restarted", "container_restart_increased", "container_restart_stable",
    },
    "get_recent_rollouts": {"rollout_preceded_incident", "revision_changed", "image_changed", "no_recent_rollout"},
    "search_container_logs": {
        "oom_log_observed", "allocation_failure_log_observed", "process_termination_log_observed",
        "runtime_error_log_observed", "cpu_hot_loop_hint_log_observed", "gc_pressure_log_observed",
        "request_timeout_log_observed",
    },
    "compare_cpu_across_replicas": {
        "single_replica_cpu_anomaly", "subset_replicas_cpu_anomaly", "all_replicas_cpu_increased",
        "new_revision_cpu_higher",
    },
    "get_application_red_metrics": {
        "request_rate_increased", "request_rate_stable", "error_rate_increased", "latency_increased",
    },
}

TARGET_IDENTITY_FIELDS = (
    "cluster_id", "namespace", "pod_name", "pod_uid", "container_name",
    "service_name", "workload_kind", "workload_name", "workload_uid",
)
FORBIDDEN_SNAPSHOT_KEYS = {"raw_output", "raw_output_json", "raw_response", "artifact_content"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode())


def _identity(value: dict[str, Any] | None) -> dict[str, Any]:
    value = value or {}
    return {key: value.get(key) for key in TARGET_IDENTITY_FIELDS}


def _contains_forbidden_key(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in FORBIDDEN_SNAPSHOT_KEYS:
                found.append(child)
            found.extend(_contains_forbidden_key(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_contains_forbidden_key(item, f"{path}[{index}]"))
    return found


class ResultValidator:
    def __init__(self) -> None:
        self.registry = build_default_registry()

    def validate(self, payload: dict[str, Any], *, expected_input_hash: str | None) -> ReplayValidationReport:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        checks: dict[str, bool] = {}

        def issue(code: str, path: str, message: str, severity: ValidationSeverity = "error") -> None:
            item = ValidationIssue(code=code, severity=severity, path=path, message=message)
            (errors if severity == "error" else warnings).append(item)

        checks["schema_version"] = payload.get("schema_version") == SNAPSHOT_SCHEMA_VERSION
        if not checks["schema_version"]:
            issue("SNAPSHOT_SCHEMA_UNSUPPORTED", "$.schema_version", "Snapshot schema version 不受支持。")

        forbidden = _contains_forbidden_key(payload)
        checks["no_raw_payload"] = not forbidden
        for path in forbidden:
            issue("RAW_DATA_EXPOSED", path, "重放快照不得包含原始数据源响应。")

        reconstructed = payload.get("run_input") or {}
        computed_input_hash = stable_hash(reconstructed)
        declared_input_hash = str(payload.get("run_input_source_hash") or computed_input_hash)
        compatibility_hash = str(payload.get("run_input_compatibility_hash") or declared_input_hash)
        checks["run_input_payload_hash"] = computed_input_hash == declared_input_hash
        if not checks["run_input_payload_hash"]:
            issue("RUN_INPUT_PAYLOAD_HASH_MISMATCH", "$.run_input_source_hash", "Run input 正文与声明 Hash 不一致。")
        source_mode = str(payload.get("run_input_source_mode") or "legacy_incomplete")
        checks["run_input_source_mode"] = source_mode in {
            "native_frozen", "historical_reconstructed", "legacy_incomplete"
        }
        if not checks["run_input_source_mode"]:
            issue("RUN_INPUT_SOURCE_MODE_INVALID", "$.run_input_source_mode", "Run input source mode 不受支持。")
        checks["input_snapshot_hash"] = not expected_input_hash or compatibility_hash == expected_input_hash
        if expected_input_hash and compatibility_hash != expected_input_hash:
            if source_mode == "legacy_incomplete":
                issue(
                    "LEGACY_INPUT_INCOMPLETE",
                    "$.run_input",
                    "旧 Run 缺少原生冻结输入，现有历史字段不足以精确恢复原始输入。",
                    "warning",
                )
            else:
                issue("INPUT_SNAPSHOT_HASH_MISMATCH", "$.run_input", "冻结或历史重建输入与 AnalysisRun 输入 Hash 不一致。")
        if source_mode == "legacy_incomplete" and (not expected_input_hash or compatibility_hash == expected_input_hash):
            issue(
                "LEGACY_INPUT_INCOMPLETE",
                "$.run_input",
                "旧 Run 缺少原生冻结输入，只能提供有限历史重建。",
                "warning",
            )
        if source_mode == "native_frozen" and not payload.get("run_input_schema_version"):
            issue("FROZEN_INPUT_SCHEMA_MISSING", "$.run_input_schema_version", "原生冻结输入缺少 Schema Version。")
        run_metadata = payload.get("analysis_run") or {}
        if source_mode == "native_frozen":
            if run_metadata.get("run_input_source_hash") != declared_input_hash:
                issue("FROZEN_INPUT_HASH_METADATA_MISMATCH", "$.analysis_run.run_input_source_hash", "AnalysisRun 冻结 Hash 与 Snapshot 输入不一致。")
            if run_metadata.get("run_input_source_mode") != "native_frozen":
                issue("FROZEN_INPUT_MODE_METADATA_MISMATCH", "$.analysis_run.run_input_source_mode", "AnalysisRun 未标记为 native_frozen。")

        run = payload.get("analysis_run") or {}
        target = run.get("target_context") or {}
        target_quality = target.get("resolution_quality")
        resolved_target = bool(target.get("pod_uid") and target.get("container_name"))
        if not resolved_target:
            issue("TARGET_UNRESOLVED", "$.analysis_run.target_context", "该 Run 没有可重放的对象级 TargetContext。", "warning")
        elif target.get("namespace") not in (target.get("allowed_namespaces") or []):
            issue("TARGET_NAMESPACE_OUT_OF_SCOPE", "$.analysis_run.target_context.namespace", "Target namespace 不在允许作用域内。")

        tools = payload.get("tool_executions") or []
        execution_ids = [str(item.get("execution_id")) for item in tools]
        checks["unique_tool_executions"] = len(execution_ids) == len(set(execution_ids))
        if not checks["unique_tool_executions"]:
            issue("DUPLICATE_TOOL_EXECUTION", "$.tool_executions", "ToolExecution ID 重复。")
        sequences = [item.get("sequence_number") for item in tools]
        checks["unique_tool_sequence"] = len(sequences) == len(set(sequences))
        if not checks["unique_tool_sequence"]:
            issue("DUPLICATE_TOOL_SEQUENCE", "$.tool_executions", "工具执行顺序号重复。")

        findings = payload.get("findings") or []
        finding_by_id = {str(item.get("id")): item for item in findings}
        checks["unique_findings"] = len(findings) == len(finding_by_id)
        if not checks["unique_findings"]:
            issue("DUPLICATE_FINDING_ID", "$.findings", "Finding ID 重复。")

        tool_by_id = {str(item.get("execution_id")): item for item in tools}
        for index, tool in enumerate(tools):
            path = f"$.tool_executions[{index}]"
            name = str((tool.get("tool") or {}).get("name") or "")
            version = str((tool.get("tool") or {}).get("version") or "")
            registered = self.registry.get(name)
            if registered is None:
                issue("UNKNOWN_TOOL", f"{path}.tool.name", f"工具 {name} 未注册。")
            elif registered.version != version:
                issue("TOOL_VERSION_DRIFT", f"{path}.tool.version", f"快照工具版本 {version} 与当前可信目录 {registered.version} 不一致。", "warning")

            input_json = tool.get("input") or {}
            input_target = input_json.get("target") or {}
            if resolved_target and _identity(input_target) != _identity(target):
                issue("TOOL_TARGET_MISMATCH", f"{path}.input.target", "工具输入目标与 AnalysisRun TargetContext 不一致。")
            scope = input_json.get("scope") or {}
            allowed = scope.get("allowed_namespaces") or []
            if input_target.get("namespace") and input_target.get("namespace") not in allowed:
                issue("TOOL_SCOPE_MISMATCH", f"{path}.input.scope", "工具 namespace 不在执行时允许作用域中。")

            output = tool.get("result") or {}
            output_ids = [str(value) for value in (output.get("finding_ids") or [])]
            persisted_ids = [
                str(item.get("id")) for item in findings
                if str(item.get("tool_execution_id")) == str(tool.get("execution_id"))
            ]
            if set(output_ids) != set(persisted_ids):
                issue("TOOL_FINDING_REF_MISMATCH", f"{path}.result.finding_ids", "ToolResult Finding 引用与持久化 Finding 不一致。")
            if output.get("status") != tool.get("status"):
                issue("TOOL_STATUS_MISMATCH", f"{path}.result.status", "模型可见状态与 ToolExecution 状态不一致。")

        for index, finding in enumerate(findings):
            path = f"$.findings[{index}]"
            execution_id = str(finding.get("tool_execution_id"))
            tool = tool_by_id.get(execution_id)
            if tool is None:
                issue("FINDING_TOOL_MISSING", f"{path}.tool_execution_id", "Finding 引用了不存在的 ToolExecution。")
                continue
            tool_name = str((tool.get("tool") or {}).get("name") or "")
            finding_type = str(finding.get("finding_type") or "")
            if finding_type not in TOOL_FINDING_TYPES.get(tool_name, set()):
                issue("FINDING_TYPE_NOT_ALLOWED", f"{path}.finding_type", f"工具 {tool_name} 不允许生成 {finding_type}。")
            if resolved_target and _identity(finding.get("subject") or {}) != _identity(target):
                issue("FINDING_SUBJECT_MISMATCH", f"{path}.subject", "Finding subject 与 TargetContext 不一致。")
            if target_quality != "high":
                issue("FINDING_WITH_UNTRUSTED_TARGET", path, "非 high 定位质量不得生成对象级 Finding。")
            if finding_type.endswith("_log_observed") and (finding.get("value") or {}).get("untrusted_input") is not True:
                issue("LOG_FINDING_NOT_MARKED_UNTRUSTED", f"{path}.value.untrusted_input", "日志 Finding 必须标记 untrusted_input=true。")
            if finding_type == "container_oom_killed" and finding.get("confirmation_rule") != "container_oom_killed_v1":
                issue("OOM_RULE_INVALID", f"{path}.confirmation_rule", "OOMKilled 必须由 container_oom_killed_v1 确认。")
            if (tool.get("result") or {}).get("is_truncated") and finding.get("quality") == "high":
                issue("TRUNCATED_SOURCE_HIGH_QUALITY_FINDING", f"{path}.quality", "截断来源生成了 high 质量 Finding。", "warning")

        diagnosis = payload.get("diagnosis") or {}
        fact_refs = [str(value) for value in (diagnosis.get("fact_refs") or [])]
        finding_ids = set(finding_by_id)
        checks["diagnosis_refs_exist"] = set(fact_refs).issubset(finding_ids)
        if not checks["diagnosis_refs_exist"]:
            missing = sorted(set(fact_refs) - finding_ids)
            issue("DIAGNOSIS_DANGLING_FACT_REF", "$.diagnosis.fact_refs", f"Diagnosis 引用了不存在的 Finding：{missing}")
        checks["diagnosis_refs_complete"] = set(fact_refs) == finding_ids
        if not checks["diagnosis_refs_complete"]:
            issue("DIAGNOSIS_FACT_SET_MISMATCH", "$.diagnosis.fact_refs", "Diagnosis fact_refs 未完整覆盖持久化 Finding。")
        if str(diagnosis.get("analysis_mode") or "").startswith("deterministic_") and diagnosis.get("hypotheses"):
            issue("DETERMINISTIC_HYPOTHESIS_NOT_ALLOWED", "$.diagnosis.hypotheses", "确定性模式不得写入模型假设。")

        size = _json_size(payload)
        checks["snapshot_size"] = size <= MAX_SNAPSHOT_BYTES
        if size > MAX_SNAPSHOT_BYTES:
            issue("SNAPSHOT_TOO_LARGE", "$", f"模型可见快照 {size} bytes 超过 {MAX_SNAPSHOT_BYTES} bytes 上限。")

        status: ValidationStatus = "INVALID" if errors else "VALID_WITH_WARNINGS" if warnings else "VALID"
        legacy_contract_violation = any(item.code in {
            "TOOL_FINDING_REF_MISMATCH", "DIAGNOSIS_DANGLING_FACT_REF", "DIAGNOSIS_FACT_SET_MISMATCH"
        } for item in errors)
        return ReplayValidationReport(
            status=status,
            checks=checks,
            errors=errors,
            warnings=warnings,
            legacy_contract_violation=legacy_contract_violation,
        )


async def build_replay_payload(session: AsyncSession, analysis_run_id: int) -> tuple[dict[str, Any], str | None]:
    run = await session.get(InvestigationAnalysisRun, analysis_run_id)
    if run is None:
        raise LookupError(f"analysis run not found: {analysis_run_id}")
    incident = await session.get(Incident, run.incident_id)
    if incident is None:
        raise LookupError(f"incident not found: {run.incident_id}")
    resolved_input = await resolve_run_input(session, run, incident)
    tools = list((await session.scalars(
        select(InvestigationToolExecution)
        .where(InvestigationToolExecution.analysis_run_id == run.id)
        .order_by(InvestigationToolExecution.sequence_number, InvestigationToolExecution.id)
    )).all())
    findings = list((await session.scalars(
        select(InvestigationFinding)
        .where(InvestigationFinding.analysis_run_id == run.id)
        .order_by(InvestigationFinding.created_at, InvestigationFinding.id)
    )).all())
    diagnosis = await session.get(InvestigationDiagnosisResult, run.id)
    run_input = resolved_input.payload
    target_context = redact_sensitive(run.target_context_json or {})
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "tool_catalog_version": TOOL_CATALOG_VERSION,
        "raw_data_included": False,
        "run_input": redact_sensitive(run_input),
        "run_input_schema_version": resolved_input.schema_version,
        "run_input_source_hash": resolved_input.source_hash,
        "run_input_compatibility_hash": resolved_input.compatibility_hash,
        "run_input_source_mode": resolved_input.source_mode,
        "incident_context": redact_sensitive(incident_context_from_run_input(run_input)),
        "analysis_run": {
            "id": run.id,
            "incident_id": run.incident_id,
            "status": run.status,
            "stop_reason": run.stop_reason,
            "degradation_reasons": run.degradation_reasons or [],
            "engine": run.engine,
            "engine_version": run.engine_version,
            "input_snapshot_hash": run.input_snapshot_hash,
            "run_input_schema_version": run.run_input_schema_version,
            "run_input_source_hash": run.run_input_source_hash,
            "run_input_source_mode": run.run_input_source_mode,
            "target_context": target_context,
            "budget": run.budget_json or {},
            "budget_usage": run.budget_usage_json or {},
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        },
        "tool_executions": [{
            "execution_id": str(tool.id),
            "sequence_number": tool.sequence_number,
            "tool": {"name": tool.tool_name, "version": tool.tool_version},
            "status": tool.status,
            "input": redact_sensitive(tool.input_json or {}),
            "result": {
                **redact_sensitive(tool.model_visible_output_json or {}),
                "structured_data": redact_sensitive(tool.structured_output_json or {}),
                "cost_units": tool.cost_units,
                "retryable": tool.retryable,
                "reused_execution_id": str(tool.reused_execution_id) if tool.reused_execution_id else None,
                "raw_artifact_uri": tool.raw_artifact_uri,
                "raw_artifact_hash": tool.raw_artifact_hash,
            },
            "started_at": tool.started_at.isoformat(),
            "completed_at": tool.completed_at.isoformat(),
        } for tool in tools],
        "findings": [{
            "id": finding.id,
            "finding_type": finding.finding_type,
            "subject": redact_sensitive(finding.subject_ref_json or {}),
            "value": redact_sensitive(finding.value_json or {}),
            "polarity": finding.polarity,
            "quality": finding.quality,
            "event_time": finding.event_time.isoformat() if finding.event_time else None,
            "tool_execution_id": str(finding.tool_execution_id),
            "parser_version": finding.parser_version,
            "confirmation_rule": finding.confirmation_rule,
        } for finding in findings],
        "diagnosis": ({
            "summary": diagnosis.summary,
            "fact_refs": diagnosis.fact_refs_json or [],
            "hypotheses": diagnosis.hypotheses_json or [],
            "missing_evidence": diagnosis.missing_evidence_json or [],
            "recommended_checks": diagnosis.recommended_checks_json or [],
            "risk_notes": diagnosis.risk_notes_json or [],
            "degradation_reasons": diagnosis.degradation_reasons_json or [],
            "analysis_mode": (diagnosis.validated_output_json or {}).get("analysis_mode"),
            "validated_output": redact_sensitive(diagnosis.validated_output_json or {}),
        } if diagnosis else {}),
    }
    return payload, resolved_input.expected_hash


async def create_replay_snapshot(session: AsyncSession, analysis_run_id: int) -> InvestigationReplaySnapshot:
    payload, expected_input_hash = await build_replay_payload(session, analysis_run_id)
    source_hash = stable_hash(payload)
    existing = await session.scalar(
        select(InvestigationReplaySnapshot).where(
            InvestigationReplaySnapshot.analysis_run_id == analysis_run_id,
            InvestigationReplaySnapshot.snapshot_version == SNAPSHOT_SCHEMA_VERSION,
            InvestigationReplaySnapshot.validator_version == VALIDATOR_VERSION,
            InvestigationReplaySnapshot.source_hash == source_hash,
        )
    )
    if existing is not None:
        return existing
    report = ResultValidator().validate(payload, expected_input_hash=expected_input_hash)
    row = InvestigationReplaySnapshot(
        id=f"R-{uuid4().hex}",
        analysis_run_id=analysis_run_id,
        snapshot_version=SNAPSHOT_SCHEMA_VERSION,
        validator_version=VALIDATOR_VERSION,
        source_hash=source_hash,
        snapshot_hash=stable_hash(payload),
        snapshot_json=payload,
        validation_status=report.status,
        validation_report_json=report.model_dump(mode="json"),
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def backfill_replay_snapshots(
    session: AsyncSession,
    *,
    limit: int = 500,
    only_missing: bool = True,
) -> dict[str, Any]:
    statement = (
        select(InvestigationAnalysisRun.id)
        .where(InvestigationAnalysisRun.status.in_(["COMPLETED", "COMPLETED_PARTIAL", "INCONCLUSIVE"]))
        .order_by(InvestigationAnalysisRun.id)
        .limit(max(1, min(limit, 5000)))
    )
    if only_missing:
        statement = statement.where(~InvestigationAnalysisRun.id.in_(
            select(InvestigationReplaySnapshot.analysis_run_id)
        ))
    run_ids = list((await session.scalars(statement)).all())
    created: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for run_id in run_ids:
        try:
            async with session.begin_nested():
                row = await create_replay_snapshot(session, int(run_id))
            created.append({
                "analysis_run_id": int(run_id),
                "snapshot_id": row.id,
                "validation_status": row.validation_status,
                "snapshot_hash": row.snapshot_hash,
            })
        except Exception as exc:
            failures.append({
                "analysis_run_id": int(run_id),
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
    await session.flush()
    return {
        "selected": len(run_ids),
        "created": len(created),
        "failed": len(failures),
        "results": created,
        "failures": failures,
    }


def replay_snapshot_payload(row: InvestigationReplaySnapshot, *, include_snapshot: bool = False) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "analysis_run_id": row.analysis_run_id,
        "snapshot_version": row.snapshot_version,
        "validator_version": row.validator_version,
        "source_hash": row.source_hash,
        "snapshot_hash": row.snapshot_hash,
        "validation_status": row.validation_status,
        "validation_report": row.validation_report_json or {},
        "run_input_source_mode": (row.snapshot_json or {}).get("run_input_source_mode"),
        "run_input_schema_version": (row.snapshot_json or {}).get("run_input_schema_version"),
        "run_input_source_hash": (row.snapshot_json or {}).get("run_input_source_hash"),
        "created_at": row.created_at,
    }
    if include_snapshot:
        payload["snapshot"] = row.snapshot_json
    return payload
