from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from app.investigation.contracts import TargetContext, ToolResult
from app.investigation.enums import Completeness, ResolutionQuality, ToolStatus
from app.investigation.findings.oom import extract_oom_killed_finding
from app.investigation.resolver import select_pod_candidate
from app.investigation.tools.container_status import get_container_termination_status


def target(*, quality: ResolutionQuality = ResolutionQuality.HIGH) -> TargetContext:
    incident_time = datetime(2026, 7, 15, 10, 32, tzinfo=UTC)
    return TargetContext(
        cluster_id="prod-a",
        namespace="production",
        workload_kind="Deployment",
        workload_name="payment-api",
        workload_uid="deployment-uid",
        pod_name="payment-api-abc",
        pod_uid="pod-uid-1",
        container_name="main",
        incident_time=incident_time,
        window_start=incident_time - timedelta(minutes=15),
        window_end=incident_time + timedelta(minutes=30),
        resolution_method="alert_pod_uid",
        resolution_path=["Alert labels", "Pod UID", "ReplicaSet", "Deployment"],
        resolution_quality=quality,
        allowed_namespaces=["production"],
    )


class FakeClient:
    def __init__(self, pod=None, error: Exception | None = None):
        self.pod = pod
        self.error = error

    async def get_pod(self, namespace: str, pod_name: str):
        if self.error:
            raise self.error
        return self.pod


class TrustedOomTests(unittest.IsolatedAsyncioTestCase):
    def test_uid_selection_never_falls_back_to_same_name(self):
        pods = [
            {"metadata": {"name": "payment-api-abc", "uid": "new-uid"}},
        ]
        pod, quality, method, _ = select_pod_candidate(
            pods,
            pod_name="payment-api-abc",
            pod_uid="old-uid",
            service="payment-api",
        )
        self.assertIsNone(pod)
        self.assertEqual(quality, ResolutionQuality.LOW)
        self.assertEqual(method, "alert_pod_uid")

    def test_ambiguous_selector_is_not_a_target(self):
        pods = [
            {"metadata": {"name": "payment-api-a", "uid": "1", "labels": {"app": "payment-api"}}},
            {"metadata": {"name": "payment-api-b", "uid": "2", "labels": {"app": "payment-api"}}},
        ]
        pod, quality, _, message = select_pod_candidate(
            pods, pod_name="", pod_uid="", service="payment-api"
        )
        self.assertIsNone(pod)
        self.assertEqual(quality, ResolutionQuality.LOW)
        self.assertIn("2 个 Pod", message)

    async def test_tool_rejects_same_name_with_different_uid(self):
        pod = {
            "metadata": {"name": "payment-api-abc", "uid": "pod-uid-2"},
            "status": {"containerStatuses": []},
        }
        observation = await get_container_termination_status(target(), client=FakeClient(pod))
        self.assertEqual(observation.status, ToolStatus.TARGET_UNCERTAIN)
        self.assertEqual(observation.error_code, "POD_IDENTITY_MISMATCH")

    async def test_source_failure_is_unavailable_not_not_found(self):
        observation = await get_container_termination_status(
            target(), client=FakeClient(error=TimeoutError("timeout"))
        )
        self.assertEqual(observation.status, ToolStatus.UNAVAILABLE)
        self.assertNotEqual(observation.status, ToolStatus.NOT_FOUND)
        self.assertTrue(observation.retryable)

    def test_high_quality_oom_generates_finding(self):
        ctx = target()
        result = ToolResult(
            execution_id="42",
            status=ToolStatus.FOUND,
            target=ctx.ref(),
            data={
                "restart_count": 3,
                "termination": {
                    "source": "last_state",
                    "reason": "OOMKilled",
                    "exitCode": 137,
                    "finishedAt": "2026-07-15T10:32:17Z",
                },
            },
            completeness=Completeness.COMPLETE,
            model_visible_summary="OOMKilled",
            tool_name="get_container_termination_status",
            tool_version="1.0.0",
            cost_units=1,
            started_at=ctx.incident_time,
            completed_at=ctx.incident_time,
        )
        finding = extract_oom_killed_finding(result, ctx)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.finding_type, "container_oom_killed")
        self.assertEqual(finding.confirmation_rule, "container_oom_killed_v1")
        self.assertEqual(finding.tool_execution_id, "42")

    def test_medium_quality_never_generates_confirmed_finding(self):
        ctx = target(quality=ResolutionQuality.MEDIUM)
        result = ToolResult(
            execution_id="43",
            status=ToolStatus.FOUND,
            target=ctx.ref(),
            data={"termination": {"reason": "OOMKilled", "finishedAt": "2026-07-15T10:32:17Z"}},
            completeness=Completeness.COMPLETE,
            model_visible_summary="OOMKilled",
            tool_name="get_container_termination_status",
            tool_version="1.0.0",
            cost_units=1,
            started_at=ctx.incident_time,
            completed_at=ctx.incident_time,
        )
        self.assertIsNone(extract_oom_killed_finding(result, ctx))

    def test_unavailable_never_generates_negative_or_positive_finding(self):
        ctx = target()
        result = ToolResult(
            execution_id="44",
            status=ToolStatus.UNAVAILABLE,
            target=ctx.ref(),
            data={},
            completeness=Completeness.UNKNOWN,
            model_visible_summary="unavailable",
            tool_name="get_container_termination_status",
            tool_version="1.0.0",
            cost_units=1,
            started_at=ctx.incident_time,
            completed_at=ctx.incident_time,
        )
        self.assertIsNone(extract_oom_killed_finding(result, ctx))


if __name__ == "__main__":
    unittest.main()
