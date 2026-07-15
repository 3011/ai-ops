from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
import zlib

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.investigation.artifacts import ArtifactPolicy
from app.investigation.budget import BudgetLedger
from app.investigation.contracts import InvestigationBudget, TargetContext, ToolObservation
from app.investigation.enums import Completeness, InvestigationStatus, ResolutionQuality, ToolStatus
from app.investigation.findings.oom import parse_oom_killed_findings
from app.investigation.registry import ToolRegistry
from app.investigation.resolver import ResolutionOutcome
from app.investigation.service import run_oom_investigation
from app.investigation.tool_runtime import ToolRuntime
from app.models import (
    AlertInstance,
    Base,
    Incident,
    IncidentAlert,
    InvestigationAnalysisRun,
    InvestigationArtifact,
    InvestigationDiagnosisResult,
    InvestigationFinding,
    InvestigationToolExecution,
    ModelSettings,
)


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlexibleArguments(BaseModel):
    model_config = ConfigDict(extra="allow")


class ScopeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = "both"


class FakeOomTool:
    name = "get_container_termination_status"
    version = "1.0.0"
    cost_units = 1
    arguments_model = ScopeArguments

    def __init__(self, *, raises: bool = False) -> None:
        self.calls = 0
        self.raises = raises

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        self.calls += 1
        if self.raises:
            raise RuntimeError("simulated tool failure")
        return ToolObservation(
            status=ToolStatus.FOUND,
            data={
                "pod_name": target.pod_name,
                "pod_uid": target.pod_uid,
                "container_name": target.container_name,
                "restart_count": 4,
                "termination": {
                    "source": "last_state",
                    "state": "terminated",
                    "reason": "OOMKilled",
                    "exitCode": 137,
                    "finishedAt": "2026-07-15T10:32:17Z",
                },
            },
            raw_output={
                "metadata": {"uid": target.pod_uid},
                "container_status": {"name": target.container_name},
            },
            completeness=Completeness.COMPLETE,
            summary="OOMKilled",
        )




class SafeNotFoundTool:
    version = "1.0.0"
    cost_units = 1
    arguments_model = FlexibleArguments

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        self.calls += 1
        return ToolObservation(
            status=ToolStatus.NOT_FOUND,
            data={"capability_gap": self.name},
            raw_output={"status": "success"},
            completeness=Completeness.COMPLETE,
            summary=f"{self.name} no data",
            error_code=f"{self.name.upper()}_NO_DATA",
        )


class ExpensiveTool(FakeOomTool):
    name = "expensive_tool"
    version = "1.0.0"
    cost_units = 2
    arguments_model = EmptyArguments


class LargeTool:
    name = "large_tool"
    version = "1.0.0"
    cost_units = 1
    arguments_model = EmptyArguments

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        self.calls += 1
        return ToolObservation(
            status=ToolStatus.FOUND,
            data={"samples": list(range(40)), "note": "x" * 200},
            raw_output={
                "Authorization": "Bearer should-not-survive",
                "rows": [{"value": i, "password": "secret-value"} for i in range(40)],
            },
            completeness=Completeness.COMPLETE,
            summary="large result",
        )


def make_target(*, pod_uid: str = "pod-uid-1", quality: ResolutionQuality = ResolutionQuality.HIGH) -> TargetContext:
    incident_time = datetime(2026, 7, 15, 10, 32, tzinfo=UTC)
    return TargetContext(
        cluster_id="prod-a",
        namespace="production",
        workload_kind="Deployment",
        workload_name="payment-api",
        workload_uid="deployment-uid",
        pod_name="payment-api-abc",
        pod_uid=pod_uid,
        container_name="main",
        incident_time=incident_time,
        window_start=incident_time - timedelta(minutes=15),
        window_end=incident_time + timedelta(minutes=30),
        resolution_method="alert_pod_uid",
        resolution_path=["Alert labels", "Pod UID", "ReplicaSet", "Deployment"],
        resolution_quality=quality,
        allowed_namespaces=["production"],
    )


