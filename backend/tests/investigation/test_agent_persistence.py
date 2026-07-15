from __future__ import annotations

import json
import os
import unittest
import zlib

from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.investigation.agents.audit import ModelInvocationAudit
from app.investigation.agents.evaluation import EVAL_SUITE_VERSION, aggregate_agent_evaluations
from app.investigation.agents.service import execute_agent_run
from app.models import (
    Base,
    InvestigationAgentEvaluation,
    InvestigationAnalysisRun,
    InvestigationArtifact,
    InvestigationDiagnosisResult,
    InvestigationModelInvocation,
    InvestigationReplaySnapshot,
)
from investigation.test_replay_persistence import ReplayPersistenceTests


class AuditedFinalModel:
    def __init__(self, session, finding_id: str) -> None:
        self.session = session
        self.finding_id = finding_id

    async def invoke(self, *, invocation_type: str, messages: list[dict[str, str]]) -> dict:
        user_payload = json.loads(messages[1]["content"])
        analysis_run_id = int(user_payload["context"]["analysis_run_id"])
        audit = ModelInvocationAudit(self.session, analysis_run_id)
        request = {"messages": messages, "api_key": "TEST_VALUE"}
        row, started = await audit.start(
            invocation_type=invocation_type,
            runtime_name="test_structured_model",
            runtime_version="1.0.0",
            provider="test",
            model="scripted",
            model_parameters={"temperature": 0},
            prompt_version="agent-investigation-v1",
            request_payload=request,
        )
        response = {
            "action": "final",
            "tool_call": None,
            "diagnosis": {
                "summary": "父级确定性 Finding 支持一个受限假设；离线 Replay 未访问实时数据源。",
                "fact_refs": [self.finding_id],
                "hypotheses": [{
                    "id": "H-1",
                    "statement": "事件窗口内的 CPU 压力可能与服务风险相关。",
                    "support_level": "partially_supported",
                    "fact_refs": [self.finding_id],
                    "contradicting_fact_refs": [self.finding_id],
                    "rationale": "只引用 Snapshot 中的父级 Finding，并保留不确定性。",
                }],
                "missing_evidence": ["应用级 profile"],
                "recommended_checks": ["核对 RED 指标"],
                "risk_notes": ["不得将假设解释为根因确认"],
            },
        }
        await audit.complete(row, started, response_payload=response, input_tokens=100, output_tokens=80)
        return response


class FailingModel:
    async def invoke(self, *, invocation_type: str, messages: list[dict[str, str]]) -> dict:
        raise RuntimeError("model unavailable for safety regression")


