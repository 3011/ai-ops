from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.investigation.enums import (
    Completeness,
    FindingPolarity,
    FindingQuality,
    ResolutionQuality,
    ToolStatus,
)


class TargetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    namespace: str
    pod_name: str
    pod_uid: str
    container_name: str
    service_name: str | None = None
    workload_kind: str | None = None
    workload_name: str | None = None
    workload_uid: str | None = None


class TargetContext(TargetRef):
    model_config = ConfigDict(extra="forbid")

    incident_time: datetime
    window_start: datetime
    window_end: datetime
    resolution_method: str
    resolution_path: list[str] = Field(min_length=1)
    resolution_quality: ResolutionQuality
    allowed_namespaces: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope(self) -> "TargetContext":
        if self.namespace not in self.allowed_namespaces:
            raise ValueError("target namespace is outside allowed_namespaces")
        if self.window_end < self.window_start:
            raise ValueError("window_end must not precede window_start")
        if not self.pod_uid.strip() or not self.container_name.strip():
            raise ValueError("pod_uid and container_name are required")
        return self

    def ref(self) -> TargetRef:
        return TargetRef(**self.model_dump(include=set(TargetRef.model_fields)))

    def identity_payload(self) -> dict[str, Any]:
        """Stable identity included in every cache key."""
        return {
            "cluster_id": self.cluster_id,
            "namespace": self.namespace,
            "pod_name": self.pod_name,
            "pod_uid": self.pod_uid,
            "container_name": self.container_name,
            "service_name": self.service_name,
            "workload_uid": self.workload_uid,
            "resolution_method": self.resolution_method,
            "resolution_quality": self.resolution_quality.value,
            "allowed_namespaces": sorted(self.allowed_namespaces),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
        }


class InvestigationBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=8, ge=1, le=100)
    max_tool_calls: int = Field(default=12, ge=1, le=200)
    max_total_cost_units: int = Field(default=20, ge=1, le=10_000)
    max_same_tool_calls: int = Field(default=3, ge=1, le=50)
    max_no_progress_rounds: int = Field(default=2, ge=1, le=20)
    deadline_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=2))


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps_used: int = 0
    tool_calls_used: int = 0
    total_cost_units_used: int = 0
    tool_call_counts: dict[str, int] = Field(default_factory=dict)
    no_progress_rounds: int = 0
    last_finding_step: int | None = None
    remaining_steps: int
    remaining_tool_calls: int
    remaining_cost_units: int
    deadline_at: datetime


class ToolObservation(BaseModel):
    """Tool-owned result before persistence, artifact handling and findings."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    raw_output: dict[str, Any] | list[Any] | None = None
    completeness: Completeness
    summary: str
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    status: ToolStatus
    target: TargetRef
    data: dict[str, Any] = Field(default_factory=dict)
    finding_ids: list[str] = Field(default_factory=list)
    completeness: Completeness
    model_visible_summary: str
    tool_name: str
    tool_version: str
    cost_units: int = Field(ge=0)
    started_at: datetime
    completed_at: datetime
    error_code: str | None = None
    retryable: bool = False
    reused_execution_id: str | None = None
    is_truncated: bool = False
    raw_artifact_uri: str | None = None
    raw_artifact_hash: str | None = None


class DeterministicFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    finding_type: str
    subject: TargetRef
    value: dict[str, Any]
    polarity: FindingPolarity
    quality: FindingQuality
    event_time: datetime | None = None
    tool_execution_id: str
    parser_version: str
    confirmation_rule: str | None = None


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    sha256: str
    size_bytes: int
    content_type: str = "application/json"


class DiagnosisResultContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    fact_refs: list[str] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    degradation_reasons: list[str] = Field(default_factory=list)
    analysis_mode: Literal["deterministic_oom_v1", "deterministic_oom_v2", "deterministic_cpu_v1"] = "deterministic_oom_v1"
