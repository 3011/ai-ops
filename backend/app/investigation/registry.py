from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

from app.investigation.contracts import DeterministicFinding, TargetContext, ToolObservation, ToolResult


class TrustedTool(Protocol):
    name: str
    version: str
    cost_units: int
    arguments_model: type[BaseModel]

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation: ...


FindingParser = Callable[[ToolResult, TargetContext], list[DeterministicFinding]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, TrustedTool] = {}
        self._parsers: dict[str, list[FindingParser]] = {}

    def register(self, tool: TrustedTool, *, parsers: list[FindingParser] | None = None) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        self._parsers[tool.name] = list(parsers or [])

    def get(self, name: str) -> TrustedTool | None:
        return self._tools.get(name)

    def parsers_for(self, name: str) -> list[FindingParser]:
        return list(self._parsers.get(name, []))

    def catalog(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "version": tool.version,
                "cost_units": tool.cost_units,
                "arguments_schema": tool.arguments_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]
