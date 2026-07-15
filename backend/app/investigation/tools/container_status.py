from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.investigation.contracts import TargetContext, ToolObservation
from app.investigation.enums import Completeness, ToolStatus
from app.investigation.kubernetes import KubernetesReadClient, KubernetesReadError

TOOL_NAME = "get_container_termination_status"
TOOL_VERSION = "1.0.0"
TOOL_COST_UNITS = 1
ContainerStatusObservation = ToolObservation


class ContainerTerminationStatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["current", "previous", "both"] = "both"


def _state_payload(state: dict | None) -> dict:
    state = state or {}
    for state_name in ("terminated", "waiting", "running"):
        value = state.get(state_name)
        if isinstance(value, dict):
            return {"state": state_name, **value}
    return {"state": "unknown"}


async def get_container_termination_status(
    target: TargetContext,
    *,
    client: KubernetesReadClient | None = None,
    scope: Literal["current", "previous", "both"] = "both",
) -> ToolObservation:
    """Compatibility function; persistence belongs exclusively to ToolRuntime."""
    if target.namespace not in target.allowed_namespaces:
        return ToolObservation(
            status=ToolStatus.DENIED,
            data={},
            raw_output=None,
            completeness=Completeness.UNKNOWN,
            summary="目标 namespace 不在允许范围内。",
            error_code="NAMESPACE_OUT_OF_SCOPE",
            retryable=False,
        )

    reader = client or KubernetesReadClient()
    try:
        pod = await reader.get_pod(target.namespace, target.pod_name)
    except KubernetesReadError as exc:
        return ToolObservation(
            status=exc.status,
            data={},
            raw_output=None,
            completeness=Completeness.PARTIAL if exc.status == ToolStatus.PARTIAL else Completeness.UNKNOWN,
            summary={
                ToolStatus.NOT_FOUND: "目标 Pod 已不存在。",
                ToolStatus.DENIED: "Kubernetes ContainerStatus 查询被权限策略拒绝。",
                ToolStatus.PARTIAL: "Kubernetes ContainerStatus 响应不完整。",
            }.get(exc.status, "Kubernetes ContainerStatus 查询不可用。"),
            error_code=exc.error_code,
            error_message=exc.message[:1000],
            retryable=exc.retryable,
        )
    except Exception as exc:
        return ToolObservation(
            status=ToolStatus.UNAVAILABLE,
            data={},
            raw_output=None,
            completeness=Completeness.UNKNOWN,
            summary="Kubernetes ContainerStatus 查询发生未分类错误。",
            error_code="KUBERNETES_UNCLASSIFIED_ERROR",
            error_message=f"{type(exc).__name__}: {exc}"[:1000],
            retryable=True,
        )

    metadata = pod.get("metadata") or {}
    actual_uid = str(metadata.get("uid") or "")
    actual_name = str(metadata.get("name") or "")
    if actual_uid != target.pod_uid or actual_name != target.pod_name:
        return ToolObservation(
            status=ToolStatus.TARGET_UNCERTAIN,
            data={"actual_pod_name": actual_name, "actual_pod_uid": actual_uid},
            raw_output={"metadata": {"name": actual_name, "uid": actual_uid}},
            completeness=Completeness.UNKNOWN,
            summary="Kubernetes 返回的 Pod 身份与 TargetContext 不一致。",
            error_code="POD_IDENTITY_MISMATCH",
            retryable=False,
        )

    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    matches = [row for row in statuses if str(row.get("name") or "") == target.container_name]
    if len(matches) != 1:
        return ToolObservation(
            status=ToolStatus.TARGET_UNCERTAIN,
            data={"available_containers": [row.get("name") for row in statuses]},
            raw_output={
                "metadata": {"name": actual_name, "uid": actual_uid},
                "container_statuses": statuses,
            },
            completeness=Completeness.UNKNOWN,
            summary="目标 Container 未唯一匹配已固定 Pod。",
            error_code="CONTAINER_IDENTITY_MISMATCH",
            retryable=False,
        )

    container = matches[0]
    current_state = _state_payload(container.get("state"))
    previous_state = _state_payload(container.get("lastState"))
    termination = None
    termination_source = None
    if scope in {"current", "both"} and current_state.get("state") == "terminated":
        termination = current_state
        termination_source = "current_state"
    elif scope in {"previous", "both"} and previous_state.get("state") == "terminated":
        termination = previous_state
        termination_source = "last_state"

    data = {
        "pod_name": actual_name,
        "pod_uid": actual_uid,
        "container_name": target.container_name,
        "scope": scope,
        "restart_count": int(container.get("restartCount") or 0),
        "ready": bool(container.get("ready")),
        "image": container.get("image"),
        "image_id": container.get("imageID"),
        "current_state": current_state,
        "last_state": previous_state,
        "termination": ({"source": termination_source, **termination} if termination else None),
    }
    raw_output = {
        "metadata": {
            "name": actual_name,
            "namespace": metadata.get("namespace"),
            "uid": actual_uid,
            "resource_version": metadata.get("resourceVersion"),
        },
        "container_status": container,
    }
    if termination:
        summary = (
            f"容器 {target.container_name} 最近终止原因={termination.get('reason') or '-'}，"
            f"exitCode={termination.get('exitCode')}，restartCount={data['restart_count']}。"
        )
        status = ToolStatus.FOUND
    else:
        summary = f"容器 {target.container_name} 在 scope={scope} 中未提供 terminated 状态。"
        status = ToolStatus.NOT_FOUND
    return ToolObservation(
        status=status,
        data=data,
        raw_output=raw_output,
        completeness=Completeness.COMPLETE,
        summary=summary,
    )


class ContainerTerminationStatusTool:
    name = TOOL_NAME
    version = TOOL_VERSION
    cost_units = TOOL_COST_UNITS
    arguments_model = ContainerTerminationStatusArguments

    def __init__(self, *, client: KubernetesReadClient | None = None) -> None:
        self.client = client

    async def execute(
        self,
        target: TargetContext,
        arguments: BaseModel,
    ) -> ToolObservation:
        parsed = ContainerTerminationStatusArguments.model_validate(arguments)
        return await get_container_termination_status(
            target,
            client=self.client,
            scope=parsed.scope,
        )
