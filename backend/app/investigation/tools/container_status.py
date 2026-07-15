from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.investigation.contracts import TargetContext
from app.investigation.enums import Completeness, ToolStatus
from app.investigation.kubernetes import KubernetesReadClient

TOOL_NAME = "get_container_termination_status"
TOOL_VERSION = "1.0.0"


@dataclass(slots=True)
class ContainerStatusObservation:
    status: ToolStatus
    data: dict[str, Any]
    raw_output: dict[str, Any] | None
    completeness: Completeness
    summary: str
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


def _state_payload(state: dict[str, Any] | None) -> dict[str, Any]:
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
) -> ContainerStatusObservation:
    if target.namespace not in target.allowed_namespaces:
        return ContainerStatusObservation(
            status=ToolStatus.DENIED,
            data={},
            raw_output=None,
            completeness=Completeness.UNKNOWN,
            summary="目标 namespace 不在允许范围内。",
            error_code="NAMESPACE_OUT_OF_SCOPE",
        )

    reader = client or KubernetesReadClient()
    try:
        pod = await reader.get_pod(target.namespace, target.pod_name)
    except Exception as exc:
        return ContainerStatusObservation(
            status=ToolStatus.UNAVAILABLE,
            data={},
            raw_output=None,
            completeness=Completeness.UNKNOWN,
            summary="Kubernetes ContainerStatus 查询不可用。",
            error_code="KUBERNETES_API_UNAVAILABLE",
            error_message=f"{type(exc).__name__}: {exc}"[:1000],
            retryable=True,
        )

    metadata = pod.get("metadata") or {}
    actual_uid = str(metadata.get("uid") or "")
    actual_name = str(metadata.get("name") or "")
    if actual_uid != target.pod_uid or actual_name != target.pod_name:
        return ContainerStatusObservation(
            status=ToolStatus.TARGET_UNCERTAIN,
            data={"actual_pod_name": actual_name, "actual_pod_uid": actual_uid},
            raw_output={"metadata": {"name": actual_name, "uid": actual_uid}},
            completeness=Completeness.UNKNOWN,
            summary="Kubernetes 返回的 Pod 身份与 TargetContext 不一致。",
            error_code="POD_IDENTITY_MISMATCH",
        )

    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    matches = [row for row in statuses if str(row.get("name") or "") == target.container_name]
    if len(matches) != 1:
        return ContainerStatusObservation(
            status=ToolStatus.TARGET_UNCERTAIN,
            data={"available_containers": [row.get("name") for row in statuses]},
            raw_output={
                "metadata": {"name": actual_name, "uid": actual_uid},
                "container_statuses": statuses,
            },
            completeness=Completeness.UNKNOWN,
            summary="目标 Container 未唯一匹配已固定 Pod。",
            error_code="CONTAINER_IDENTITY_MISMATCH",
        )

    container = matches[0]
    current_state = _state_payload(container.get("state"))
    last_state = _state_payload(container.get("lastState"))
    termination = None
    termination_source = None
    if current_state.get("state") == "terminated":
        termination = current_state
        termination_source = "current_state"
    elif last_state.get("state") == "terminated":
        termination = last_state
        termination_source = "last_state"

    data = {
        "pod_name": actual_name,
        "pod_uid": actual_uid,
        "container_name": target.container_name,
        "restart_count": int(container.get("restartCount") or 0),
        "ready": bool(container.get("ready")),
        "image": container.get("image"),
        "image_id": container.get("imageID"),
        "current_state": current_state,
        "last_state": last_state,
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
        summary = f"容器 {target.container_name} 当前未提供 terminated 状态。"
        status = ToolStatus.NOT_FOUND
    return ContainerStatusObservation(
        status=status,
        data=data,
        raw_output=raw_output,
        completeness=Completeness.COMPLETE,
        summary=summary,
    )
