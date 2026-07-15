from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import math
import re
import statistics
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db import SessionLocal
from app.investigation.contracts import TargetContext, ToolObservation
from app.investigation.enums import Completeness, ResolutionQuality, ToolStatus
from app.investigation.kubernetes import KubernetesReadClient, KubernetesReadError
from app.investigation.loki import LokiReadClient, LokiReadError
from app.investigation.prometheus import PrometheusReadClient, PrometheusReadError, escape_promql_label, target_scope_selector
from app.investigation.tools.prometheus_resources import _coverage, _effective_end, _matrix_points
from app.models import ChangeEvent


def _controller_owner(obj: dict[str, Any]) -> dict[str, Any] | None:
    owners = list((obj.get("metadata") or {}).get("ownerReferences") or [])
    return next((item for item in owners if item.get("controller") is True), owners[0] if owners else None)


def _selector_string(selector: dict[str, Any]) -> str | None:
    labels = (selector or {}).get("matchLabels") or {}
    if not labels:
        return None
    parts = []
    for key, value in sorted(labels.items()):
        if not re.fullmatch(r"[A-Za-z0-9_.\-/]+", str(key)) or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", str(value)):
            return None
        parts.append(f"{key}={value}")
    return ",".join(parts)


def _revision(obj: dict[str, Any]) -> int | None:
    value = str(((obj.get("metadata") or {}).get("annotations") or {}).get("deployment.kubernetes.io/revision") or "")
    try:
        return int(value)
    except ValueError:
        return None


