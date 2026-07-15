from __future__ import annotations

from copy import deepcopy
import unittest

from app.investigation.replay import ResultValidator, SNAPSHOT_SCHEMA_VERSION
from app.investigation.tool_runtime import stable_hash


def valid_payload() -> dict:
    target = {
        "cluster_id": "prod-a", "namespace": "production", "pod_name": "payment-api-abc",
        "pod_uid": "pod-uid-1", "container_name": "main", "service_name": "payment-api",
        "workload_kind": "Deployment", "workload_name": "payment-api", "workload_uid": "deploy-uid-1",
        "incident_time": "2026-07-15T10:00:00Z", "window_start": "2026-07-15T09:45:00Z",
        "window_end": "2026-07-15T10:30:00Z", "resolution_method": "alert_pod_uid",
        "resolution_path": ["Alert", "Pod UID"], "resolution_quality": "high",
        "allowed_namespaces": ["production"],
    }
    target_ref = {key: target.get(key) for key in (
        "cluster_id", "namespace", "pod_name", "pod_uid", "container_name", "service_name",
        "workload_kind", "workload_name", "workload_uid",
    )}
    run_input = {
        "mode": "cpu", "tool_catalog_version": "0.9.0-dev.2", "incident_id": 1,
        "incident_labels": {"namespace": "production"}, "first_seen_at": "2026-07-15T10:00:00+00:00",
        "alerts": [],
    }
    finding_id = "F-valid"
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "tool_catalog_version": "0.9.0-dev.2",
        "raw_data_included": False,
        "run_input": run_input,
        "run_input_source_hash": stable_hash(run_input),
        "incident_context": {"id": 1, "alerts": []},
        "analysis_run": {
            "id": 10, "incident_id": 1, "status": "COMPLETED_PARTIAL",
            "stop_reason": "AGENT_NOT_ENABLED", "degradation_reasons": ["AGENT_NOT_ENABLED"],
            "engine": "deterministic_cpu_v1", "engine_version": "0.9.0-dev.2",
            "input_snapshot_hash": stable_hash(run_input), "target_context": target,
            "budget": {}, "budget_usage": {}, "started_at": "2026-07-15T10:00:00Z",
            "completed_at": "2026-07-15T10:01:00Z",
        },
        "tool_executions": [{
            "execution_id": "100", "sequence_number": 1,
            "tool": {"name": "get_cpu_usage_vs_request_limit", "version": "1.0.0"},
            "status": "FOUND",
            "input": {"target": target_ref, "target_identity": target, "arguments": {}, "scope": {"allowed_namespaces": ["production"]}},
            "result": {
                "summary": "CPU spike", "status": "FOUND", "completeness": "complete",
                "finding_ids": [finding_id], "error_code": None, "is_truncated": False,
                "structured_data": {"peak_cores": 1.0}, "cost_units": 4, "retryable": False,
                "reused_execution_id": None, "raw_artifact_uri": None, "raw_artifact_hash": "abc",
            },
            "started_at": "2026-07-15T10:00:00Z", "completed_at": "2026-07-15T10:00:01Z",
        }],
        "findings": [{
            "id": finding_id, "finding_type": "container_cpu_spike", "subject": target_ref,
            "value": {"peak_cores": 1.0}, "polarity": "positive", "quality": "high",
            "event_time": "2026-07-15T10:00:00Z", "tool_execution_id": "100",
            "parser_version": "1.0.0", "confirmation_rule": "container_cpu_spike_v1",
        }],
        "diagnosis": {
            "summary": "confirmed", "fact_refs": [finding_id], "hypotheses": [],
            "missing_evidence": [], "recommended_checks": [], "risk_notes": [],
            "degradation_reasons": ["AGENT_NOT_ENABLED"], "analysis_mode": "deterministic_cpu_v1",
            "validated_output": {},
        },
    }


class ReplayValidatorTests(unittest.TestCase):
    def validate(self, payload: dict):
        return ResultValidator().validate(payload, expected_input_hash=payload["analysis_run"]["input_snapshot_hash"])

    def test_valid_snapshot(self):
        report = self.validate(valid_payload())
        self.assertEqual(report.status, "VALID")
        self.assertFalse(report.errors)

    def test_uid_tampering_is_invalid(self):
        payload = valid_payload()
        payload["tool_executions"][0]["input"]["target"]["pod_uid"] = "other-uid"
        report = self.validate(payload)
        self.assertEqual(report.status, "INVALID")
        self.assertIn("TOOL_TARGET_MISMATCH", {item.code for item in report.errors})

    def test_unknown_finding_type_is_invalid(self):
        payload = valid_payload()
        payload["findings"][0]["finding_type"] = "deployment_caused_incident"
        report = self.validate(payload)
        self.assertIn("FINDING_TYPE_NOT_ALLOWED", {item.code for item in report.errors})

    def test_dangling_fact_ref_is_invalid(self):
        payload = valid_payload()
        payload["diagnosis"]["fact_refs"] = ["F-missing"]
        report = self.validate(payload)
        codes = {item.code for item in report.errors}
        self.assertIn("DIAGNOSIS_DANGLING_FACT_REF", codes)
        self.assertIn("DIAGNOSIS_FACT_SET_MISMATCH", codes)

    def test_log_finding_requires_untrusted_marker(self):
        payload = valid_payload()
        payload["tool_executions"][0]["tool"] = {"name": "search_container_logs", "version": "1.0.0"}
        payload["findings"][0]["finding_type"] = "runtime_error_log_observed"
        payload["findings"][0]["value"] = {"matched_line_count": 1, "untrusted_input": False}
        report = self.validate(payload)
        self.assertIn("LOG_FINDING_NOT_MARKED_UNTRUSTED", {item.code for item in report.errors})

    def test_raw_response_key_is_invalid(self):
        payload = valid_payload()
        payload["tool_executions"][0]["result"]["raw_response"] = {"secret": "x"}
        report = self.validate(payload)
        self.assertIn("RAW_DATA_EXPOSED", {item.code for item in report.errors})

    def test_tool_finding_reference_mismatch_is_invalid(self):
        payload = valid_payload()
        payload["tool_executions"][0]["result"]["finding_ids"] = []
        report = self.validate(payload)
        self.assertIn("TOOL_FINDING_REF_MISMATCH", {item.code for item in report.errors})

    def test_truncated_high_quality_is_warning(self):
        payload = valid_payload()
        payload["tool_executions"][0]["result"]["is_truncated"] = True
        report = self.validate(payload)
        self.assertEqual(report.status, "VALID_WITH_WARNINGS")
        self.assertIn("TRUNCATED_SOURCE_HIGH_QUALITY_FINDING", {item.code for item in report.warnings})

    def test_snapshot_is_deterministic(self):
        first = valid_payload()
        second = deepcopy(first)
        self.assertEqual(stable_hash(first), stable_hash(second))


if __name__ == "__main__":
    unittest.main()
