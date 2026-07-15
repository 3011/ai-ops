from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.investigation.contracts import TargetContext
from app.investigation.enums import ResolutionQuality, ToolStatus
from app.investigation.kubernetes import KubernetesReadClient
from app.models import AlertInstance, Incident


@dataclass(slots=True)
class ResolutionOutcome:
    status: ToolStatus
    context: TargetContext | None
    message: str
    details: dict[str, Any]


def _labels_match_service(pod: dict[str, Any], service: str) -> bool:
    labels = (pod.get("metadata") or {}).get("labels") or {}
    values = {
        str(labels.get(key) or "")
        for key in ("app.kubernetes.io/name", "app", "k8s-app", "service", "component", "job")
    }
    name = str((pod.get("metadata") or {}).get("name") or "")
    return service in values or (service and service in name)


def select_pod_candidate(
    pods: list[dict[str, Any]],
    *,
    pod_name: str,
    pod_uid: str,
    service: str,
) -> tuple[dict[str, Any] | None, ResolutionQuality, str, str]:
    """Select one pod without ever treating ambiguity as a confirmed target."""
    if pod_uid:
        uid_matches = [pod for pod in pods if str((pod.get("metadata") or {}).get("uid") or "") == pod_uid]
        if len(uid_matches) == 1:
            candidate = uid_matches[0]
            actual_name = str((candidate.get("metadata") or {}).get("name") or "")
            if pod_name and actual_name != pod_name:
                return None, ResolutionQuality.LOW, "alert_pod_uid", "Pod UID 与告警 Pod 名不一致"
            return candidate, ResolutionQuality.HIGH, "alert_pod_uid", "通过告警 Pod UID 唯一定位"
        return None, ResolutionQuality.LOW, "alert_pod_uid", "告警 Pod UID 未唯一匹配当前对象"

    if pod_name:
        name_matches = [pod for pod in pods if str((pod.get("metadata") or {}).get("name") or "") == pod_name]
        if len(name_matches) == 1:
            return name_matches[0], ResolutionQuality.HIGH, "alert_pod_name", "通过告警 Pod 名定位并固定当前 UID"
        return None, ResolutionQuality.LOW, "alert_pod_name", "告警 Pod 名未唯一匹配"

    service_matches = [pod for pod in pods if _labels_match_service(pod, service)] if service else []
    if len(service_matches) == 1:
        return service_matches[0], ResolutionQuality.MEDIUM, "service_selector", "通过服务标签唯一定位 Pod"
    if len(service_matches) > 1:
        return None, ResolutionQuality.LOW, "service_selector", f"服务匹配到 {len(service_matches)} 个 Pod，目标不唯一"
    return None, ResolutionQuality.LOW, "service_selector", "未找到与服务匹配的 Pod"


def _container_names(pod: dict[str, Any]) -> list[str]:
    status_names = [str(item.get("name")) for item in ((pod.get("status") or {}).get("containerStatuses") or []) if item.get("name")]
    spec_names = [str(item.get("name")) for item in ((pod.get("spec") or {}).get("containers") or []) if item.get("name")]
    return list(dict.fromkeys(status_names + spec_names))