def _images(obj: dict[str, Any]) -> list[str]:
    containers = (((obj.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    return sorted({str(item.get("image")) for item in containers if item.get("image")})


def _iso(value: Any) -> str | None:
    if not value:
        return None
    return str(value)


def _prom_error(error: PrometheusReadError, prefix: str, raw: dict[str, Any]) -> ToolObservation:
    return ToolObservation(
        status=error.status,
        data={},
        raw_output=raw,
        completeness=Completeness.PARTIAL if error.status == ToolStatus.PARTIAL else Completeness.UNKNOWN,
        summary=f"{prefix}：{error.message}",
        error_code=error.code,
        error_message=error.message,
        retryable=error.retryable,
    )


class RestartHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_minutes: int = Field(default=30, ge=5, le=120)
    step_seconds: int = Field(default=15, ge=10, le=120)


class GetContainerRestartHistoryTool:
    name = "get_container_restart_history"
    version = "1.0.0"
    cost_units = 2
    arguments_model = RestartHistoryInput

    def __init__(self, prom: PrometheusReadClient | None = None, kubernetes: KubernetesReadClient | None = None) -> None:
        self.prom = prom or PrometheusReadClient()
        self.kubernetes = kubernetes or KubernetesReadClient()

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = RestartHistoryInput.model_validate(arguments)
        end = _effective_end(target)
        start = max(target.window_start, target.incident_time - timedelta(minutes=args.window_minutes))
        scope = target_scope_selector(target)
        query = (
            f'kube_pod_container_status_restarts_total{{{scope},'
            f'uid="{escape_promql_label(target.pod_uid)}"}}'
        )
        raw: dict[str, Any] = {"query": query, "responses": {}}
        try:
            payload = await self.prom.query_range(query=query, target=target, start=start, end=end, step_seconds=args.step_seconds)
            raw["responses"]["prometheus"] = payload
            pod = await self.kubernetes.get_pod(target.namespace, target.pod_name)
            raw["responses"]["pod_status"] = pod
        except PrometheusReadError as error:
            return _prom_error(error, "容器重启历史查询失败", raw)
        except KubernetesReadError as error:
            return ToolObservation(
                status=error.status,
                data={},
                raw_output=raw,
                completeness=Completeness.UNKNOWN,
                summary=f"容器重启状态读取失败：{error.message}",
                error_code=error.error_code,
                error_message=error.message,
                retryable=error.retryable,
            )
        actual_uid = str((pod.get("metadata") or {}).get("uid") or "")
        if actual_uid != target.pod_uid:
            return ToolObservation(
                status=ToolStatus.TARGET_UNCERTAIN,
                data={"expected_uid": target.pod_uid, "actual_uid": actual_uid},
                raw_output=raw,
                completeness=Completeness.UNKNOWN,
                summary="当前 Pod UID 与 TargetContext 不一致。",
                error_code="KUBERNETES_POD_UID_MISMATCH",
            )
        statuses = list((pod.get("status") or {}).get("containerStatuses") or [])
        container_status = next((item for item in statuses if item.get("name") == target.container_name), None)
        if container_status is None:
            return ToolObservation(
                status=ToolStatus.TARGET_UNCERTAIN,
                data={}, raw_output=raw, completeness=Completeness.UNKNOWN,
                summary="目标 Container 不属于当前 Pod。", error_code="KUBERNETES_CONTAINER_MISMATCH",
            )
        points, matched, unmatched = _matrix_points(payload, target)
        if unmatched and not matched:
            return ToolObservation(
                status=ToolStatus.TARGET_UNCERTAIN,
                data={"unmatched_series": unmatched}, raw_output=raw, completeness=Completeness.UNKNOWN,
                summary="重启指标序列 UID 与 TargetContext 不一致。", error_code="PROMETHEUS_POD_UID_MISMATCH",
            )
        current_count = int(container_status.get("restartCount") or 0)
        if not points:
            return ToolObservation(
                status=ToolStatus.PARTIAL,
                data={"current_restart_count": current_count, "window_restart_delta": None, "restart_events": []},
                raw_output=raw,
                completeness=Completeness.PARTIAL,
                summary=f"当前 restartCount={current_count}，但 Prometheus 没有窗口历史样本。",
                error_code="PROMETHEUS_NO_RESTART_SAMPLES",
            )
        values = [item["value"] for item in points]
        delta = max(0.0, max(values) - min(values))
        events: list[dict[str, Any]] = []
        previous = points[0]["value"]
        for point in points[1:]:
            if point["value"] > previous:
                events.append({
                    "time": datetime.fromtimestamp(point["timestamp"], tz=UTC).isoformat(),
                    "from": previous,
                    "to": point["value"],
                    "increase": point["value"] - previous,
                })
            previous = point["value"]
        coverage = _coverage(points, start, end, args.step_seconds)
        completeness = Completeness.COMPLETE if coverage["complete"] else Completeness.PARTIAL
        data = {
            "query_start": start.isoformat(), "query_end": end.isoformat(),
            "current_restart_count": current_count,
            "window_restart_delta": round(delta, 3),
            "restart_events": events[:50],
            "restart_event_count": len(events),
            "repeated": delta >= 3 or len(events) >= 3,
            "coverage": coverage,
        }
        return ToolObservation(
            status=ToolStatus.FOUND if completeness == Completeness.COMPLETE else ToolStatus.PARTIAL,
            data=data, raw_output=raw, completeness=completeness,
            summary=f"当前 restartCount={current_count}，调查窗口内增加 {delta:.0f} 次。",
            error_code=None if completeness == Completeness.COMPLETE else "PROMETHEUS_SAMPLE_COVERAGE_PARTIAL",
        )


class RecentRolloutsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback_minutes: int = Field(default=120, ge=15, le=720)


class GetRecentRolloutsTool:
    name = "get_recent_rollouts"
    version = "1.0.0"
    cost_units = 2
    arguments_model = RecentRolloutsInput

    def __init__(self, kubernetes: KubernetesReadClient | None = None) -> None:
        self.kubernetes = kubernetes or KubernetesReadClient()

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = RecentRolloutsInput.model_validate(arguments)
        if target.workload_kind != "Deployment" or not target.workload_name or not target.workload_uid:
            return ToolObservation(
                status=ToolStatus.NOT_FOUND, data={}, raw_output=None, completeness=Completeness.COMPLETE,
                summary="目标不是已固定 UID 的 Deployment，无法生成 rollout 事实。",
                error_code="ROLLOUT_TARGET_NOT_DEPLOYMENT",
            )
        start = max(target.window_start, target.incident_time - timedelta(minutes=args.lookback_minutes))
        end = _effective_end(target)
        raw: dict[str, Any] = {"kubernetes": {}, "change_events": []}
        try:
            deployment = await self.kubernetes.get_deployment(target.namespace, target.workload_name)
            raw["kubernetes"]["deployment"] = deployment
        except KubernetesReadError as error:
            return ToolObservation(
                status=error.status, data={}, raw_output=raw, completeness=Completeness.UNKNOWN,
                summary=f"Deployment 读取失败：{error.message}", error_code=error.error_code,
                error_message=error.message, retryable=error.retryable,
            )
        actual_uid = str((deployment.get("metadata") or {}).get("uid") or "")
        if actual_uid != target.workload_uid:
            return ToolObservation(
                status=ToolStatus.TARGET_UNCERTAIN,
                data={"expected_workload_uid": target.workload_uid, "actual_workload_uid": actual_uid},
                raw_output=raw, completeness=Completeness.UNKNOWN,
                summary="Deployment UID 与 TargetContext 不一致。", error_code="KUBERNETES_WORKLOAD_UID_MISMATCH",
            )
        selector = _selector_string((deployment.get("spec") or {}).get("selector") or {})
        if not selector:
            return ToolObservation(
                status=ToolStatus.PARTIAL, data={}, raw_output=raw, completeness=Completeness.PARTIAL,
                summary="Deployment selector 无法转换为受控查询。", error_code="KUBERNETES_SELECTOR_UNSUPPORTED",
            )
        try:
            replica_sets = await self.kubernetes.list_replicasets(target.namespace, label_selector=selector)
            raw["kubernetes"]["replicasets"] = replica_sets
        except KubernetesReadError as error:
            return ToolObservation(
                status=error.status, data={}, raw_output=raw, completeness=Completeness.UNKNOWN,
                summary=f"ReplicaSet 列表读取失败：{error.message}", error_code=error.error_code,
                error_message=error.message, retryable=error.retryable,
            )
        owned = []
        for rs in replica_sets:
            owner = _controller_owner(rs) or {}
            if owner.get("kind") == "Deployment" and str(owner.get("uid") or "") == target.workload_uid:
                meta = rs.get("metadata") or {}
                owned.append({
                    "name": meta.get("name"), "uid": meta.get("uid"),
                    "revision": _revision(rs), "created_at": _iso(meta.get("creationTimestamp")),
                    "images": _images(rs),
                    "replicas": (rs.get("status") or {}).get("replicas"),
                    "ready_replicas": (rs.get("status") or {}).get("readyReplicas"),
                })
        owned.sort(key=lambda item: (item.get("revision") or -1, item.get("created_at") or ""))
        async with SessionLocal() as session:
            changes = list((await session.scalars(
                select(ChangeEvent).where(
                    ChangeEvent.namespace == target.namespace,
                    ChangeEvent.service == (target.service_name or target.workload_name),
                    ChangeEvent.occurred_at >= start,
                    ChangeEvent.occurred_at <= end,
                ).order_by(ChangeEvent.occurred_at)
            )).all())
        raw["change_events"] = [
            {"id": row.id, "source": row.source, "version": row.version, "commit_sha": row.commit_sha,
             "image": row.image, "occurred_at": row.occurred_at.isoformat(), "title": row.title}
            for row in changes
        ]
        recent_rs = [item for item in owned if item.get("created_at") and start <= datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00")) <= end]
        current_revision = _revision(deployment)
        previous_revision = owned[-2]["revision"] if len(owned) >= 2 else None
        current_images = _images(deployment)
        previous_images = owned[-2]["images"] if len(owned) >= 2 else []
        image_changed = bool(previous_images and current_images and previous_images != current_images)
        rollout_times = [item["created_at"] for item in recent_rs if item.get("created_at")] + [row.occurred_at.isoformat() for row in changes]
        nearest_before = None
        before_times = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in rollout_times if datetime.fromisoformat(value.replace("Z", "+00:00")) <= target.incident_time]
        if before_times:
            nearest_before = max(before_times)
        data = {
            "query_start": start.isoformat(), "query_end": end.isoformat(),
            "deployment_name": target.workload_name, "deployment_uid": target.workload_uid,
            "current_revision": current_revision, "previous_revision": previous_revision,
            "current_images": current_images, "previous_images": previous_images,
            "revision_changed": bool(current_revision and previous_revision and current_revision != previous_revision),
            "image_changed": image_changed,
            "replicasets": owned[-10:],
            "recent_replicaset_count": len(recent_rs),
            "change_events": raw["change_events"],
            "recent_change_count": len(changes),
            "rollout_preceded_incident": nearest_before is not None,
            "nearest_rollout_before_incident": nearest_before.isoformat() if nearest_before else None,
            "minutes_before_incident": round((target.incident_time - nearest_before).total_seconds() / 60, 3) if nearest_before else None,
            "no_recent_rollout": not recent_rs and not changes,
        }
        return ToolObservation(
            status=ToolStatus.FOUND,
            data=data, raw_output=raw, completeness=Completeness.COMPLETE,
            summary=(
                f"最近窗口包含 {len(recent_rs)} 个 ReplicaSet 变化和 {len(changes)} 条 CI/CD 记录。"
                if not data["no_recent_rollout"] else "最近窗口未观察到 Deployment rollout 或 CI/CD 发布记录。"
            ),
        )


_LOG_PATTERNS: dict[str, str] = {
    "oom": r"oomkilled|oom kill|out of memory",
    "allocation_failure": r"cannot allocate memory|allocation fail|memoryerror|bad_alloc",
    "process_termination": r"terminated|killed|exit code|signal 9|sigkill",
    "runtime_error": r"exception|fatal|panic|runtime error|traceback",
    "cpu_hot_loop_hint": r"hot loop|busy loop|spin loop|event loop blocked|cpu bound",
    "gc_pressure": r"gc overhead|garbage collection|full gc|allocation pressure",
    "request_timeout": r"request timeout|context deadline exceeded|upstream timed out|gateway timeout",
}
_PROMPT_INJECTION = re.compile(
    r"(?i)(ignore previous|system prompt|call an unregistered tool|read .*secrets|restart the deployment|mark .* confirmed|execute shell|curl http)",
)
_SECRET_TEXT = re.compile(r"(?i)(authorization|api[_-]?key|password|token|secret)\s*[:=]\s*\S+")


class SearchContainerLogsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["current", "previous", "both"] = "both"
    categories: list[Literal[
        "oom", "allocation_failure", "process_termination", "runtime_error",
        "cpu_hot_loop_hint", "gc_pressure", "request_timeout",
    ]] = Field(min_length=1, max_length=7)
    relative_window_minutes: int = Field(default=30, ge=5, le=120)


class SearchContainerLogsTool:
    name = "search_container_logs"
    version = "1.0.0"
    cost_units = 3
    arguments_model = SearchContainerLogsInput

    def __init__(self, loki: LokiReadClient | None = None, kubernetes: KubernetesReadClient | None = None) -> None:
        self.loki = loki or LokiReadClient()
        self.kubernetes = kubernetes or KubernetesReadClient()

    @staticmethod
    def _safe_line(line: str) -> tuple[str, bool]:
        clean = _SECRET_TEXT.sub(lambda m: m.group(1) + "=[REDACTED]", line.strip())
        injection = bool(_PROMPT_INJECTION.search(clean))
        if injection:
            return "[UNTRUSTED_INSTRUCTION_REDACTED]", True
        return clean[:2000], False

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = SearchContainerLogsInput.model_validate(arguments)
        categories = list(dict.fromkeys(args.categories))
        regex = "|".join(f"(?:{_LOG_PATTERNS[item]})" for item in categories)
        end = _effective_end(target)
        start = max(target.window_start, target.incident_time - timedelta(minutes=args.relative_window_minutes))
        raw: dict[str, Any] = {"loki": None, "kubernetes": [], "untrusted_input": True}
        lines: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        try:
            loki_payload = await self.loki.query_lines(
                namespace=target.namespace, pod=target.pod_name, container=target.container_name,
                regex=regex, start=start, end=end, limit=120,
            )
            safe_loki_lines: list[dict[str, Any]] = []
            for stream in (((loki_payload.get("response") or {}).get("data") or {}).get("result") or []):
                for pair in stream.get("values") or []:
                    if not isinstance(pair, list) or len(pair) != 2:
                        continue
                    safe, injection = self._safe_line(str(pair[1]))
                    item = {"source": "loki", "time_ns": str(pair[0]), "line": safe, "prompt_injection_redacted": injection}
                    lines.append(item)
                    safe_loki_lines.append(item)
            raw["loki"] = {"query": loki_payload.get("query"), "matched_lines": safe_loki_lines}
        except LokiReadError as error:
            errors.append({"source": "loki", "status": error.status.value, "code": error.code, "message": error.message, "retryable": error.retryable})
        modes = []
        if args.target in {"current", "both"}:
            modes.append(False)
        if args.target in {"previous", "both"}:
            modes.append(True)
        for previous in modes:
            try:
                text = await self.kubernetes.read_pod_log(
                    target.namespace, target.pod_name, container=target.container_name,
                    previous=previous, tail_lines=150, limit_bytes=131072,
                    since_seconds=args.relative_window_minutes * 60,
                )
                sanitized_log_lines: list[str] = []
                for raw_line in text.splitlines():
                    safe, injection = self._safe_line(raw_line)
                    sanitized_log_lines.append(safe)
                    lower = raw_line.lower()
                    if not any(re.search(_LOG_PATTERNS[category], lower, re.I) for category in categories):
                        continue
                    lines.append({"source": "kubernetes_previous" if previous else "kubernetes_current", "line": safe, "prompt_injection_redacted": injection})
                raw["kubernetes"].append({"previous": previous, "lines": sanitized_log_lines[:200]})
            except KubernetesReadError as error:
                if previous and error.status in {ToolStatus.NOT_FOUND, ToolStatus.INVALID_REQUEST}:
                    continue
                errors.append({"source": "kubernetes_previous" if previous else "kubernetes_current", "status": error.status.value, "code": error.error_code, "message": error.message, "retryable": error.retryable})
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in lines:
            key = re.sub(r"^\S+\s+", "", item["line"]).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= 100:
                break
        counts = {category: 0 for category in categories}
        for item in deduped:
            if item["line"] == "[UNTRUSTED_INSTRUCTION_REDACTED]":
                continue
            for category in categories:
                if re.search(_LOG_PATTERNS[category], item["line"], re.I):
                    counts[category] += 1
        data = {
            "query_start": start.isoformat(), "query_end": end.isoformat(),
            "requested_categories": categories,
            "category_counts": counts,
            "matched_line_count": len(deduped),
            "duplicate_lines_removed": max(0, len(lines) - len(deduped)),
            "prompt_injection_redacted_count": sum(1 for item in deduped if item.get("prompt_injection_redacted")),
            "sample_lines": deduped[:30],
            "errors": errors,
            "untrusted_input": True,
        }
        if deduped:
            status = ToolStatus.FOUND if not errors else ToolStatus.PARTIAL
            completeness = Completeness.COMPLETE if not errors else Completeness.PARTIAL
            return ToolObservation(
                status=status, data=data, raw_output=raw, completeness=completeness,
                summary=f"在受控日志范围内观察到 {len(deduped)} 条去重后的类别匹配日志。",
                error_code=None if not errors else "LOG_SOURCE_PARTIAL",
            )
        if errors and all(item["status"] in {ToolStatus.UNAVAILABLE.value, ToolStatus.DENIED.value} for item in errors):
            denied = any(item["status"] == ToolStatus.DENIED.value for item in errors)
            return ToolObservation(
                status=ToolStatus.DENIED if denied else ToolStatus.UNAVAILABLE,
                data=data, raw_output=raw, completeness=Completeness.UNKNOWN,
                summary="日志数据源不可用或访问被拒绝。",
                error_code="LOG_ACCESS_DENIED" if denied else "LOG_SOURCES_UNAVAILABLE",
                retryable=not denied,
            )
        return ToolObservation(
            status=ToolStatus.NOT_FOUND, data=data, raw_output=raw, completeness=Completeness.COMPLETE,
            summary="受控日志范围内未观察到请求的日志类别。", error_code="LOG_CATEGORY_NOT_OBSERVED",
        )


class CompareCPUAcrossReplicasInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_minutes: int = Field(default=15, ge=5, le=30)
    step_seconds: int = Field(default=15, ge=10, le=60)
    max_replicas: int = Field(default=12, ge=2, le=20)


class CompareCPUAcrossReplicasTool:
    name = "compare_cpu_across_replicas"
    version = "1.0.0"
    cost_units = 5
    arguments_model = CompareCPUAcrossReplicasInput

    def __init__(self, prom: PrometheusReadClient | None = None, kubernetes: KubernetesReadClient | None = None) -> None:
        self.prom = prom or PrometheusReadClient(max_series=5)
        self.kubernetes = kubernetes or KubernetesReadClient()

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = CompareCPUAcrossReplicasInput.model_validate(arguments)
        if target.workload_kind != "Deployment" or not target.workload_name or not target.workload_uid:
            return ToolObservation(
                status=ToolStatus.NOT_FOUND, data={}, raw_output=None, completeness=Completeness.COMPLETE,
                summary="目标不是已固定 UID 的 Deployment，无法比较副本。", error_code="REPLICA_TARGET_NOT_DEPLOYMENT",
            )
        try:
            deployment = await self.kubernetes.get_deployment(target.namespace, target.workload_name)
        except KubernetesReadError as error:
            return ToolObservation(status=error.status, data={}, raw_output=None, completeness=Completeness.UNKNOWN,
                summary=f"Deployment 读取失败：{error.message}", error_code=error.error_code, retryable=error.retryable)
        if str((deployment.get("metadata") or {}).get("uid") or "") != target.workload_uid:
            return ToolObservation(status=ToolStatus.TARGET_UNCERTAIN, data={}, raw_output=deployment,
                completeness=Completeness.UNKNOWN, summary="Deployment UID 与 TargetContext 不一致。",
                error_code="KUBERNETES_WORKLOAD_UID_MISMATCH")
        selector = _selector_string((deployment.get("spec") or {}).get("selector") or {})
        if not selector:
            return ToolObservation(status=ToolStatus.PARTIAL, data={}, raw_output=deployment,
                completeness=Completeness.PARTIAL, summary="Deployment selector 不受支持。",
                error_code="KUBERNETES_SELECTOR_UNSUPPORTED")
        try:
            replica_sets = await self.kubernetes.list_replicasets(target.namespace, label_selector=selector)
            pods = await self.kubernetes.list_pods(target.namespace, label_selector=selector)
        except KubernetesReadError as error:
            return ToolObservation(status=error.status, data={}, raw_output=None, completeness=Completeness.UNKNOWN,
                summary=f"副本对象读取失败：{error.message}", error_code=error.error_code, retryable=error.retryable)
        revision_by_rs: dict[str, int | None] = {}
        for rs in replica_sets:
            owner = _controller_owner(rs) or {}
            if owner.get("kind") == "Deployment" and str(owner.get("uid") or "") == target.workload_uid:
                revision_by_rs[str((rs.get("metadata") or {}).get("name") or "")] = _revision(rs)
        candidates: list[dict[str, Any]] = []
        for pod in pods:
            meta = pod.get("metadata") or {}
            owner = _controller_owner(pod) or {}
            rs_name = str(owner.get("name") or "")
            if owner.get("kind") != "ReplicaSet" or rs_name not in revision_by_rs:
                continue
            names = [str(item.get("name")) for item in (((pod.get("spec") or {}).get("containers")) or [])]
            if target.container_name not in names:
                continue
            candidates.append({
                "name": str(meta.get("name") or ""), "uid": str(meta.get("uid") or ""),
                "node": (pod.get("spec") or {}).get("nodeName"), "revision": revision_by_rs[rs_name],
                "replicaset": rs_name,
            })
        candidates = candidates[: args.max_replicas]
        if len(candidates) < 2:
            return ToolObservation(status=ToolStatus.NOT_FOUND, data={"replica_count": len(candidates)},
                raw_output={"deployment": deployment, "replicasets": replica_sets, "pods": pods},
                completeness=Completeness.COMPLETE, summary="可比较的同一 Workload 副本少于 2 个。",
                error_code="INSUFFICIENT_REPLICAS")
        end = _effective_end(target)
        start = max(target.window_start, target.incident_time - timedelta(minutes=args.window_minutes))
        # Alert delivery happens after the signal has already been elevated. Compare the
        # most recent two minutes across replicas so short incidents are not diluted by
        # the entire lookback window. Older same-UID samples remain the replica baseline.
        incident_split = max(start, end - timedelta(minutes=2))
        raw: dict[str, Any] = {"deployment": deployment, "replicasets": replica_sets, "pods": candidates, "prometheus": {}}
        results: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for pod in candidates:
            derived = target.model_copy(update={"pod_name": pod["name"], "pod_uid": pod["uid"], "resolution_method": "workload_uid_replica_member"})
            query = f"rate(container_cpu_usage_seconds_total{{{target_scope_selector(derived)}}}[1m])"
            try:
                payload = await self.prom.query_range(query=query, target=derived, start=start, end=end, step_seconds=args.step_seconds)
                raw["prometheus"][pod["name"]] = payload
                points, matched, unmatched = _matrix_points(payload, derived)
                if unmatched and not matched:
                    failed.append({"pod": pod["name"], "code": "PROMETHEUS_POD_UID_MISMATCH"})
                    continue
                baseline = [item["value"] for item in points if item["timestamp"] < incident_split.timestamp()]
                incident = [item["value"] for item in points if item["timestamp"] >= incident_split.timestamp()]
                if not incident:
                    failed.append({"pod": pod["name"], "code": "PROMETHEUS_NO_CPU_SAMPLES"})
                    continue
                baseline_median = statistics.median(baseline) if baseline else None
                incident_mean = statistics.fmean(incident)
                peak = max(incident)
                results.append({**pod, "baseline_median_cores": baseline_median, "incident_mean_cores": incident_mean,
                    "peak_cores": peak, "baseline_multiplier": incident_mean / max(baseline_median or 0.005, 0.005)})
            except PrometheusReadError as error:
                failed.append({"pod": pod["name"], "code": error.code, "status": error.status.value})
        if len(results) < 2:
            return ToolObservation(status=ToolStatus.PARTIAL, data={"replicas": results, "failures": failed},
                raw_output=raw, completeness=Completeness.PARTIAL,
                summary="获得 CPU 数据的副本少于 2 个，无法形成副本比较事实。",
                error_code="INSUFFICIENT_REPLICA_METRICS")
        means = [item["incident_mean_cores"] for item in results]
        median_mean = statistics.median(means)
        anomaly_threshold = max(median_mean * 1.8, median_mean + 0.05)
        anomalies = [item for item in results if item["incident_mean_cores"] >= anomaly_threshold]
        revisions: dict[int | None, list[float]] = defaultdict(list)
        for item in results:
            revisions[item["revision"]].append(item["incident_mean_cores"])
        revision_means = {str(key): statistics.fmean(values) for key, values in revisions.items()}
        latest_revision = max((key for key in revisions if key is not None), default=None)
        older_values = [value for key, values in revisions.items() if key is not None and key != latest_revision for value in values]
        latest_values = revisions.get(latest_revision, []) if latest_revision is not None else []
        new_revision_higher = bool(latest_values and older_values and statistics.fmean(latest_values) >= statistics.fmean(older_values) * 1.5 + 0.05)
        all_increased = bool(all(
            item.get("baseline_median_cores") is not None
            and item["incident_mean_cores"] - item["baseline_median_cores"] >= 0.10
            and item["baseline_multiplier"] >= 2.0 for item in results
        ))
        data = {
            "query_start": start.isoformat(), "query_end": end.isoformat(),
            "comparison_window_start": incident_split.isoformat(),
            "replica_count": len(results), "failed_replica_count": len(failed),
            "replicas": results, "failures": failed,
            "median_incident_cpu_cores": median_mean,
            "max_incident_cpu_cores": max(means),
            "anomaly_threshold_cores": anomaly_threshold,
            "anomalous_pods": [item["name"] for item in anomalies],
            "anomalous_replica_count": len(anomalies),
            "single_replica_anomaly": len(anomalies) == 1,
            "subset_replicas_anomaly": 1 < len(anomalies) < len(results),
            "all_replicas_increased": all_increased,
            "revision_means": revision_means,
            "latest_revision": latest_revision,
            "new_revision_cpu_higher": new_revision_higher,
        }
        completeness = Completeness.COMPLETE if not failed else Completeness.PARTIAL
        return ToolObservation(
            status=ToolStatus.FOUND if completeness == Completeness.COMPLETE else ToolStatus.PARTIAL,
            data=data, raw_output=raw, completeness=completeness,
            summary=f"比较 {len(results)} 个副本，发现 {len(anomalies)} 个 CPU 离群副本。",
            error_code=None if not failed else "REPLICA_METRICS_PARTIAL",
        )


_RED_PROFILES = [
    {
        "name": "prometheus_http",
        "label": "service",
        "request": "http_requests_total",
        "latency": "http_request_duration_seconds_bucket",
        "status_label": "status",
    },
    {
        "name": "micrometer_http_server",
        "label": "application",
        "request": "http_server_requests_seconds_count",
        "latency": "http_server_requests_seconds_bucket",
        "status_label": "status",
    },
    {
        "name": "otel_http_server",
        "label": "service_name",
        "request": "http_server_request_duration_seconds_count",
        "latency": "http_server_request_duration_seconds_bucket",
        "status_label": "http_response_status_code",
    },
]


class ApplicationREDInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signals: list[Literal["request_rate", "error_rate", "latency"]] = Field(default_factory=lambda: ["request_rate", "error_rate", "latency"], min_length=1, max_length=3)
    route_scope: Literal["all"] = "all"
    baseline_minutes: int = Field(default=30, ge=15, le=60)
    incident_minutes: int = Field(default=10, ge=5, le=20)
    step_seconds: int = Field(default=30, ge=15, le=120)


def _aggregate_matrix(payload: dict[str, Any]) -> list[dict[str, float]]:
    by_time: dict[float, float] = defaultdict(float)
    for series in (((payload.get("data") or {}).get("result")) or []):
        for pair in series.get("values") or []:
            try:
                ts, value = float(pair[0]), float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(value):
                by_time[ts] += value
    return [{"timestamp": ts, "value": value} for ts, value in sorted(by_time.items())]


def _aggregate_matrix_with_series_count(payload: dict[str, Any]) -> tuple[list[dict[str, float]], list[dict[str, float]], list[dict[str, float]]]:
    totals: dict[float, float] = defaultdict(float)
    counts: dict[float, int] = defaultdict(int)
    for series in (((payload.get("data") or {}).get("result")) or []):
        seen: set[float] = set()
        for pair in series.get("values") or []:
            try:
                ts, value = float(pair[0]), float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            if not math.isfinite(value):
                continue
            totals[ts] += value
            if ts not in seen:
                counts[ts] += 1
                seen.add(ts)
    total_points = [{"timestamp": ts, "value": value} for ts, value in sorted(totals.items())]
    per_series_points = [
        {"timestamp": ts, "value": totals[ts] / max(counts.get(ts, 1), 1)}
        for ts in sorted(totals)
    ]
    count_points = [{"timestamp": ts, "value": float(counts[ts])} for ts in sorted(counts)]
    return total_points, per_series_points, count_points


def _signal_change(points: list[dict[str, float]], incident_start: datetime) -> dict[str, Any] | None:
    baseline_points = [item for item in points if item["timestamp"] < incident_start.timestamp()]
    incident_points = [item for item in points if item["timestamp"] >= incident_start.timestamp()]
    method = "fixed_target_window"
    if len(baseline_points) < 3 or len(incident_points) < 2:
        split_index = max(3, min(len(points) - 3, int(len(points) * 0.45))) if len(points) >= 6 else 0
        if split_index:
            baseline_points = points[:split_index]
            incident_points = points[split_index:]
            method = "observed_same_service_series_split"
    if len(baseline_points) < 3 or len(incident_points) < 2:
        return None
    baseline = [item["value"] for item in baseline_points]
    incident = [item["value"] for item in incident_points]
    baseline_mean = statistics.fmean(baseline)
    incident_mean = statistics.fmean(incident)
    return {
        "baseline_mean": baseline_mean, "incident_mean": incident_mean,
        "ratio": incident_mean / max(baseline_mean, 1e-9), "absolute_delta": incident_mean - baseline_mean,
        "baseline_samples": len(baseline), "incident_samples": len(incident),
        "baseline_method": method,
    }


class GetApplicationREDMetricsTool:
    name = "get_application_red_metrics"
    version = "1.0.0"
    cost_units = 4
    arguments_model = ApplicationREDInput

    def __init__(self, prom: PrometheusReadClient | None = None) -> None:
        self.prom = prom or PrometheusReadClient(max_series=20)

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = ApplicationREDInput.model_validate(arguments)
        service = target.service_name or target.workload_name
        if not service:
            return ToolObservation(status=ToolStatus.NOT_FOUND, data={"capability_gap": "service_name_missing"},
                raw_output=None, completeness=Completeness.COMPLETE,
                summary="TargetContext 没有可用 service，无法匹配应用 RED 指标。",
                error_code="APPLICATION_SERVICE_UNKNOWN")
        end = _effective_end(target)
        incident_start = max(target.window_start, target.incident_time - timedelta(minutes=args.incident_minutes))
        start = max(target.window_start, incident_start - timedelta(minutes=args.baseline_minutes))
        raw: dict[str, Any] = {"profiles_attempted": [], "responses": {}}
        selected = None
        signal_data: dict[str, Any] = {}
        allowed_metrics = {item[key] for item in _RED_PROFILES for key in ("request", "latency")}
        for profile in _RED_PROFILES:
            label = profile["label"]
            base_selector = f'namespace="{escape_promql_label(target.namespace)}",{label}="{escape_promql_label(service)}"'
            request_metric = profile["request"]
            latency_metric = profile["latency"]
            queries = {
                "request_rate": f"sum by (pod, instance) (rate({request_metric}{{{base_selector}}}[2m]))",
                "error_rate": (
                    f"sum(rate({request_metric}{{{base_selector},{profile['status_label']}=~\"5..\"}}[2m])) / "
                    f"clamp_min(sum(rate({request_metric}{{{base_selector}}}[2m])), 0.001)"
                ),
                "latency": f"histogram_quantile(0.99, sum by (le) (rate({latency_metric}{{{base_selector}}}[2m])))",
            }
            raw["profiles_attempted"].append({"name": profile["name"], "queries": queries})
            try:
                request_payload = await self.prom.query_range_application(
                    query=queries["request_rate"], namespace=target.namespace, service=service,
                    allowed_metrics=allowed_metrics, start=start, end=end, step_seconds=args.step_seconds,
                )
            except PrometheusReadError as error:
                if error.status in {ToolStatus.DENIED, ToolStatus.UNAVAILABLE}:
                    return _prom_error(error, "应用 RED 指标查询失败", raw)
                continue
            request_points, request_per_series_points, request_series_count_points = _aggregate_matrix_with_series_count(request_payload)
            if not request_points:
                continue
            selected = profile
            raw["responses"]["request_rate"] = request_payload
            request_total_change = _signal_change(request_points, target.incident_time)
            request_per_series_change = _signal_change(request_per_series_points, target.incident_time)
            request_series_change = _signal_change(request_series_count_points, target.incident_time)
            signal_data["request_rate_total"] = request_total_change
            signal_data["request_rate_per_reporting_series"] = request_per_series_change
            signal_data["reporting_series"] = request_series_change
            signal_data["request_rate"] = request_total_change
            for signal in ("error_rate", "latency"):
                if signal not in args.signals:
                    continue
                try:
                    payload = await self.prom.query_range_application(
                        query=queries[signal], namespace=target.namespace, service=service,
                        allowed_metrics=allowed_metrics, start=start, end=end, step_seconds=args.step_seconds,
                    )
                    raw["responses"][signal] = payload
                    signal_data[signal] = _signal_change(_aggregate_matrix(payload), target.incident_time)
                except PrometheusReadError as error:
                    signal_data[signal] = {"error_code": error.code, "status": error.status.value}
            break
        if selected is None:
            return ToolObservation(
                status=ToolStatus.NOT_FOUND,
                data={
                    "service": service, "profiles_attempted": [item["name"] for item in _RED_PROFILES],
                    "capability_gap": "application_metrics_unavailable",
                    "query_start": start.isoformat(), "query_end": end.isoformat(),
                },
                raw_output=raw, completeness=Completeness.COMPLETE,
                summary="内置应用指标 Profile 均未匹配到 RED 指标。",
                error_code="APPLICATION_METRICS_UNAVAILABLE",
            )
        request_total = signal_data.get("request_rate_total") or {}
        request_per_series = signal_data.get("request_rate_per_reporting_series") or {}
        reporting_series = signal_data.get("reporting_series") or {}
        series_changed = bool(
            reporting_series
            and abs(reporting_series.get("absolute_delta", 0)) >= 0.5
        )
        total_stable = bool(
            request_total
            and abs(request_total.get("absolute_delta", 0)) <= max(0.1, request_total.get("baseline_mean", 0) * 0.15)
        )
        per_series_stable = bool(
            request_per_series
            and abs(request_per_series.get("absolute_delta", 0)) <= max(0.1, request_per_series.get("baseline_mean", 0) * 0.15)
        )
        normalized_stable = bool(
            series_changed
            and request_total
            and request_total.get("ratio", 0) < 1.5
            and per_series_stable
        )
        if normalized_stable:
            effective_request = dict(request_per_series)
            effective_request["normalization"] = "per_reporting_series_due_coverage_change"
            effective_request["service_total"] = request_total
            effective_request["reporting_series"] = reporting_series
            signal_data["request_rate"] = effective_request
        request = signal_data.get("request_rate") or {}
        error = signal_data.get("error_rate") or {}
        latency = signal_data.get("latency") or {}
        data = {
            "service": service, "profile": selected["name"],
            "query_start": start.isoformat(), "query_end": end.isoformat(),
            "incident_start": incident_start.isoformat(),
            "signal_split_at": target.incident_time.isoformat(),
            "signals": signal_data,
            "request_rate_increased": bool(request_total and request_total.get("ratio", 0) >= 1.5 and request_total.get("absolute_delta", 0) >= 1.0),
            "request_rate_stable": bool(total_stable or normalized_stable),
            "request_rate_normalization": "per_reporting_series_due_coverage_change" if normalized_stable else "service_total",
            "reporting_series_changed": series_changed,
            "error_rate_increased": bool(error and error.get("incident_mean", 0) >= 0.01 and error.get("incident_mean", 0) >= max(error.get("baseline_mean", 0) * 2, error.get("baseline_mean", 0) + 0.01)),
            "latency_increased": bool(latency and latency.get("incident_mean", 0) >= max(latency.get("baseline_mean", 0) * 1.5, latency.get("baseline_mean", 0) + 0.1)),
        }
        partial = any(value is None or (isinstance(value, dict) and value.get("error_code")) for value in signal_data.values())
        return ToolObservation(
            status=ToolStatus.PARTIAL if partial else ToolStatus.FOUND,
            data=data, raw_output=raw,
            completeness=Completeness.PARTIAL if partial else Completeness.COMPLETE,
            summary=f"应用 RED 指标匹配 Profile={selected['name']}，已比较请求率、错误率和 P99 延迟。",
            error_code="APPLICATION_RED_PARTIAL" if partial else None,
        )