@unittest.skipUnless(os.getenv("AIOPS_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class AgentPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def test_offline_agent_run_persists_audit_replay_and_evaluation(self) -> None:
        seed_key = "agent-offline"
        parent_id = await ReplayPersistenceTests._seed_run(self, seed_key=seed_key)
        finding_id = f"F-replay-valid-{seed_key}"
        async with SessionLocal() as session:
            parent_before = await session.get(InvestigationAnalysisRun, parent_id)
            parent_status = parent_before.status
            child_id = await execute_agent_run(
                session,
                parent_id,
                run_mode="offline_replay",
                model=AuditedFinalModel(session, finding_id),
            )
            await session.commit()

        async with SessionLocal() as session:
            parent = await session.get(InvestigationAnalysisRun, parent_id)
            child = await session.get(InvestigationAnalysisRun, child_id)
            invocation = await session.scalar(select(InvestigationModelInvocation).where(
                InvestigationModelInvocation.analysis_run_id == child_id
            ))
            snapshot = await session.scalar(select(InvestigationReplaySnapshot).where(
                InvestigationReplaySnapshot.analysis_run_id == child_id
            ))
            evaluation = await session.scalar(select(InvestigationAgentEvaluation).where(
                InvestigationAgentEvaluation.analysis_run_id == child_id
            ))
            self.assertEqual(parent.status, parent_status)
            self.assertEqual(child.parent_run_id, parent_id)
            self.assertEqual(child.run_kind, "agent_offline")
            self.assertEqual(child.status, "COMPLETED")
            self.assertEqual(child.agent_validation_status, "VALID")
            self.assertEqual(invocation.status, "SUCCEEDED")
            self.assertTrue(invocation.request_snapshot_uri.startswith("db://investigation_artifacts/"))
            self.assertTrue(invocation.response_snapshot_uri.startswith("db://investigation_artifacts/"))
            request_artifact_id = invocation.request_snapshot_uri.rsplit("/", 1)[-1]
            request_artifact = await session.get(InvestigationArtifact, request_artifact_id)
            request_json = json.loads(zlib.decompress(request_artifact.content))
            self.assertEqual(request_json["api_key"], "[REDACTED]")
            self.assertNotIn("TEST_VALUE", json.dumps(request_json))
            self.assertEqual(snapshot.validation_status, "VALID")
            self.assertEqual(evaluation.suite_version, EVAL_SUITE_VERSION)
            self.assertEqual(evaluation.status, "PASS")
            self.assertTrue(evaluation.gates_json["offline_replay_no_external_data_access"])

    async def test_aggregate_evaluation_reports_release_gates(self) -> None:
        seed_key = "agent-aggregate"
        parent_id = await ReplayPersistenceTests._seed_run(self, seed_key=seed_key)
        finding_id = f"F-replay-valid-{seed_key}"
        async with SessionLocal() as session:
            await execute_agent_run(
                session,
                parent_id,
                run_mode="offline_replay",
                model=AuditedFinalModel(session, finding_id),
            )
            summary = await aggregate_agent_evaluations(session)
            await session.commit()
            self.assertEqual(summary["sample_size"], 1)
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(summary["gates"]["offline_replay_no_external_data_access"])
            self.assertTrue(summary["gates"]["prompt_injection_behavior_change_zero"])
            self.assertTrue(summary["gates"]["budget_exhaustion_rate_10"])

    async def test_invalid_agent_output_is_audited_but_not_persisted_as_diagnosis(self) -> None:
        parent_id = await ReplayPersistenceTests._seed_run(self, seed_key="agent-invalid")
        async with SessionLocal() as session:
            child_id = await execute_agent_run(
                session,
                parent_id,
                run_mode="offline_replay",
                model=AuditedFinalModel(session, "F-invented"),
            )
            await session.commit()

        async with SessionLocal() as session:
            child = await session.get(InvestigationAnalysisRun, child_id)
            diagnosis = await session.get(InvestigationDiagnosisResult, child_id)
            invocation = await session.scalar(select(InvestigationModelInvocation).where(
                InvestigationModelInvocation.analysis_run_id == child_id
            ))
            evaluation = await session.scalar(select(InvestigationAgentEvaluation).where(
                InvestigationAgentEvaluation.analysis_run_id == child_id
            ))
            self.assertEqual(child.status, "INCONCLUSIVE")
            self.assertEqual(child.agent_validation_status, "INVALID")
            self.assertIsNone(diagnosis)
            self.assertEqual(invocation.status, "SUCCEEDED")
            self.assertEqual(evaluation.status, "FAIL")
            self.assertFalse(evaluation.gates_json["no_unknown_finding_reference"])

    async def test_model_failure_creates_failed_child_without_changing_parent(self) -> None:
        parent_id = await ReplayPersistenceTests._seed_run(self, seed_key="agent-failure")
        async with SessionLocal() as session:
            parent = await session.get(InvestigationAnalysisRun, parent_id)
            original = (parent.status, parent.stop_reason, dict(parent.target_context_json or {}))
            child_id = await execute_agent_run(
                session,
                parent_id,
                run_mode="offline_replay",
                model=FailingModel(),
            )
            await session.commit()

        async with SessionLocal() as session:
            parent = await session.get(InvestigationAnalysisRun, parent_id)
            child = await session.get(InvestigationAnalysisRun, child_id)
            invocation_count = await session.scalar(
                select(func.count()).select_from(InvestigationModelInvocation).where(
                    InvestigationModelInvocation.analysis_run_id == child_id
                )
            )
            self.assertEqual((parent.status, parent.stop_reason, dict(parent.target_context_json or {})), original)
            self.assertEqual(child.status, "FAILED")
            self.assertEqual(child.parent_run_id, parent_id)
            self.assertIn("AGENT_MODEL_UNAVAILABLE", child.degradation_reasons)
            self.assertEqual(invocation_count, 0)


if __name__ == "__main__":
    unittest.main()
