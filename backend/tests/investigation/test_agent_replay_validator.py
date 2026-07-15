from __future__ import annotations

from copy import deepcopy
import unittest

from app.investigation.replay import ResultValidator
from investigation.test_replay_validator import valid_payload


def valid_agent_offline_payload() -> dict:
    payload = deepcopy(valid_payload())
    source_finding = payload["findings"][0]
    source_execution_id = payload["tool_executions"][0]["execution_id"]
    payload["source_findings"] = [source_finding]
    payload["findings"] = []
    payload["analysis_run"].update({
        "id": 11,
        "parent_run_id": 10,
        "run_kind": "agent_offline",
        "source_snapshot_id": "R-source",
        "agent_validation_status": "VALID",
        "agent_validation_report": {
            "status": "VALID",
            "validator_version": "1.0.0",
            "checks": {"fact_refs_exist": True},
            "errors": [],
            "warnings": [],
        },
        "engine": "agent_cpu_offline_replay_v1",
        "engine_version": "0.9.0",
        "status": "COMPLETED",
    })
    payload["tool_executions"][0]["execution_id"] = "201"
    payload["tool_executions"][0]["result"]["reused_execution_id"] = source_execution_id
    payload["tool_executions"][0]["result"]["cost_units"] = 0
    payload["diagnosis"].update({
        "analysis_mode": "agent_cpu_offline_replay_v1",
        "hypotheses": [{
            "id": "H-1",
            "statement": "CPU 配额压力可能与事件相关。",
            "support_level": "partially_supported",
            "fact_refs": [source_finding["id"]],
            "contradicting_fact_refs": [source_finding["id"]],
            "rationale": "只引用父级确定性 Finding。",
        }],
        "fact_refs": [source_finding["id"]],
    })
    payload["model_invocations"] = [{
        "id": 1,
        "sequence_number": 1,
        "invocation_type": "investigation_step",
        "runtime_name": "openai_compatible_json",
        "runtime_version": "1.0.0",
        "provider": "openai-compatible",
        "model": "test-model",
        "model_parameters": {},
        "prompt_version": "agent-investigation-v1",
        "request_snapshot_uri": "db://investigation_artifacts/A-request",
        "request_hash": "request-hash",
        "response_snapshot_uri": "db://investigation_artifacts/A-response",
        "response_hash": "response-hash",
        "status": "SUCCEEDED",
        "started_at": "2026-07-15T10:00:00Z",
        "completed_at": "2026-07-15T10:00:01Z",
    }]
    return payload


class AgentReplayValidatorTests(unittest.TestCase):
    def test_valid_offline_agent_snapshot_reuses_source_finding(self) -> None:
        payload = valid_agent_offline_payload()
        report = ResultValidator().validate(
            payload,
            expected_input_hash=payload["analysis_run"]["input_snapshot_hash"],
        )
        self.assertEqual(report.status, "VALID", report.model_dump())

    def test_offline_agent_cannot_invent_snapshot_finding(self) -> None:
        payload = valid_agent_offline_payload()
        payload["tool_executions"][0]["result"]["finding_ids"] = ["F-invented"]
        report = ResultValidator().validate(
            payload,
            expected_input_hash=payload["analysis_run"]["input_snapshot_hash"],
        )
        self.assertEqual(report.status, "INVALID")
        self.assertIn("SNAPSHOT_TOOL_FINDING_REF_UNKNOWN", {item.code for item in report.errors})

    def test_agent_snapshot_requires_model_artifact_audit(self) -> None:
        payload = valid_agent_offline_payload()
        payload["model_invocations"] = []
        report = ResultValidator().validate(
            payload,
            expected_input_hash=payload["analysis_run"]["input_snapshot_hash"],
        )
        self.assertEqual(report.status, "INVALID")
        self.assertIn("MODEL_INVOCATION_AUDIT_MISSING", {item.code for item in report.errors})


if __name__ == "__main__":
    unittest.main()
