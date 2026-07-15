from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
import statistics
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.investigation.contracts import TargetContext, ToolObservation
from app.investigation.enums import Completeness, ToolStatus
from app.investigation.prometheus import (
    PrometheusReadClient,
    PrometheusReadError,
    resource_scope_selector,
    target_scope_selector,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _effective_end(target: TargetContext) -> datetime:
    return min(target.window_end, utcnow())


def _uid_token(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _series_matches_target_uid(metric: dict[str, Any], target: TargetContext) -> bool:
    direct_uid = str(metric.get("uid") or "")
    if direct_uid:
        return direct_uid == target.pod_uid
    cgroup_id = str(metric.get("id") or "")
    return bool(cgroup_id) and _uid_token(target.pod_uid) in _uid_token(cgroup_id)


def _matrix_points(payload: dict[str, Any], target: TargetContext) -> tuple[list[dict[str, Any]], int, int]:
    result = ((payload.get("data") or {}).get("result") or [])
    points: list[dict[str, Any]] = []
    matched_series = 0
    unmatched_series = 0
    for series_index, series in enumerate(result):
        metric = series.get("metric") or {}
        if not _series_matches_target_uid(metric, target):
            unmatched_series += 1
            continue
        matched_series += 1
        for pair in series.get("values") or []:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            try:
                timestamp = float(pair[0])
                value = float(pair[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                points.append({
                    "timestamp": timestamp,
                    "value": value,
                    "series_index": series_index,
                    "metric": metric,
                })
    points.sort(key=lambda item: item["timestamp"])
    return points, matched_series, unmatched_series


def _vector_value(payload: dict[str, Any], target: TargetContext) -> tuple[float | None, int]:
    result = ((payload.get("data") or {}).get("result") or [])
    values: list[float] = []
    for item in result:
        metric = item.get("metric") or {}
        if not _series_matches_target_uid(metric, target):
            continue
        pair = item.get("value") or []
        try:
            value = float(pair[1])
        except (IndexError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return (max(values) if values else None), len(values)


def _coverage(points: list[dict[str, Any]], start: datetime, end: datetime, step_seconds: int) -> dict[str, Any]:
    timestamps = sorted({round(float(item["timestamp"]), 3) for item in points})
    requested_expected = max(1, int((end - start).total_seconds() // step_seconds) + 1)
    if not timestamps:
        return {
            "requested_expected_points": requested_expected,
            "observed_timestamps": 0,
            "observed_expected_points": 0,
            "observed_density_ratio": 0.0,
            "requested_window_ratio": 0.0,
            "observed_start": None,
            "observed_end": None,
            "complete": False,
        }
    observed_expected = max(1, int((timestamps[-1] - timestamps[0]) // step_seconds) + 1)
    density_ratio = min(1.0, len(timestamps) / observed_expected)
    requested_ratio = min(1.0, len(timestamps) / requested_expected)
    return {
        "requested_expected_points": requested_expected,
        "observed_timestamps": len(timestamps),
        "observed_expected_points": observed_expected,
        "observed_density_ratio": round(density_ratio, 4),
        "requested_window_ratio": round(requested_ratio, 4),
        "observed_start": datetime.fromtimestamp(timestamps[0], tz=UTC).isoformat(),
        "observed_end": datetime.fromtimestamp(timestamps[-1], tz=UTC).isoformat(),
        "complete": density_ratio >= 0.70 and len(timestamps) >= 3,
    }


def _prom_error_observation(error: PrometheusReadError, *, summary_prefix: str) -> ToolObservation:
    return ToolObservation(
        status=error.status,
        data={},
        raw_output=None,
        completeness=Completeness.UNKNOWN if error.status != ToolStatus.PARTIAL else Completeness.PARTIAL,
        summary=f"{summary_prefix}：{error.message}",
        error_code=error.code,
        error_message=error.message,
        retryable=error.retryable,
    )


class MemoryUsageVsLimitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_seconds: int = Field(default=15, ge=10, le=120)


class CPUUsageVsRequestLimitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_minutes: int = Field(default=30, ge=15, le=60)
    spike_window_minutes: int = Field(default=10, ge=5, le=20)
    step_seconds: int = Field(default=15, ge=10, le=60)


class CPUThrottlingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_minutes: int = Field(default=15, ge=5, le=30)
    step_seconds: int = Field(default=15, ge=10, le=60)


class GetMemoryUsageVsLimitTool:
    name = "get_memory_usage_vs_limit"
    version = "1.0.0"
    cost_units = 3
    arguments_model = MemoryUsageVsLimitInput

    def __init__(self, client: PrometheusReadClient | None = None) -> None:
        self.client = client or PrometheusReadClient()

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = MemoryUsageVsLimitInput.model_validate(arguments)
        start = target.window_start
        end = _effective_end(target)
        if end <= start:
            return ToolObservation(
                status=ToolStatus.INVALID_REQUEST,
                data={},
                raw_output=None,
                completeness=Completeness.UNKNOWN,
                summary="内存查询窗口尚未开始或已经无效。",
                error_code="PROMETHEUS_RANGE_INVALID",
            )
        scope = target_scope_selector(target)
        resource_scope = resource_scope_selector(target, resource="memory", unit="byte")
        queries = {
            "working_set": f"container_memory_working_set_bytes{{{scope}}}",
            "max_usage": f"container_memory_max_usage_bytes{{{scope}}}",
            "request": f"kube_pod_container_resource_requests{{{resource_scope}}}",
            "limit": f"kube_pod_container_resource_limits{{{resource_scope}}}",
        }
        raw: dict[str, Any] = {"queries": queries, "responses": {}}
        try:
            working_payload = await self.client.query_range(
                query=queries["working_set"], target=target, start=start, end=end, step_seconds=args.step_seconds
            )
            raw["responses"]["working_set"] = working_payload
            max_payload = await self.client.query_range(
                query=queries["max_usage"], target=target, start=start, end=end, step_seconds=args.step_seconds
            )
            raw["responses"]["max_usage"] = max_payload
            request_payload = await self.client.query_instant(query=queries["request"], target=target, at=end)
            raw["responses"]["request"] = request_payload
            limit_payload = await self.client.query_instant(query=queries["limit"], target=target, at=end)
            raw["responses"]["limit"] = limit_payload
        except PrometheusReadError as error:
            observation = _prom_error_observation(error, summary_prefix="Prometheus 内存查询失败")
            observation.raw_output = raw
            return observation

        working_points, working_series, working_unmatched = _matrix_points(working_payload, target)
        max_points, max_series, max_unmatched = _matrix_points(max_payload, target)
        if (working_unmatched or max_unmatched) and not (working_series or max_series):
            return ToolObservation(
                status=ToolStatus.TARGET_UNCERTAIN,
                data={"unmatched_series": working_unmatched + max_unmatched},
                raw_output=raw,
                completeness=Completeness.UNKNOWN,
                summary="Prometheus 返回了同名 Pod 序列，但 Pod UID 与 TargetContext 不一致。",
                error_code="PROMETHEUS_POD_UID_MISMATCH",
            )
        if not working_points and not max_points:
            return ToolObservation(
                status=ToolStatus.NOT_FOUND,
                data={
                    "query_start": start.isoformat(),
                    "query_end": end.isoformat(),
                    "sampling_warning": "没有样本不等同于确认容器未达到内存限制。",
                },
                raw_output=raw,
                completeness=Completeness.COMPLETE,
                summary="Prometheus 查询成功，但目标容器没有内存样本。",
                error_code="PROMETHEUS_NO_MEMORY_SAMPLES",
            )

        request_bytes, _ = _vector_value(request_payload, target)
        limit_bytes, _ = _vector_value(limit_payload, target)
        working_peak = max(working_points, key=lambda item: item["value"]) if working_points else None
        max_usage_peak = max(max_points, key=lambda item: item["value"]) if max_points else None
        peak_candidates = [item for item in [working_peak, max_usage_peak] if item]
        observed_peak = max(peak_candidates, key=lambda item: item["value"])
        peak_source = "max_usage" if max_usage_peak and observed_peak is max_usage_peak else "working_set"
        first_value = working_points[0]["value"] if working_points else None
        last_value = working_points[-1]["value"] if working_points else None
        limit_ratio = observed_peak["value"] / limit_bytes if limit_bytes and limit_bytes > 0 else None
        request_ratio = observed_peak["value"] / request_bytes if request_bytes and request_bytes > 0 else None
        coverage = _coverage(working_points or max_points, start, end, args.step_seconds)
        completeness = Completeness.COMPLETE if coverage["complete"] else Completeness.PARTIAL
        data = {
            "query_start": start.isoformat(),
            "query_end": end.isoformat(),
            "step_seconds": args.step_seconds,
            "sample_count": len(working_points) + len(max_points),
            "series_count": working_series + max_series,
            "working_set_peak_bytes": working_peak["value"] if working_peak else None,
            "max_usage_peak_bytes": max_usage_peak["value"] if max_usage_peak else None,
            "observed_peak_bytes": observed_peak["value"],
            "observed_peak_at": datetime.fromtimestamp(observed_peak["timestamp"], tz=UTC).isoformat(),
            "peak_source": peak_source,
            "first_working_set_bytes": first_value,
            "last_working_set_bytes": last_value,
            "memory_request_bytes": request_bytes,
            "memory_limit_bytes": limit_bytes,
            "peak_request_ratio": round(request_ratio, 6) if request_ratio is not None else None,
            "peak_limit_ratio": round(limit_ratio, 6) if limit_ratio is not None else None,
            "coverage": coverage,
            "sampling_warning": "Prometheus 抓取间隔可能错过终止前的瞬时内存峰值；未观察到达到 limit 不能反证 OOMKilled。",
        }
        ratio_text = f"，占 limit {limit_ratio * 100:.1f}%" if limit_ratio is not None else "，未配置或未获取到内存 limit"
        return ToolObservation(
            status=ToolStatus.FOUND if completeness == Completeness.COMPLETE else ToolStatus.PARTIAL,
            data=data,
            raw_output=raw,
            completeness=completeness,
            summary=f"目标容器观测内存峰值 {observed_peak['value'] / 1024 / 1024:.1f} MiB{ratio_text}。",
            error_code=None if completeness == Completeness.COMPLETE else "PROMETHEUS_SAMPLE_COVERAGE_PARTIAL",
            retryable=False,
        )


class GetCPUUsageVsRequestLimitTool:
    name = "get_cpu_usage_vs_request_limit"
    version = "1.0.0"
    cost_units = 4
    arguments_model = CPUUsageVsRequestLimitInput

    def __init__(self, client: PrometheusReadClient | None = None) -> None:
        self.client = client or PrometheusReadClient()

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = CPUUsageVsRequestLimitInput.model_validate(arguments)
        incident_start = max(target.window_start, target.incident_time - timedelta(minutes=args.spike_window_minutes))
        baseline_start = max(target.window_start, incident_start - timedelta(minutes=args.baseline_minutes))
        end = _effective_end(target)
        if end <= incident_start:
            return ToolObservation(
                status=ToolStatus.INVALID_REQUEST,
                data={},
                raw_output=None,
                completeness=Completeness.UNKNOWN,
                summary="CPU 查询窗口尚未包含事件样本。",
                error_code="PROMETHEUS_RANGE_INVALID",
            )
        scope = target_scope_selector(target)
        resource_scope = resource_scope_selector(target, resource="cpu", unit="core")
        queries = {
            "usage": f"rate(container_cpu_usage_seconds_total{{{scope}}}[1m])",
            "request": f"kube_pod_container_resource_requests{{{resource_scope}}}",
            "limit": f"kube_pod_container_resource_limits{{{resource_scope}}}",
        }
        raw: dict[str, Any] = {"queries": queries, "responses": {}}
        try:
            usage_payload = await self.client.query_range(
                query=queries["usage"], target=target, start=baseline_start, end=end, step_seconds=args.step_seconds
            )
            raw["responses"]["usage"] = usage_payload
            request_payload = await self.client.query_instant(query=queries["request"], target=target, at=end)
            raw["responses"]["request"] = request_payload
            limit_payload = await self.client.query_instant(query=queries["limit"], target=target, at=end)
            raw["responses"]["limit"] = limit_payload
        except PrometheusReadError as error:
            observation = _prom_error_observation(error, summary_prefix="Prometheus CPU 查询失败")
            observation.raw_output = raw
            return observation

        points, matched_series, unmatched_series = _matrix_points(usage_payload, target)
        if unmatched_series and not matched_series:
            return ToolObservation(
                status=ToolStatus.TARGET_UNCERTAIN,
                data={"unmatched_series": unmatched_series},
                raw_output=raw,
                completeness=Completeness.UNKNOWN,
                summary="Prometheus CPU 序列的 Pod UID 与 TargetContext 不一致。",
                error_code="PROMETHEUS_POD_UID_MISMATCH",
            )
        if not points:
            return ToolObservation(
                status=ToolStatus.NOT_FOUND,
                data={"baseline_start": baseline_start.isoformat(), "query_end": end.isoformat()},
                raw_output=raw,
                completeness=Completeness.COMPLETE,
                summary="Prometheus 查询成功，但目标容器没有 CPU 样本。",
                error_code="PROMETHEUS_NO_CPU_SAMPLES",
            )
        baseline_points = [item for item in points if item["timestamp"] < incident_start.timestamp()]
        incident_points = [item for item in points if item["timestamp"] >= incident_start.timestamp()]
        baseline_method = "fixed_target_window"
        if len(baseline_points) < 3 or len(incident_points) < 2:
            # New Pods may not exist in the requested historical part of TargetContext.
            # A bounded split of the same UID series is allowed, but never data from another Pod.
            split_index = max(3, min(len(points) - 3, int(len(points) * 0.45))) if len(points) >= 6 else 0
            if split_index:
                baseline_points = points[:split_index]
                incident_points = points[split_index:]
                incident_start = datetime.fromtimestamp(incident_points[0]["timestamp"], tz=UTC)
                baseline_method = "observed_same_uid_series_split"
        if len(baseline_points) < 3 or len(incident_points) < 2:
            data = {
                "baseline_start": baseline_start.isoformat(),
                "incident_start": incident_start.isoformat(),
                "query_end": end.isoformat(),
                "baseline_method": baseline_method,
                "baseline_sample_count": len(baseline_points),
                "incident_sample_count": len(incident_points),
                "sampling_warning": "CPU 基线或事件窗口样本不足，不能确认 CPU Spike。",
            }
            return ToolObservation(
                status=ToolStatus.PARTIAL,
                data=data,
                raw_output=raw,
                completeness=Completeness.PARTIAL,
                summary="CPU 样本不足，未生成 CPU Spike 确定性结论。",
                error_code="PROMETHEUS_CPU_BASELINE_INSUFFICIENT",
            )

        baseline_values = [item["value"] for item in baseline_points]
        incident_values = [item["value"] for item in incident_points]
        baseline_median = statistics.median(baseline_values)
        baseline_mean = statistics.fmean(baseline_values)
        incident_mean = statistics.fmean(incident_values)
        peak = max(incident_points, key=lambda item: item["value"])
        absolute_delta = peak["value"] - baseline_median
        multiplier = peak["value"] / max(baseline_median, 0.005)
        spike_threshold = max(baseline_median * 2.0, baseline_median + 0.10)
        elevated = [item for item in incident_points if item["value"] >= spike_threshold]
        elevated_duration = 0.0
        if len(elevated) >= 2:
            elevated_duration = max(0.0, elevated[-1]["timestamp"] - elevated[0]["timestamp"])
        spike_detected = bool(
            multiplier >= 2.0
            and absolute_delta >= 0.10
            and len(elevated) >= 2
            and elevated_duration >= min(30.0, args.step_seconds * 2.0)
        )
        request_cores, _ = _vector_value(request_payload, target)
        limit_cores, _ = _vector_value(limit_payload, target)
        request_ratio = peak["value"] / request_cores if request_cores and request_cores > 0 else None
        limit_ratio = peak["value"] / limit_cores if limit_cores and limit_cores > 0 else None
        coverage = _coverage(points, baseline_start, end, args.step_seconds)
        completeness = Completeness.COMPLETE if coverage["complete"] else Completeness.PARTIAL
        data = {
            "baseline_start": baseline_start.isoformat(),
            "incident_start": incident_start.isoformat(),
            "query_end": end.isoformat(),
            "step_seconds": args.step_seconds,
            "baseline_method": baseline_method,
            "baseline_sample_count": len(baseline_points),
            "incident_sample_count": len(incident_points),
            "baseline_median_cores": round(baseline_median, 9),
            "baseline_mean_cores": round(baseline_mean, 9),
            "incident_mean_cores": round(incident_mean, 9),
            "peak_cores": round(peak["value"], 9),
            "peak_at": datetime.fromtimestamp(peak["timestamp"], tz=UTC).isoformat(),
            "absolute_delta_cores": round(absolute_delta, 9),
            "baseline_multiplier": round(multiplier, 6),
            "spike_threshold_cores": round(spike_threshold, 9),
            "elevated_sample_count": len(elevated),
            "elevated_duration_seconds": round(elevated_duration, 3),
            "spike_detected": spike_detected,
            "cpu_request_cores": request_cores,
            "cpu_limit_cores": limit_cores,
            "peak_request_ratio": round(request_ratio, 6) if request_ratio is not None else None,
            "peak_limit_ratio": round(limit_ratio, 6) if limit_ratio is not None else None,
            "coverage": coverage,
        }
        return ToolObservation(
            status=ToolStatus.FOUND if completeness == Completeness.COMPLETE else ToolStatus.PARTIAL,
            data=data,
            raw_output=raw,
            completeness=completeness,
            summary=(
                f"CPU 峰值 {peak['value']:.3f} Core，历史基线中位数 {baseline_median:.3f} Core，"
                f"倍数 {multiplier:.1f}x，{'满足' if spike_detected else '未满足'} CPU Spike 规则。"
            ),
            error_code=None if completeness == Completeness.COMPLETE else "PROMETHEUS_SAMPLE_COVERAGE_PARTIAL",
        )


class GetCPUThrottlingTool:
    name = "get_cpu_throttling"
    version = "1.0.0"
    cost_units = 3
    arguments_model = CPUThrottlingInput

    def __init__(self, client: PrometheusReadClient | None = None) -> None:
        self.client = client or PrometheusReadClient()

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        args = CPUThrottlingInput.model_validate(arguments)
        end = _effective_end(target)
        start = max(target.window_start, target.incident_time - timedelta(minutes=args.window_minutes))
        if end <= start:
            return ToolObservation(
                status=ToolStatus.INVALID_REQUEST,
                data={},
                raw_output=None,
                completeness=Completeness.UNKNOWN,
                summary="CPU throttling 查询窗口无效。",
                error_code="PROMETHEUS_RANGE_INVALID",
            )
        scope = target_scope_selector(target)
        queries = {
            "period_ratio": (
                f"rate(container_cpu_cfs_throttled_periods_total{{{scope}}}[1m]) / "
                f"clamp_min(rate(container_cpu_cfs_periods_total{{{scope}}}[1m]), 0.001)"
            ),
            "seconds_rate": f"rate(container_cpu_cfs_throttled_seconds_total{{{scope}}}[1m])",
        }
        raw: dict[str, Any] = {"queries": queries, "responses": {}}
        try:
            ratio_payload = await self.client.query_range(
                query=queries["period_ratio"], target=target, start=start, end=end, step_seconds=args.step_seconds
            )
            raw["responses"]["period_ratio"] = ratio_payload
            seconds_payload = await self.client.query_range(
                query=queries["seconds_rate"], target=target, start=start, end=end, step_seconds=args.step_seconds
            )
            raw["responses"]["seconds_rate"] = seconds_payload
        except PrometheusReadError as error:
            observation = _prom_error_observation(error, summary_prefix="Prometheus CPU throttling 查询失败")
            observation.raw_output = raw
            return observation

        ratio_points, ratio_series, ratio_unmatched = _matrix_points(ratio_payload, target)
        seconds_points, seconds_series, seconds_unmatched = _matrix_points(seconds_payload, target)
        if (ratio_unmatched or seconds_unmatched) and not (ratio_series or seconds_series):
            return ToolObservation(
                status=ToolStatus.TARGET_UNCERTAIN,
                data={"unmatched_series": ratio_unmatched + seconds_unmatched},
                raw_output=raw,
                completeness=Completeness.UNKNOWN,
                summary="Prometheus throttling 序列的 Pod UID 与 TargetContext 不一致。",
                error_code="PROMETHEUS_POD_UID_MISMATCH",
            )
        if not ratio_points:
            return ToolObservation(
                status=ToolStatus.NOT_FOUND,
                data={
                    "query_start": start.isoformat(),
                    "query_end": end.isoformat(),
                    "seconds_metric_available": bool(seconds_points),
                },
                raw_output=raw,
                completeness=Completeness.COMPLETE,
                summary="Prometheus 未返回目标容器的 CFS periods 指标，无法判断 throttling。",
                error_code="PROMETHEUS_NO_THROTTLING_PERIOD_SAMPLES",
            )
        ratio_values = [max(0.0, item["value"]) for item in ratio_points]
        peak = max(ratio_points, key=lambda item: item["value"])
        average = statistics.fmean(ratio_values)
        observed = [item for item in ratio_points if item["value"] >= 0.05]
        sustained = [item for item in ratio_points if item["value"] >= 0.10]
        sustained_duration = 0.0
        if len(sustained) >= 2:
            sustained_duration = max(0.0, sustained[-1]["timestamp"] - sustained[0]["timestamp"])
        coverage = _coverage(ratio_points, start, end, args.step_seconds)
        completeness = Completeness.COMPLETE if coverage["complete"] else Completeness.PARTIAL
        data = {
            "query_start": start.isoformat(),
            "query_end": end.isoformat(),
            "step_seconds": args.step_seconds,
            "period_ratio_peak": round(max(ratio_values), 6),
            "period_ratio_average": round(average, 6),
            "peak_at": datetime.fromtimestamp(peak["timestamp"], tz=UTC).isoformat(),
            "observed_sample_count": len(observed),
            "sustained_sample_count": len(sustained),
            "sustained_duration_seconds": round(sustained_duration, 3),
            "throttling_observed": len(observed) >= 2,
            "throttling_sustained": len(sustained) >= 3 and sustained_duration >= 30.0,
            "throttled_seconds_rate_peak": max([item["value"] for item in seconds_points], default=None),
            "seconds_metric_available": bool(seconds_points),
            "coverage": coverage,
        }
        return ToolObservation(
            status=ToolStatus.FOUND if completeness == Completeness.COMPLETE else ToolStatus.PARTIAL,
            data=data,
            raw_output=raw,
            completeness=completeness,
            summary=(
                f"CPU throttled periods 峰值比例 {data['period_ratio_peak'] * 100:.1f}% ，"
                f"{'存在持续 throttling' if data['throttling_sustained'] else '未满足持续 throttling 规则'}。"
            ),
            error_code=None if completeness == Completeness.COMPLETE else "PROMETHEUS_SAMPLE_COVERAGE_PARTIAL",
        )
