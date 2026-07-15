from __future__ import annotations

from datetime import datetime
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


class DiagnosisResultContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    fact_refs: list[str] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    degradation_reasons: list[str] = Field(default_factory=list)
    analysis_mode: Literal["deterministic_oom_v1"] = "deterministic_oom_v1"
