from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import unittest

import httpx

from app.investigation.contracts import TargetContext, ToolResult
from app.investigation.enums import Completeness, ResolutionQuality, ToolStatus
from app.investigation.findings.resources import (
    parse_cpu_throttling_findings,
    parse_cpu_usage_findings,
    parse_memory_usage_findings,
)
from app.investigation.prometheus import PrometheusReadClient, PrometheusReadError
from app.investigation.tools.prometheus_resources import (
    CPUThrottlingInput,
    CPUUsageVsRequestLimitInput,
    GetCPUThrottlingTool,
    GetCPUUsageVsRequestLimitTool,
    GetMemoryUsageVsLimitTool,
    MemoryUsageVsLimitInput,
)


def make_target(*, uid: str = "pod-uid-1", quality: ResolutionQuality = ResolutionQuality.HIGH) -> TargetContext:
    incident_time = datetime.now(UTC) - timedelta(minutes=1)
    return TargetContext(
        cluster_id="prod-a",
        namespace="production",
        workload_kind="Deployment",
        workload_name="payment-api",
        workload_uid="deployment-uid",
        pod_name="payment-api-abc",
        pod_uid=uid,
        container_name="main",
        incident_time=incident_time,
        window_start=incident_time - timedelta(minutes=15),
        window_end=incident_time + timedelta(minutes=30),
        resolution_method="alert_pod_uid",
        resolution_path=["Alert", "Pod UID"],
        resolution_quality=quality,
        allowed_namespaces=["production"],
    )


def matrix(metric: dict[str, str], values: list[tuple[float, float]]) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": metric, "values": [[ts, str(value)] for ts, value in values]}],
        },
    }


def vector(metric: dict[str, str], value: float) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": metric, "value": [datetime.now(UTC).timestamp(), str(value)]}],
        },
    }


def empty_matrix() -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": []}}


def empty_vector() -> dict:
    return {"status": "success", "data": {"resultType": "vector", "result": []}}


class FakePrometheusClient:
    def __init__(self, target: TargetContext, *, uid: str | None = None) -> None:
        self.target = target
        self.uid = uid or target.pod_uid
        self.calls: list[str] = []
        self.override: dict[str, dict] = {}

    def _metric(self) -> dict[str, str]:
        return {
            "namespace": self.target.namespace,
            "pod": self.target.pod_name,
            "container": self.target.container_name,
            "id": "/kubepods/pod" + self.uid.replace("-", "_") + "/container",
        }

    def _resource_metric(self) -> dict[str, str]:
        return {
            "namespace": self.target.namespace,
            "pod": self.target.pod_name,
            "container": self.target.container_name,
            "uid": self.uid,
        }

    async def query_range(self, *, query: str, target: TargetContext, start: datetime, end: datetime, step_seconds: int):
        self.calls.append(query)
        for key, payload in self.override.items():
            if key in query:
                return payload
        timestamps = []
        current = start.timestamp()
        while current <= end.timestamp():
            timestamps.append(current)
            current += step_seconds
        if "container_memory_working_set_bytes" in query:
            total = max(1, len(timestamps) - 1)
            return matrix(self._metric(), [(ts, 8 * 1024 * 1024 + (i / total) * 23 * 1024 * 1024) for i, ts in enumerate(timestamps)])
        if "container_memory_max_usage_bytes" in query:
            return matrix(self._metric(), [(ts, 31.7 * 1024 * 1024) for ts in timestamps])
        if "container_cpu_usage_seconds_total" in query:
            incident_start = target.incident_time - timedelta(minutes=10)
            return matrix(self._metric(), [(ts, 0.01 if ts < incident_start.timestamp() else 0.19) for ts in timestamps])
        if "container_cpu_cfs_throttled_periods_total" in query:
            return matrix(self._metric(), [(ts, 0.48) for ts in timestamps])
        if "container_cpu_cfs_throttled_seconds_total" in query:
            return empty_matrix()
        raise AssertionError(f"unexpected range query: {query}")

    async def query_instant(self, *, query: str, target: TargetContext, at: datetime):
        self.calls.append(query)
        metric = self._resource_metric()
        if 'resource="memory"' in query and "resource_requests" in query:
            return vector(metric, 8 * 1024 * 1024)
        if 'resource="memory"' in query and "resource_limits" in query:
            return vector(metric, 32 * 1024 * 1024)
        if 'resource="cpu"' in query and "resource_requests" in query:
            return vector(metric, 0.05)
        if 'resource="cpu"' in query and "resource_limits" in query:
            return vector(metric, 0.2)
        return empty_vector()


