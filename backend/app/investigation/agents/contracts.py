from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.investigation.contracts import BudgetSnapshot, TargetContext

InvestigationMode = Literal["oom", "cpu"]
AgentRunMode = Literal["offline_replay", "realtime_shadow"]
HypothesisSupport = Literal[
    "highly_supported",
    "partially_supported",
    "insufficient_evidence",
    "contradicted",
]
AgentValidationStatus = Literal["VALID", "VALID_WITH_WARNINGS", "INVALID"]

_PROBABILITY_PERCENTAGE = re.compile(
    r"(?:概率|可能性|置信度|把握|probability|likelihood|confidence|chance)"
    r"\s*(?:为|是|is|[:：=])?\s*\d+(?:\.\d+)?\s*%"
    r"|\d+(?:\.\d+)?\s*%\s*(?:的)?\s*"
    r"(?:概率|可能性|置信度|把握|probability|likelihood|confidence|chance)"
    r"|(?:根因|root\s+cause)\s*(?:概率|置信度|confidence|score|[:：=])"
    r"\s*\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)


def reject_probability_percentage(value: str) -> str:
    if _PROBABILITY_PERCENTAGE.search(value):
        raise ValueError("probability percentage estimates are forbidden")
    return value


class AgentToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    cost_units: int = Field(ge=0)
    arguments_schema: dict[str, Any] = Field(default_factory=dict)


class InvestigationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_run_id: int
    parent_run_id: int
    incident_summary: dict[str, Any]
    target_context: TargetContext | None
    initial_finding_ids: list[str] = Field(default_factory=list)
    initial_findings: list[dict[str, Any]] = Field(default_factory=list)
    available_tools: list[AgentToolSpec] = Field(default_factory=list)
    budget_snapshot: BudgetSnapshot
    investigation_mode: InvestigationMode
    run_mode: AgentRunMode
    source_snapshot_id: str | None = None
    source_snapshot_hash: str | None = None


class AgentHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^H-[A-Za-z0-9_-]{1,48}$")
    statement: str = Field(min_length=1, max_length=800)
    support_level: HypothesisSupport
    fact_refs: list[str] = Field(default_factory=list)
    contradicting_fact_refs: list[str] = Field(default_factory=list)
    counterevidence_check: str = Field(default="", max_length=1200)
    rationale: str = Field(min_length=1, max_length=1600)

    @field_validator("statement", "rationale", "counterevidence_check")
    @classmethod
    def reject_forbidden_certainty(cls, value: str) -> str:
        lowered = value.casefold()
        forbidden = (
            "root_cause_confirmed", "root cause confirmed", "confirmed root cause",
            "根因已确认", "确认根因", "概率为", "probability is",
        )
        if any(token in lowered for token in forbidden):
            raise ValueError("forbidden certainty language")
        return reject_probability_percentage(value)

    @model_validator(mode="after")
    def validate_support(self) -> "AgentHypothesis":
        if self.support_level == "contradicted" and not self.contradicting_fact_refs:
            raise ValueError("contradicted hypotheses require contradicting_fact_refs")
        if self.support_level == "highly_supported" and not self.fact_refs:
            raise ValueError("highly_supported hypotheses require fact_refs")
        return self


class AgentDiagnosisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    fact_refs: list[str] = Field(default_factory=list)
    hypotheses: list[AgentHypothesis] = Field(default_factory=list, max_length=12)
    missing_evidence: list[str] = Field(default_factory=list, max_length=30)
    recommended_checks: list[str] = Field(default_factory=list, max_length=30)
    risk_notes: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("summary")
    @classmethod
    def reject_summary_certainty(cls, value: str) -> str:
        lowered = value.casefold()
        if any(token in lowered for token in (
            "root_cause_confirmed", "root cause confirmed", "confirmed root cause",
            "根因已确认", "确认根因",
        )):
            raise ValueError("forbidden certainty language")
        return reject_probability_percentage(value)


class AgentToolObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    tool_name: str
    tool_version: str
    status: str
    summary: str
    finding_ids: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    cost_units: int = Field(ge=0)
    reused_execution_id: str | None = None


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def reject_free_queries(self) -> "AgentToolCall":
        forbidden = {"promql", "logql", "query", "query_text", "expression", "expr"}

        def walk(value: Any) -> bool:
            if isinstance(value, dict):
                return any(str(key).casefold() in forbidden or walk(item) for key, item in value.items())
            if isinstance(value, list):
                return any(walk(item) for item in value)
            return False

        if walk(self.arguments):
            raise ValueError("free PromQL/LogQL/query expressions are forbidden")
        return self


class AgentTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["tool", "final"]
    tool_call: AgentToolCall | None = None
    diagnosis: AgentDiagnosisOutput | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "AgentTurn":
        if self.action == "tool" and self.tool_call is None:
            raise ValueError("tool action requires tool_call")
        if self.action == "final" and self.diagnosis is None:
            raise ValueError("final action requires diagnosis")
        if self.action == "tool" and self.diagnosis is not None:
            raise ValueError("tool action must not include diagnosis")
        return self


class AgentValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["error", "warning"]
    path: str
    message: str


class AgentValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentValidationStatus
    validator_version: str
    checks: dict[str, bool] = Field(default_factory=dict)
    errors: list[AgentValidationIssue] = Field(default_factory=list)
    warnings: list[AgentValidationIssue] = Field(default_factory=list)
