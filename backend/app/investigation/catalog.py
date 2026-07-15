from __future__ import annotations

from app.investigation.findings.oom import parse_oom_killed_findings
from app.investigation.registry import ToolRegistry
from app.investigation.tools.container_status import ContainerTerminationStatusTool


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ContainerTerminationStatusTool(),
        parsers=[parse_oom_killed_findings],
    )
    return registry