@unittest.skipUnless(os.getenv("AIOPS_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class InvestigationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def _create_incident(self, *, title: str = "[critical] payment-api - OOMKilled") -> int:
        incident_time = datetime(2026, 7, 15, 10, 32, tzinfo=UTC)
        async with SessionLocal() as session:
            incident = Incident(
                grouping_key=f"prod-a|production|payment-api|{title}",
                title=title,
                status="open",
                severity="critical",
                labels={"cluster": "prod-a", "namespace": "production", "service": "payment-api"},
                alert_count=1,
                first_seen_at=incident_time,
                last_seen_at=incident_time,
            )
            session.add(incident)
            await session.flush()
            await session.commit()
            return incident.id

    async def _create_run(self, session, incident_id: int, budget: InvestigationBudget) -> InvestigationAnalysisRun:
        run = InvestigationAnalysisRun(
            incident_id=incident_id,
            status=InvestigationStatus.RUNNING.value,
            engine="test_runtime",
            engine_version="1",
            budget_json=budget.model_dump(mode="json"),
            budget_usage_json={},
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        return run

    async def test_oom_vertical_slice_persists_full_trace(self) -> None:
        incident_time = datetime(2026, 7, 15, 10, 32, tzinfo=UTC)
        async with SessionLocal() as session:
            session.add(ModelSettings(
                id=1,
                provider="openai-compatible",
                base_url="https://model.invalid",
                model="disabled-model",
                enabled=False,
            ))
            incident = Incident(
                grouping_key="prod-a|production|payment-api|prod",
                title="[critical] payment-api - OOMKilled",
                status="open",
                severity="critical",
                labels={"cluster": "prod-a", "namespace": "production", "service": "payment-api"},
                alert_count=1,
                first_seen_at=incident_time,
                last_seen_at=incident_time,
            )
            session.add(incident)
            await session.flush()
            alert = AlertInstance(
                fingerprint="oom-test",
                status="firing",
                alertname="ContainerOOMKilled",
                severity="critical",
                labels={
                    "namespace": "production",
                    "service": "payment-api",
                    "pod": "payment-api-abc",
                    "uid": "pod-uid-1",
                    "container": "main",
                    "reason": "OOMKilled",
                },
                annotations={"summary": "OOMKilled"},
                starts_at=incident_time,
                last_seen_at=incident_time,
            )
            session.add(alert)
            await session.flush()
            session.add(IncidentAlert(incident_id=incident.id, alert_instance_id=alert.id))
            await session.commit()
            incident_id = incident.id

        target = make_target()
        resolution = ResolutionOutcome(ToolStatus.FOUND, target, "resolved", {})
        fake_tool = FakeOomTool()
        registry = ToolRegistry()
        registry.register(fake_tool, parsers=[parse_oom_killed_findings])
        supplemental = [
            SafeNotFoundTool("get_memory_usage_vs_limit"),
            SafeNotFoundTool("get_container_restart_history"),
            SafeNotFoundTool("get_recent_rollouts"),
            SafeNotFoundTool("search_container_logs"),
        ]
        for item in supplemental:
            registry.register(item)
        with patch("app.investigation.service.resolve_target_context", new=AsyncMock(return_value=resolution)), patch(
            "app.investigation.service.build_default_registry",
            return_value=registry,
        ):
            run_id = await run_oom_investigation(incident_id)

        self.assertIsNotNone(run_id)
        self.assertEqual(fake_tool.calls, 1)
        async with SessionLocal() as session:
            run = await session.get(InvestigationAnalysisRun, run_id)
            tool = await session.scalar(select(InvestigationToolExecution).where(InvestigationToolExecution.analysis_run_id == run_id))
            finding = await session.scalar(select(InvestigationFinding).where(InvestigationFinding.analysis_run_id == run_id))
            diagnosis = await session.get(InvestigationDiagnosisResult, run_id)
            model_settings = await session.get(ModelSettings, 1)
            self.assertFalse(model_settings.enabled)
            self.assertEqual(run.status, "COMPLETED_PARTIAL")
            self.assertEqual(run.target_context_json["pod_uid"], "pod-uid-1")
            self.assertEqual(run.budget_usage_json["tool_calls_used"], 5)
            self.assertEqual(tool.status, "FOUND")
            self.assertTrue(tool.raw_artifact_hash)
            self.assertEqual(finding.tool_execution_id, tool.id)
            self.assertEqual(finding.confirmation_rule, "container_oom_killed_v1")
            self.assertIn(finding.id, diagnosis.fact_refs_json)
            self.assertIn(finding.id, tool.model_visible_output_json["finding_ids"])
            self.assertIn("AGENT_NOT_ENABLED", diagnosis.degradation_reasons_json)

    async def test_cache_reuses_result_and_isolated_by_target(self) -> None:
        incident_id = await self._create_incident()
        tool = FakeOomTool()
        registry = ToolRegistry()
        registry.register(tool, parsers=[parse_oom_killed_findings])
        budget = InvestigationBudget(
            max_same_tool_calls=5,
            deadline_at=datetime.now(UTC) + timedelta(minutes=2),
        )
        ledger = BudgetLedger(budget)
        async with SessionLocal() as session:
            run = await self._create_run(session, incident_id, budget)
            runtime = ToolRuntime(session, registry=registry, budget=ledger)
            first = await runtime.execute(
                analysis_run_id=run.id, sequence_number=1, target=make_target(),
                tool_name=tool.name, arguments={"scope": "both"},
            )
            second = await runtime.execute(
                analysis_run_id=run.id, sequence_number=2, target=make_target(),
                tool_name=tool.name, arguments={"scope": "both"},
            )
            third = await runtime.execute(
                analysis_run_id=run.id, sequence_number=3, target=make_target(pod_uid="pod-uid-2"),
                tool_name=tool.name, arguments={"scope": "both"},
            )
            fourth = await runtime.execute(
                analysis_run_id=run.id, sequence_number=4,
                target=make_target(quality=ResolutionQuality.MEDIUM),
                tool_name=tool.name, arguments={"scope": "both"},
            )
            await session.commit()
            self.assertEqual(tool.calls, 3)
            self.assertEqual(second.reused_execution_id, first.execution_id)
            self.assertEqual(second.cost_units, 0)
            self.assertEqual(second.finding_ids, first.finding_ids)
            self.assertIsNone(third.reused_execution_id)
            self.assertNotEqual(third.finding_ids, first.finding_ids)
            self.assertIsNone(fourth.reused_execution_id)
            self.assertEqual(fourth.finding_ids, [])
            finding_count = await session.scalar(select(func.count()).select_from(InvestigationFinding))
            execution_count = await session.scalar(select(func.count()).select_from(InvestigationToolExecution))
            self.assertEqual(finding_count, 2)
            self.assertEqual(execution_count, 4)

    async def test_budget_rejection_does_not_call_data_source(self) -> None:
        incident_id = await self._create_incident()
        tool = ExpensiveTool()
        registry = ToolRegistry()
        registry.register(tool)
        budget = InvestigationBudget(
            max_total_cost_units=1,
            deadline_at=datetime.now(UTC) + timedelta(minutes=2),
        )
        async with SessionLocal() as session:
            run = await self._create_run(session, incident_id, budget)
            runtime = ToolRuntime(session, registry=registry, budget=BudgetLedger(budget))
            result = await runtime.execute(
                analysis_run_id=run.id, sequence_number=1, target=make_target(),
                tool_name=tool.name, arguments={},
            )
            await session.commit()
            self.assertEqual(tool.calls, 0)
            self.assertEqual(result.status, ToolStatus.BUDGET_EXCEEDED)
            self.assertEqual(result.error_code, "COST_BUDGET_EXHAUSTED")
            row = await session.get(InvestigationToolExecution, int(result.execution_id))
            self.assertEqual(row.status, "BUDGET_EXCEEDED")
            self.assertEqual(row.cost_units, 0)

    async def test_unknown_tool_and_tool_exception_are_audited(self) -> None:
        incident_id = await self._create_incident()
        registry = ToolRegistry()
        failing = FakeOomTool(raises=True)
        registry.register(failing)
        budget = InvestigationBudget(deadline_at=datetime.now(UTC) + timedelta(minutes=2))
        async with SessionLocal() as session:
            run = await self._create_run(session, incident_id, budget)
            runtime = ToolRuntime(session, registry=registry, budget=BudgetLedger(budget))
            unknown = await runtime.execute(
                analysis_run_id=run.id, sequence_number=1, target=make_target(),
                tool_name="not_registered", arguments={},
            )
            failed = await runtime.execute(
                analysis_run_id=run.id, sequence_number=2, target=make_target(),
                tool_name=failing.name, arguments={"scope": "both"},
            )
            await session.commit()
            self.assertEqual(unknown.status, ToolStatus.INVALID_REQUEST)
            self.assertEqual(unknown.error_code, "UNKNOWN_TOOL")
            self.assertEqual(failed.status, ToolStatus.UNAVAILABLE)
            self.assertEqual(failed.error_code, "TOOL_EXECUTION_EXCEPTION")
            self.assertEqual(runtime.budget.tool_calls_used, 2)
            self.assertEqual(runtime.budget.total_cost_units_used, 1)
            self.assertEqual(runtime.budget.no_progress_rounds, 2)
            rows = list(await session.scalars(select(InvestigationToolExecution).order_by(InvestigationToolExecution.sequence_number)))
            self.assertEqual(len(rows), 2)

    async def test_large_artifact_is_redacted_stored_and_marked_truncated(self) -> None:
        incident_id = await self._create_incident()
        tool = LargeTool()
        registry = ToolRegistry()
        registry.register(tool)
        budget = InvestigationBudget(deadline_at=datetime.now(UTC) + timedelta(minutes=2))
        policy = ArtifactPolicy(
            max_inline_raw_bytes=100,
            max_collection_items=5,
            max_string_chars=20,
            max_depth=5,
        )
        async with SessionLocal() as session:
            run = await self._create_run(session, incident_id, budget)
            runtime = ToolRuntime(
                session, registry=registry, budget=BudgetLedger(budget), artifact_policy=policy,
            )
            result = await runtime.execute(
                analysis_run_id=run.id, sequence_number=1, target=make_target(),
                tool_name=tool.name, arguments={},
            )
            await session.commit()
            self.assertEqual(result.status, ToolStatus.PARTIAL)
            self.assertTrue(result.is_truncated)
            self.assertTrue(result.raw_artifact_uri)
            row = await session.get(InvestigationToolExecution, int(result.execution_id))
            self.assertTrue(row.is_truncated)
            artifact_id = row.raw_artifact_uri.rsplit("/", 1)[-1]
            artifact = await session.get(InvestigationArtifact, artifact_id)
            full = zlib.decompress(artifact.content).decode()
            decoded = json.loads(full)
            self.assertIn("structured_output", decoded)
            self.assertIn("raw_output", decoded)
            self.assertIn("[REDACTED]", full)
            self.assertNotIn("should-not-survive", full)
            self.assertNotIn("secret-value", full)
            self.assertEqual(artifact.sha256, row.raw_artifact_hash)


if __name__ == "__main__":
    unittest.main()
