from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from app.investigation.budget import BudgetLedger
from app.investigation.contracts import InvestigationBudget
from app.investigation.enums import ResolutionQuality, StopReason, ToolStatus
from app.investigation.kubernetes import KubernetesReadClient, KubernetesReadError, classify_http_error
from app.investigation.resolver import _controller_owner, resolve_target_context
from app.investigation.service import is_oom_candidate
from app.models import AlertInstance, Incident


class PagingClient(KubernetesReadClient):
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def list_pods_page(self, namespace: str, *, label_selector=None, continue_token=None, limit=500):
        self.calls.append(continue_token)
        if continue_token is None:
            return ([{"metadata": {"name": f"pod-{i}", "uid": f"uid-{i}"}} for i in range(500)], "next")
        return ([{"metadata": {"name": f"pod-{i}", "uid": f"uid-{i}"}} for i in range(500, 620)], None)


class ResolverClient:
    async def get_pod(self, namespace: str, pod_name: str):
        return {
            "metadata": {
                "name": pod_name,
                "uid": "pod-uid",
                "ownerReferences": [
                    {"kind": "Job", "name": "wrong", "uid": "wrong", "controller": False},
                    {"kind": "ReplicaSet", "name": "payment-rs", "uid": "rs-uid", "controller": True},
                ],
            },
            "spec": {"containers": [{"name": "main"}]},
            "status": {"containerStatuses": [{"name": "main"}]},
        }

    async def get_replicaset(self, namespace: str, name: str):
        return {
            "metadata": {
                "name": name,
                "uid": "rs-uid",
                "ownerReferences": [
                    {"kind": "CronJob", "name": "wrong", "uid": "wrong", "controller": False},
                    {"kind": "Deployment", "name": "payment-api", "uid": "deploy-uid", "controller": True},
                ],
            }
        }

    async def get_deployment(self, namespace: str, name: str):
        raise KubernetesReadError(ToolStatus.UNAVAILABLE, "KUBERNETES_SERVER_ERROR", "server error", True, 500)


class MissingPodClient(ResolverClient):
    async def get_pod(self, namespace: str, pod_name: str):
        raise KubernetesReadError(ToolStatus.NOT_FOUND, "KUBERNETES_NOT_FOUND", "gone", False, 404)


class RuntimeUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_http_error_semantics(self):
        cases = {
            404: (ToolStatus.NOT_FOUND, "KUBERNETES_NOT_FOUND", False),
            403: (ToolStatus.DENIED, "KUBERNETES_ACCESS_DENIED", False),
            429: (ToolStatus.UNAVAILABLE, "RATE_LIMITED", True),
            500: (ToolStatus.UNAVAILABLE, "KUBERNETES_SERVER_ERROR", True),
            400: (ToolStatus.INVALID_REQUEST, "KUBERNETES_INVALID_REQUEST", False),
        }
        for status_code, expected in cases.items():
            error = classify_http_error(status_code)
            self.assertEqual((error.status, error.error_code, error.retryable), expected)

    async def test_pagination_reads_beyond_500_pods(self):
        client = PagingClient()
        pods = await client.list_pods("production")
        self.assertEqual(len(pods), 620)
        self.assertEqual(client.calls, [None, "next"])
        self.assertEqual(pods[-1]["metadata"]["uid"], "uid-619")

    def test_controller_owner_is_preferred(self):
        metadata = {
            "ownerReferences": [
                {"kind": "Job", "name": "not-controller", "uid": "1", "controller": False},
                {"kind": "ReplicaSet", "name": "controller-rs", "uid": "2", "controller": True},
            ]
        }
        owner = _controller_owner(metadata)
        self.assertEqual(owner["name"], "controller-rs")


    async def test_resolver_keeps_high_pod_identity_when_workload_lookup_fails(self):
        incident_time = datetime(2026, 7, 15, 10, 32, tzinfo=UTC)
        incident = Incident(
            grouping_key="g", title="OOMKilled", status="open", severity="critical",
            labels={"namespace": "production", "service": "payment-api"}, alert_count=1,
            first_seen_at=incident_time, last_seen_at=incident_time,
        )
        alert = AlertInstance(
            fingerprint="f", status="firing", alertname="ContainerOOMKilled", severity="critical",
            labels={"namespace": "production", "pod": "payment-pod", "uid": "pod-uid", "container": "main"},
            annotations={}, starts_at=incident_time, last_seen_at=incident_time,
        )
        outcome = await resolve_target_context(incident, [alert], client=ResolverClient())
        self.assertEqual(outcome.status, ToolStatus.FOUND)
        self.assertEqual(outcome.context.resolution_quality, ResolutionQuality.HIGH)
        self.assertEqual(outcome.context.workload_name, "payment-api")
        self.assertTrue(outcome.details["workload_lookup_errors"])
        self.assertIn("Deployment/payment-api metadata unavailable", " ".join(outcome.context.resolution_path))

    def test_oom_candidate_requires_explicit_token(self):
        incident_time = datetime(2026, 7, 15, 10, 32, tzinfo=UTC)
        incident = Incident(
            grouping_key="g", title="Zoom latency warning", status="open", severity="warning",
            labels={}, alert_count=1, first_seen_at=incident_time, last_seen_at=incident_time,
        )
        alert = AlertInstance(
            fingerprint="f", status="firing", alertname="ZoomLatency", severity="warning",
            labels={}, annotations={"summary": "Zoom call is slow"},
            starts_at=incident_time, last_seen_at=incident_time,
        )
        self.assertFalse(is_oom_candidate(incident, [alert]))
        alert.annotations = {"summary": "process terminated: out of memory"}
        self.assertTrue(is_oom_candidate(incident, [alert]))


    async def test_resolver_preserves_not_found_for_deleted_pod(self):
        incident_time = datetime(2026, 7, 15, 10, 32, tzinfo=UTC)
        incident = Incident(
            grouping_key="g", title="OOMKilled", status="open", severity="critical",
            labels={"namespace": "production"}, alert_count=1,
            first_seen_at=incident_time, last_seen_at=incident_time,
        )
        alert = AlertInstance(
            fingerprint="f", status="firing", alertname="ContainerOOMKilled", severity="critical",
            labels={"namespace": "production", "pod": "deleted-pod", "uid": "old-uid", "container": "main"},
            annotations={}, starts_at=incident_time, last_seen_at=incident_time,
        )
        outcome = await resolve_target_context(incident, [alert], client=MissingPodClient())
        self.assertEqual(outcome.status, ToolStatus.NOT_FOUND)
        self.assertIsNone(outcome.context)

    def test_budget_ledger_enforces_deadline_same_tool_and_no_progress(self):
        expired = BudgetLedger(InvestigationBudget(deadline_at=datetime.now(UTC) - timedelta(seconds=1)))
        decision = expired.reserve("tool", 1)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.stop_reason, StopReason.DEADLINE_EXCEEDED)

        limited = BudgetLedger(InvestigationBudget(
            max_same_tool_calls=1,
            deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        ))
        self.assertTrue(limited.reserve("tool", 1).allowed)
        self.assertFalse(limited.reserve("tool", 0).allowed)

        no_progress = BudgetLedger(InvestigationBudget(
            max_no_progress_rounds=1,
            deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        ))
        self.assertTrue(no_progress.reserve("tool-a", 1).allowed)
        no_progress.record_findings(0)
        decision = no_progress.reserve("tool-b", 1)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.stop_reason, StopReason.NO_PROGRESS)


if __name__ == "__main__":
    unittest.main()
