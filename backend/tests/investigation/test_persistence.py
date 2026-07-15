from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.investigation.contracts import TargetContext
from app.investigation.enums import Completeness, ResolutionQuality, ToolStatus
from app.investigation.resolver import ResolutionOutcome
from app.investigation.service import run_oom_investigation
from app.investigation.tools.container_status import ContainerStatusObservation
from app.models import (
    AlertInstance,
    Base,
    Incident,
    IncidentAlert,
    InvestigationAnalysisRun,
    InvestigationDiagnosisResult,
    InvestigationFinding,
    InvestigationToolExecution,
    ModelSettings,
)


@unittest.skipUnless(os.getenv("AIOPS_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class InvestigationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await engine.dispose()

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

        target = TargetContext(
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
            resolution_quality=ResolutionQuality.HIGH,
            allowed_namespaces=["production"],
        )
        resolution = ResolutionOutcome(ToolStatus.FOUND, target, "resolved", {})
        observation = ContainerStatusObservation(
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
            raw_output={"metadata": {"uid": target.pod_uid}, "container_status": {"name": "main"}},
            completeness=Completeness.COMPLETE,
            summary="OOMKilled",
        )
        with patch("app.investigation.service.resolve_target_context", new=AsyncMock(return_value=resolution)), patch(
            "app.investigation.tool_runtime.get_container_termination_status",
            new=AsyncMock(return_value=observation),
        ):
            run_id = await run_oom_investigation(incident_id)

        self.assertIsNotNone(run_id)
        async with SessionLocal() as session:
            run = await session.get(InvestigationAnalysisRun, run_id)
            tool = await session.scalar(select(InvestigationToolExecution).where(InvestigationToolExecution.analysis_run_id == run_id))
            finding = await session.scalar(select(InvestigationFinding).where(InvestigationFinding.analysis_run_id == run_id))
            diagnosis = await session.get(InvestigationDiagnosisResult, run_id)
            model_settings = await session.get(ModelSettings, 1)
            self.assertFalse(model_settings.enabled)
            self.assertEqual(run.status, "COMPLETED_PARTIAL")
            self.assertEqual(run.target_context_json["pod_uid"], "pod-uid-1")
            self.assertEqual(tool.status, "FOUND")
            self.assertTrue(tool.raw_artifact_hash)
            self.assertEqual(finding.tool_execution_id, tool.id)
            self.assertEqual(finding.confirmation_rule, "container_oom_killed_v1")
            self.assertIn(finding.id, diagnosis.fact_refs_json)
            self.assertIn("AGENT_NOT_ENABLED", diagnosis.degradation_reasons_json)


if __name__ == "__main__":
    unittest.main()
