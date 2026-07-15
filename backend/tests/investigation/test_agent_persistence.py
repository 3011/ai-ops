from __future__ import annotations

import json
import os
import unittest
import zlib
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.investigation.agents.audit import ModelInvocationAudit
from app.investigation.agents.evaluation import EVAL_SUITE_VERSION, aggregate_agent_evaluations
from app.investigation.agents.prompts import PROMPT_VERSION
from app.investigation.agents.service import execute_agent_run
from app.worker import process
from app.models import (
    Base,
    InvestigationAgentEvaluation,
    InvestigationAnalysisRun,
    InvestigationArtifact,
    InvestigationDiagnosisResult,
    InvestigationFinding,
    InvestigationModelInvocation,
    InvestigationReplaySnapshot,
    InvestigationToolExecution,
    OutboxJob,
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
            prompt_version=PROMPT_VERSION,
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


class AuditedUnavailableThenFinalModel(AuditedFinalModel):
    def __init__(self, session, finding_id: str) -> None:
        super().__init__(session, finding_id)
        self.turn = 0

    async def invoke(self, *, invocation_type: str, messages: list[dict[str, str]]) -> dict:
        self.turn += 1
        user_payload = json.loads(messages[1]["content"])
        analysis_run_id = int(user_payload["context"]["analysis_run_id"])
        audit = ModelInvocationAudit(self.session, analysis_run_id)
        row, started = await audit.start(
            invocation_type=invocation_type,
            runtime_name="test_structured_model",
            runtime_version="1.0.0",
            provider="test",
            model="scripted",
            model_parameters={"temperature": 0},
            prompt_version=PROMPT_VERSION,
            request_payload={"messages": messages},
        )
        if self.turn == 1:
            response = {
                "action": "tool",
                "tool_call": {
                    "tool_name": "get_cpu_usage_vs_request_limit",
                    "arguments": {"window_minutes": 20},
                    "rationale": "请求一个快照中不存在的参数组合以验证离线边界。",
                },
                "diagnosis": None,
            }
        else:
            response = {
                "action": "final",
                "tool_call": None,
                "diagnosis": {
                    "summary": "快照没有该参数组合，离线 Replay 未访问实时数据源。",
                    "fact_refs": [self.finding_id],
                    "hypotheses": [],
                    "missing_evidence": ["缺少该参数组合的快照结果"],
                    "recommended_checks": [],
                    "risk_notes": ["不得将快照缺失解释为未发生异常"],
                },
            }
        await audit.complete(row, started, response_payload=response)
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

    async def test_offline_unavailable_tool_snapshot_remains_valid_and_bound(self) -> None:
        seed_key = "agent-unavailable"
        parent_id = await ReplayPersistenceTests._seed_run(self, seed_key=seed_key)
        finding_id = f"F-replay-valid-{seed_key}"
        async with SessionLocal() as session:
            child_id = await execute_agent_run(
                session,
                parent_id,
                run_mode="offline_replay",
                model=AuditedUnavailableThenFinalModel(session, finding_id),
            )
            await session.commit()

        async with SessionLocal() as session:
            snapshot = await session.scalar(select(InvestigationReplaySnapshot).where(
                InvestigationReplaySnapshot.analysis_run_id == child_id
            ))
            tool = await session.scalar(select(InvestigationToolExecution).where(
                InvestigationToolExecution.analysis_run_id == child_id,
                InvestigationToolExecution.error_code == "SNAPSHOT_TOOL_NOT_AVAILABLE",
            ))
            self.assertEqual(snapshot.validation_status, "VALID", snapshot.validation_report_json)
            self.assertIsNotNone(tool)
            self.assertEqual(tool.input_json["target"]["pod_uid"], "pod-uid-1")
            self.assertEqual(tool.input_json["scope"]["allowed_namespaces"], ["production"])
            self.assertEqual(tool.cost_units, 0)

    async def test_agent_shadow_outbox_is_processed_by_worker(self) -> None:
        parent_id = await ReplayPersistenceTests._seed_run(self, seed_key="agent-worker-job")
        async with SessionLocal() as session:
            job = OutboxJob(
                job_type="agent_shadow_investigation",
                payload={"parent_run_id": parent_id},
                idempotency_key=f"agent-shadow-test:{parent_id}",
                status="processing",
                attempts=1,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
            await session.commit()

        runner = AsyncMock(return_value=999)
        with patch("app.worker.run_agent_shadow", new=runner):
            await process(job_id)

        runner.assert_awaited_once_with(parent_id)
        async with SessionLocal() as session:
            job = await session.get(OutboxJob, job_id)
            self.assertEqual(job.status, "succeeded")
            self.assertEqual(job.payload["parent_run_id"], parent_id)
            self.assertEqual(job.payload["analysis_run_id"], 999)
            self.assertIsNotNone(job.finished_at)
            self.assertIsNone(job.locked_at)
            self.assertIsNone(job.locked_by)

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
            diagnosis = await session.get(InvestigationDiagnosisResult, child_id)
            evaluation = await session.scalar(select(InvestigationAgentEvaluation).where(
                InvestigationAgentEvaluation.analysis_run_id == child_id,
                InvestigationAgentEvaluation.suite_version == EVAL_SUITE_VERSION,
            ))
            invocation_count = await session.scalar(
                select(func.count()).select_from(InvestigationModelInvocation).where(
                    InvestigationModelInvocation.analysis_run_id == child_id
                )
            )
            parent_finding_ids = set((await session.scalars(select(InvestigationFinding.id).where(
                InvestigationFinding.analysis_run_id == parent_id
            ))).all())
            self.assertEqual((parent.status, parent.stop_reason, dict(parent.target_context_json or {})), original)
            self.assertEqual(child.status, "FAILED")
            self.assertEqual(child.parent_run_id, parent_id)
            self.assertIn("AGENT_MODEL_UNAVAILABLE", child.degradation_reasons)
            self.assertEqual(set(diagnosis.fact_refs_json), parent_finding_ids)
            self.assertEqual(evaluation.status, "EFFECTIVENESS_WARNING")
            self.assertTrue(evaluation.gates_json["oom_hard_fact_consistency_100"])
            self.assertTrue(evaluation.gates_json["model_failure_does_not_change_parent"])
            self.assertFalse(evaluation.metrics_json["model_available"])
            self.assertEqual(invocation_count, 0)


if __name__ == "__main__":
    unittest.main()
