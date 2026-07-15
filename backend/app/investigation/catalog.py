from __future__ import annotations

from app.investigation.findings.oom import parse_oom_killed_findings
from app.investigation.findings.resources import (
    parse_cpu_throttling_findings,
    parse_cpu_usage_findings,
    parse_memory_usage_findings,
)
from app.investigation.registry import ToolRegistry
from app.investigation.tools.container_status import ContainerTerminationStatusTool
from app.investigation.tools.prometheus_resources import (
    GetCPUThrottlingTool,
    GetCPUUsageVsRequestLimitTool,
    GetMemoryUsageVsLimitTool,
)

TOOL_CATALOG_VERSION = "0.9.0-dev.1"


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ContainerTerminationStatusTool(),
        parsers=[parse_oom_killed_findings],
    )
    registry.register(
        GetMemoryUsageVsLimitTool(),
        parsers=[parse_memory_usage_findings],
    )
    registry.register(
        GetCPUUsageVsRequestLimitTool(),
        parsers=[parse_cpu_usage_findings],
    )
    registry.register(
        GetCPUThrottlingTool(),
        parsers=[parse_cpu_throttling_findings],
    )
    return registry
