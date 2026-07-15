from __future__ import annotations
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.worker import calculate_coverage, compact_pod, generator_promql, validate_dynamic_plan


class EvidencePlannerTests(unittest.TestCase):
    def test_generator_promql_from_url(self):
        alert = SimpleNamespace(annotations={"_generator_url": "http://prom/graph?g0.expr=vector%281%29&g0.tab=1"})
        self.assertEqual(generator_promql([alert]), "vector(1)")

    def test_compact_pod_detects_crashloop_and_oom(self):
        pod = {
            "metadata": {"name": "oom-pod", "namespace": "aiops-dev", "labels": {}},
            "spec": {"nodeName": "worker-1"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "False"}],
                "containerStatuses": [{
                    "name": "app", "ready": False, "restartCount": 3, "image": "app:v1",
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                }],
            },
        }
        result = compact_pod(pod)
        text = " ".join(result["issues"])
        self.assertIn("CrashLoopBackOff", text)
        self.assertIn("OOMKilled", text)
        self.assertIn("重启 3 次", text)


    def test_dynamic_plan_requires_scope(self):
        plan = {"prometheus_queries": [{"name": "global", "query": "sum(up)", "reason": "too broad"}], "loki_queries": []}
        prom, logs, rejected = validate_dynamic_plan(plan, {"namespace": "aiops-dev", "service": "orders", "pods": [], "node": "", "instance": "", "job": ""})
        self.assertEqual(prom, [])
        self.assertTrue(rejected)


    def test_dynamic_plan_rejects_complex_logql_pipeline(self):
        plan = {"prometheus_queries": [], "loki_queries": [{"name": "unsafe_complex", "query": '{namespace="aiops-dev"} | regexp "(?P<x>.*)" | unwrap x', "reason": "complex parser"}]}
        prom, logs, rejected = validate_dynamic_plan(plan, {"namespace": "aiops-dev", "service": "orders", "pods": [], "node": "", "instance": "", "job": ""})
        self.assertEqual(logs, [])
        self.assertTrue(any("简单行过滤" in item for item in rejected))

    def test_dynamic_plan_accepts_scoped_query(self):
        plan = {"prometheus_queries": [{"name": "errors", "query": 'sum(rate(http_requests_total{namespace="aiops-dev",service="orders",status=~"5.."}[5m]))', "reason": "check errors"}], "loki_queries": [{"name": "timeouts", "query": '{namespace="aiops-dev",pod=~"orders-.*"} |= "timeout"', "reason": "check timeout logs"}]}
        prom, logs, rejected = validate_dynamic_plan(plan, {"namespace": "aiops-dev", "service": "orders", "pods": ["orders-abc"], "node": "", "instance": "", "job": ""})
        self.assertEqual(len(prom), 1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(rejected, [])

    def test_coverage_degrades_without_target(self):
        start = datetime.now(UTC)
        evidence = [
            {"source_type": "prometheus", "summary": {"query_name": "original_alert_expression", "sample_count": 1}, "error": None},
            {"source_type": "kubernetes", "summary": {"discovered_pods": [], "node": None}, "error": None},
        ]
        coverage = calculate_coverage(evidence, start, start + timedelta(minutes=30))
        self.assertIn(coverage["level"], ("low", "medium"))
        self.assertIn("未发现明确的 Kubernetes 目标", coverage["missing"])


if __name__ == "__main__":
    unittest.main()