class LatePodPrometheusClient(FakePrometheusClient):
    async def query_range(self, *, query: str, target: TargetContext, start: datetime, end: datetime, step_seconds: int):
        self.calls.append(query)
        if "container_cpu_usage_seconds_total" in query:
            # Pod appears late in the TargetContext window: first half idle, second half busy.
            first = target.incident_time - timedelta(minutes=3)
            values = []
            for index in range(16):
                timestamp = (first + timedelta(seconds=index * step_seconds)).timestamp()
                values.append((timestamp, 0.01 if index < 7 else 0.18))
            return matrix(self._metric(), values)
        return await super().query_range(
            query=query, target=target, start=start, end=end, step_seconds=step_seconds
        )


def as_result(observation, target: TargetContext, tool_name: str) -> ToolResult:
    now = datetime.now(UTC)
    return ToolResult(
        execution_id="42",
        status=observation.status,
        target=target.ref(),
        data=observation.data,
        finding_ids=[],
        completeness=observation.completeness,
        model_visible_summary=observation.summary,
        tool_name=tool_name,
        tool_version="1.0.0",
        cost_units=1,
        started_at=now,
        completed_at=now,
        error_code=observation.error_code,
        retryable=observation.retryable,
    )


class PrometheusClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_violation_is_denied_before_http(self):
        target = make_target()
        client = PrometheusReadClient(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("HTTP must not run"))))
        with self.assertRaises(PrometheusReadError) as caught:
            await client.query_range(
                query='rate(container_cpu_usage_seconds_total{namespace="other",pod="x",container="y"}[1m])',
                target=target,
                start=target.window_start,
                end=target.incident_time,
                step_seconds=15,
            )
        self.assertEqual(caught.exception.status, ToolStatus.DENIED)
        self.assertEqual(caught.exception.code, "PROMETHEUS_SCOPE_VIOLATION")

    async def test_http_failure_semantics(self):
        target = make_target()
        query = 'container_memory_working_set_bytes{namespace="production",pod="payment-api-abc",container="main"}'
        cases = [
            (403, ToolStatus.DENIED, "PROMETHEUS_ACCESS_DENIED", False),
            (429, ToolStatus.UNAVAILABLE, "PROMETHEUS_RATE_LIMITED", True),
            (500, ToolStatus.UNAVAILABLE, "PROMETHEUS_SERVER_ERROR", True),
        ]
        for status_code, status, code, retryable in cases:
            transport = httpx.MockTransport(lambda request, sc=status_code: httpx.Response(sc, request=request, json={"error": "x"}))
            client = PrometheusReadClient(transport=transport)
            with self.assertRaises(PrometheusReadError) as caught:
                await client.query_range(
                    query=query,
                    target=target,
                    start=target.window_start,
                    end=target.incident_time,
                    step_seconds=15,
                )
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.retryable, retryable)

    async def test_timeout_is_retryable_unavailable(self):
        target = make_target()
        query = 'container_memory_working_set_bytes{namespace="production",pod="payment-api-abc",container="main"}'
        def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)
        client = PrometheusReadClient(transport=httpx.MockTransport(handler))
        with self.assertRaises(PrometheusReadError) as caught:
            await client.query_range(
                query=query,
                target=target,
                start=target.window_start,
                end=target.incident_time,
                step_seconds=15,
            )
        self.assertEqual(caught.exception.status, ToolStatus.UNAVAILABLE)
        self.assertEqual(caught.exception.code, "PROMETHEUS_TIMEOUT")
        self.assertTrue(caught.exception.retryable)

    async def test_high_cardinality_response_is_rejected(self):
        target = make_target()
        query = 'container_memory_working_set_bytes{namespace="production",pod="payment-api-abc",container="main"}'
        payload = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {"metric": {"id": f"series-{i}"}, "values": [[target.incident_time.timestamp(), "1"]]}
                    for i in range(3)
                ],
            },
        }
        client = PrometheusReadClient(max_series=2, transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request, json=payload)))
        with self.assertRaises(PrometheusReadError) as caught:
            await client.query_range(
                query=query,
                target=target,
                start=target.window_start,
                end=target.incident_time,
                step_seconds=15,
            )
        self.assertEqual(caught.exception.code, "PROMETHEUS_HIGH_CARDINALITY")


class PrometheusToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_limit_and_increase_findings(self):
        target = make_target()
        tool = GetMemoryUsageVsLimitTool(FakePrometheusClient(target))
        observation = await tool.execute(target, MemoryUsageVsLimitInput(step_seconds=15))
        self.assertIn(observation.status, {ToolStatus.FOUND, ToolStatus.PARTIAL})
        self.assertGreater(observation.data["peak_limit_ratio"], 0.98)
        findings = parse_memory_usage_findings(as_result(observation, target, tool.name), target)
        types = {item.finding_type for item in findings}
        self.assertIn("memory_limit_reached", types)
        self.assertIn("memory_usage_increased", types)

    async def test_cpu_spike_request_limit_and_throttling_findings(self):
        target = make_target()
        client = FakePrometheusClient(target)
        cpu_tool = GetCPUUsageVsRequestLimitTool(client)
        cpu_observation = await cpu_tool.execute(
            target,
            CPUUsageVsRequestLimitInput(baseline_minutes=30, spike_window_minutes=10, step_seconds=15),
        )
        self.assertTrue(cpu_observation.data["spike_detected"])
        cpu_types = {item.finding_type for item in parse_cpu_usage_findings(as_result(cpu_observation, target, cpu_tool.name), target)}
        self.assertEqual(
            cpu_types,
            {"container_cpu_spike", "cpu_request_saturated", "cpu_near_limit"},
        )

        throttling_tool = GetCPUThrottlingTool(client)
        throttling_observation = await throttling_tool.execute(
            target,
            CPUThrottlingInput(window_minutes=15, step_seconds=15),
        )
        self.assertTrue(throttling_observation.data["throttling_sustained"])
        throttle_types = {item.finding_type for item in parse_cpu_throttling_findings(as_result(throttling_observation, target, throttling_tool.name), target)}
        self.assertEqual(throttle_types, {"cpu_throttling_sustained"})


    async def test_late_created_pod_uses_same_uid_series_split(self):
        target = make_target()
        client = LatePodPrometheusClient(target)
        tool = GetCPUUsageVsRequestLimitTool(client)
        observation = await tool.execute(
            target,
            CPUUsageVsRequestLimitInput(baseline_minutes=30, spike_window_minutes=10, step_seconds=15),
        )
        self.assertEqual(observation.data["baseline_method"], "observed_same_uid_series_split")
        self.assertTrue(observation.data["spike_detected"])
        types = {item.finding_type for item in parse_cpu_usage_findings(as_result(observation, target, tool.name), target)}
        self.assertIn("container_cpu_spike", types)

    async def test_same_name_different_uid_is_target_uncertain(self):
        target = make_target()
        client = FakePrometheusClient(target, uid="different-uid")
        tool = GetMemoryUsageVsLimitTool(client)
        observation = await tool.execute(target, MemoryUsageVsLimitInput())
        self.assertEqual(observation.status, ToolStatus.TARGET_UNCERTAIN)
        self.assertEqual(observation.error_code, "PROMETHEUS_POD_UID_MISMATCH")
        self.assertEqual(parse_memory_usage_findings(as_result(observation, target, tool.name), target), [])

    async def test_no_samples_do_not_generate_negative_findings(self):
        target = make_target()
        client = FakePrometheusClient(target)
        client.override = {
            "container_memory_working_set_bytes": empty_matrix(),
            "container_memory_max_usage_bytes": empty_matrix(),
        }
        tool = GetMemoryUsageVsLimitTool(client)
        observation = await tool.execute(target, MemoryUsageVsLimitInput())
        self.assertEqual(observation.status, ToolStatus.NOT_FOUND)
        self.assertIn("不等同", json.dumps(observation.data, ensure_ascii=False))
        self.assertEqual(parse_memory_usage_findings(as_result(observation, target, tool.name), target), [])

    async def test_medium_quality_target_never_generates_resource_facts(self):
        target = make_target(quality=ResolutionQuality.MEDIUM)
        client = FakePrometheusClient(target)
        tool = GetCPUUsageVsRequestLimitTool(client)
        observation = await tool.execute(target, CPUUsageVsRequestLimitInput())
        self.assertEqual(parse_cpu_usage_findings(as_result(observation, target, tool.name), target), [])


if __name__ == "__main__":
    unittest.main()
