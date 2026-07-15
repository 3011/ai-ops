from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.investigation.contracts import BudgetSnapshot, InvestigationBudget
from app.investigation.enums import StopReason


@dataclass(slots=True)
class BudgetDecision:
    allowed: bool
    error_code: str | None = None
    stop_reason: StopReason | None = None
    message: str | None = None


@dataclass(slots=True)
class BudgetLedger:
    budget: InvestigationBudget
    steps_used: int = 0
    tool_calls_used: int = 0
    total_cost_units_used: int = 0
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    no_progress_rounds: int = 0
    last_finding_step: int | None = None

    def reserve(self, tool_name: str, cost_units: int) -> BudgetDecision:
        """Reserve before any external access. Cache hits reserve zero cost."""
        now = datetime.now(UTC)
        if now >= self.budget.deadline_at:
            return BudgetDecision(False, "DEADLINE_EXCEEDED", StopReason.DEADLINE_EXCEEDED, "调查截止时间已到。")
        if self.steps_used >= self.budget.max_steps:
            return BudgetDecision(False, "MAX_STEPS_REACHED", StopReason.MAX_STEPS_REACHED, "调查步骤预算已耗尽。")
        if self.tool_calls_used >= self.budget.max_tool_calls:
            return BudgetDecision(False, "MAX_TOOL_CALLS_REACHED", StopReason.BUDGET_EXHAUSTED, "工具调用预算已耗尽。")
        same_count = self.tool_call_counts.get(tool_name, 0)
        if same_count >= self.budget.max_same_tool_calls:
            return BudgetDecision(False, "MAX_SAME_TOOL_CALLS_REACHED", StopReason.BUDGET_EXHAUSTED, "同一工具调用预算已耗尽。")
        if self.total_cost_units_used + cost_units > self.budget.max_total_cost_units:
            return BudgetDecision(False, "COST_BUDGET_EXHAUSTED", StopReason.BUDGET_EXHAUSTED, "调查成本预算不足。")
        if self.no_progress_rounds >= self.budget.max_no_progress_rounds:
            return BudgetDecision(False, "NO_PROGRESS", StopReason.NO_PROGRESS, "连续调查未产生新 Finding。")

        self.steps_used += 1
        self.tool_calls_used += 1
        self.total_cost_units_used += cost_units
        self.tool_call_counts[tool_name] = same_count + 1
        return BudgetDecision(True)

    def record_findings(self, finding_count: int) -> None:
        if finding_count > 0:
            self.last_finding_step = self.steps_used
            self.no_progress_rounds = 0
        else:
            self.no_progress_rounds += 1

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            steps_used=self.steps_used,
            tool_calls_used=self.tool_calls_used,
            total_cost_units_used=self.total_cost_units_used,
            tool_call_counts=dict(self.tool_call_counts),
            no_progress_rounds=self.no_progress_rounds,
            last_finding_step=self.last_finding_step,
            remaining_steps=max(0, self.budget.max_steps - self.steps_used),
            remaining_tool_calls=max(0, self.budget.max_tool_calls - self.tool_calls_used),
            remaining_cost_units=max(0, self.budget.max_total_cost_units - self.total_cost_units_used),
            deadline_at=self.budget.deadline_at,
        )
