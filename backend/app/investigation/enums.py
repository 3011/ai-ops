from enum import StrEnum


class ResolutionQuality(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ToolStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    TARGET_UNCERTAIN = "TARGET_UNCERTAIN"
    DENIED = "DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID_REQUEST = "INVALID_REQUEST"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class FindingPolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class FindingQuality(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InvestigationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_PARTIAL = "COMPLETED_PARTIAL"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"


class StopReason(StrEnum):
    SUFFICIENT_EVIDENCE = "SUFFICIENT_EVIDENCE"
    NO_MORE_USEFUL_TOOLS = "NO_MORE_USEFUL_TOOLS"
    AGENT_NOT_ENABLED = "AGENT_NOT_ENABLED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    TARGET_UNCERTAIN = "TARGET_UNCERTAIN"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MAX_STEPS_REACHED = "MAX_STEPS_REACHED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    NO_PROGRESS = "NO_PROGRESS"