async def resolve_target_context(
    incident: Incident,
    alerts: list[AlertInstance],
    *,
    client: KubernetesReadClient | None = None,
) -> ResolutionOutcome:
    labels: dict[str, Any] = dict(incident.labels or {})
    for alert in alerts:
        labels.update(alert.labels or {})
    namespace = str(labels.get("namespace") or "")
    service = str(labels.get("service") or labels.get("app.kubernetes.io/name") or labels.get("app") or "")
    explicit_pod = str(labels.get("pod") or labels.get("pod_name") or "")
    explicit_uid = str(labels.get("pod_uid") or labels.get("uid") or "")
    explicit_container = str(labels.get("container") or "")
    if not namespace:
        return ResolutionOutcome(ToolStatus.TARGET_UNCERTAIN, None, "告警缺少 namespace", {"labels": labels})

    reader = client or KubernetesReadClient()
    try:
        pods = await reader.list_pods(namespace)
    except Exception as exc:
        return ResolutionOutcome(
            ToolStatus.UNAVAILABLE,
            None,
            f"Kubernetes Pod 列表不可用：{type(exc).__name__}",
            {"namespace": namespace, "error": str(exc)[:500]},
        )

    pod, quality, method, message = select_pod_candidate(
        pods,
        pod_name=explicit_pod,
        pod_uid=explicit_uid,
        service=service,
    )
    if pod is None:
        return ResolutionOutcome(
            ToolStatus.TARGET_UNCERTAIN,
            None,
            message,
            {"namespace": namespace, "pod": explicit_pod, "pod_uid": explicit_uid, "service": service},
        )

    metadata = pod.get("metadata") or {}
    actual_name = str(metadata.get("name") or "")
    actual_uid = str(metadata.get("uid") or "")
    creation = str(metadata.get("creationTimestamp") or "")
    if not actual_name or not actual_uid:
        return ResolutionOutcome(ToolStatus.TARGET_UNCERTAIN, None, "Pod 缺少 name 或 UID", {"metadata": metadata})

    names = _container_names(pod)
    if explicit_container:
        if explicit_container not in names:
            return ResolutionOutcome(
                ToolStatus.TARGET_UNCERTAIN,
                None,
                "告警 Container 不属于已定位 Pod",
                {"container": explicit_container, "available_containers": names, "pod_uid": actual_uid},
            )
        container_name = explicit_container
    elif len(names) == 1:
        container_name = names[0]
        if quality == ResolutionQuality.HIGH:
            quality = ResolutionQuality.MEDIUM
        message += "；Container 由单容器推断"
    else:
        return ResolutionOutcome(
            ToolStatus.TARGET_UNCERTAIN,
            None,
            "未提供 Container 且 Pod 包含多个容器",
            {"available_containers": names, "pod_uid": actual_uid},
        )

    path = [
        f"Alert labels(namespace={namespace}, pod={explicit_pod or '-'}, uid={explicit_uid or '-'}, container={explicit_container or '-'})",
        f"Pod/{actual_name} uid={actual_uid}",
    ]
    workload_kind: str | None = None
    workload_name: str | None = None
    workload_uid: str | None = None
    owners = metadata.get("ownerReferences") or []
    owner = owners[0] if owners else None
    if owner:
        owner_kind = str(owner.get("kind") or "")
        owner_name = str(owner.get("name") or "")
        owner_uid = str(owner.get("uid") or "")
        path.append(f"{owner_kind}/{owner_name} uid={owner_uid or '-'}")
        workload_kind, workload_name, workload_uid = owner_kind, owner_name, owner_uid or None
        if owner_kind == "ReplicaSet" and owner_name:
            try:
                rs = await reader.get_replicaset(namespace, owner_name)
                rs_owner = (((rs.get("metadata") or {}).get("ownerReferences") or [None])[0])
                if rs_owner and str(rs_owner.get("kind") or "") == "Deployment":
                    deployment_name = str(rs_owner.get("name") or "")
                    deployment_uid = str(rs_owner.get("uid") or "")
                    if deployment_name:
                        deployment = await reader.get_deployment(namespace, deployment_name)
                        deployment_uid = str((deployment.get("metadata") or {}).get("uid") or deployment_uid)
                    path.append(f"Deployment/{deployment_name} uid={deployment_uid or '-'}")
                    workload_kind, workload_name, workload_uid = "Deployment", deployment_name, deployment_uid or None
            except Exception:
                path.append("Deployment owner lookup unavailable")

    context = TargetContext(
        cluster_id=str(labels.get("cluster") or "default"),
        namespace=namespace,
        workload_kind=workload_kind,
        workload_name=workload_name,
        workload_uid=workload_uid,
        pod_name=actual_name,
        pod_uid=actual_uid,
        container_name=container_name,
        incident_time=incident.first_seen_at,
        window_start=incident.first_seen_at - timedelta(minutes=15),
        window_end=incident.first_seen_at + timedelta(minutes=30),
        resolution_method=method,
        resolution_path=path,
        resolution_quality=quality,
        allowed_namespaces=[namespace],
    )
    return ResolutionOutcome(
        ToolStatus.FOUND,
        context,
        message,
        {"pod_created_at": creation, "available_containers": names},
    )
