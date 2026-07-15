from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    receiver: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    group_key: Mapped[str | None] = mapped_column(String(1024))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processing_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertInstance(Base):
    __tablename__ = "alert_instances"
    __table_args__ = (UniqueConstraint("fingerprint", "starts_at", name="uq_alert_lifecycle"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    alertname: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="warning")
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incident_group_status", "grouping_key", "status"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    grouping_key: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="warning")
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentAlert(Base):
    __tablename__ = "incident_alerts"
    __table_args__ = (UniqueConstraint("incident_id", "alert_instance_id", name="uq_incident_alert"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    alert_instance_id: Mapped[int] = mapped_column(ForeignKey("alert_instances.id", ondelete="CASCADE"), index=True)


class OutboxJob(Base):
    __tablename__ = "outbox_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"), Index("ix_outbox_claim", "status", "available_at", "priority"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    query_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_response: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))



class ModelSettings(Base):
    __tablename__ = "model_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openai-compatible")
    base_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(32))
    last_test_message: Mapped[str | None] = mapped_column(Text)

class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_change_event_content_hash"),
        Index("ix_change_event_scope_time", "namespace", "service", "occurred_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="cicd")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="deployment")
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cluster: Mapped[str | None] = mapped_column(String(255))
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(255), nullable=False)
    environment: Mapped[str | None] = mapped_column(String(128))
    workload_kind: Mapped[str | None] = mapped_column(String(64))
    workload_name: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(255))
    image: Mapped[str | None] = mapped_column(String(1000))
    actor: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TraceSettings(Base):
    __tablename__ = "trace_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="tempo")
    base_url: Mapped[str | None] = mapped_column(String(1000))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    service_tag: Mapped[str] = mapped_column(String(255), nullable=False, default="service.name")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(32))
    last_test_message: Mapped[str | None] = mapped_column(Text)



class User(Base):
    __tablename__ = "app_users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"), index=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReleaseNote(Base):
    __tablename__ = "release_notes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationAnalysisRun(Base):
    __tablename__ = "investigation_analysis_runs"
    __table_args__ = (Index("ix_investigation_runs_incident_created", "incident_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    degradation_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    target_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    engine: Mapped[str] = mapped_column(String(64), nullable=False, default="deterministic_oom_v1")
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    input_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    run_input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    run_input_schema_version: Mapped[str | None] = mapped_column(String(64))
    run_input_source_hash: Mapped[str | None] = mapped_column(String(64))
    run_input_source_mode: Mapped[str | None] = mapped_column(String(32))
    budget_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    budget_usage_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationToolExecution(Base):
    __tablename__ = "investigation_tool_executions"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "sequence_number", name="uq_investigation_tool_sequence"),
        Index("ix_investigation_tool_run", "analysis_run_id", "created_at"),
        Index(
            "ix_investigation_tool_cache_lookup",
            "analysis_run_id", "tool_name", "tool_version", "normalized_input_hash",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("investigation_analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    structured_output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    model_visible_output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_output_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    raw_artifact_uri: Mapped[str | None] = mapped_column(String(1000))
    raw_artifact_hash: Mapped[str | None] = mapped_column(String(64))
    cost_units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reused_execution_id: Mapped[int | None] = mapped_column(ForeignKey("investigation_tool_executions.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationFinding(Base):
    __tablename__ = "investigation_findings"
    __table_args__ = (Index("ix_investigation_findings_run", "analysis_run_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("investigation_analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_execution_id: Mapped[int] = mapped_column(ForeignKey("investigation_tool_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    finding_type: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_ref_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    polarity: Mapped[str] = mapped_column(String(16), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmation_rule: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationArtifact(Base):
    __tablename__ = "investigation_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/json")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compression: Mapped[str] = mapped_column(String(32), nullable=False, default="zlib")
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationReplaySnapshot(Base):
    __tablename__ = "investigation_replay_snapshots"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "snapshot_version", "validator_version", "source_hash", name="uq_investigation_replay_source"),
        Index("ix_investigation_replay_run_created", "analysis_run_id", "created_at"),
        Index("ix_investigation_replay_snapshot_hash", "snapshot_hash"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("investigation_analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_version: Mapped[str] = mapped_column(String(64), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationDiagnosisResult(Base):
    __tablename__ = "investigation_diagnosis_results"

    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("investigation_analysis_runs.id", ondelete="CASCADE"), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    fact_refs_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    hypotheses_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    missing_evidence_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    recommended_checks_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    risk_notes_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    degradation_reasons_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    validated_output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
