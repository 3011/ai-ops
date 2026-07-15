from __future__ import annotations

from typing import Any, Protocol

from app.investigation.agents.contracts import (
    AgentDiagnosisOutput,
    AgentToolObservation,
    AgentToolSpec,
    InvestigationContext,
)
from app.investigation.contracts import InvestigationBudget


class AgentToolRuntime(Protocol):
    def catalog(self) -> list[AgentToolSpec]: ...

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> AgentToolObservation: ...


class InvestigationAgent(Protocol):
    async def investigate(
        self,
        context: InvestigationContext,
        tools: AgentToolRuntime,
        budget: InvestigationBudget,
    ) -> AgentDiagnosisOutput: ...
