from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.investigation.contracts import TargetContext, ToolObservation
from app.investigation.enums import Completeness, ResolutionQuality, ToolStatus
from app.investigation.findings.oom import parse_oom_killed_findings
from app.investigation.findings.gate3 import (
    parse_log_findings,
    parse_red_findings,
    parse_replica_cpu_findings,
    parse_restart_findings,
    parse_rollout_findings,
)
from app.investigation.findings.resources import (
    parse_cpu_throttling_findings,
    parse_cpu_usage_findings,
    parse_memory_usage_findings,
)
from app.investigation.registry import ToolRegistry
from app.investigation.resolver import ResolutionOutcome
from app.investigation.service import run_cpu_investigation, run_oom_investigation
from app.models import (
    AlertInstance,
    Base,
    Incident,
    IncidentAlert,
    InvestigationAnalysisRun,
    InvestigationDiagnosisResult,
    InvestigationFinding,
    InvestigationReplaySnapshot,
    InvestigationToolExecution,
    ModelSettings,
)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class FakeTool:
    version = "1.0.0"
    cost_units = 1
    arguments_model = EmptyArgs

    def __init__(self, name: str, observation: ToolObservation) -> None:
        self.name = name
        self.observation = observation
        self.calls = 0

    async def execute(self, target: TargetContext, arguments: BaseModel) -> ToolObservation:
        self.calls += 1
        return self.observation


def target() -> TargetContext:
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
        service_name="payment-api",
        incident_time=incident_time,
        window_start=incident_time - timedelta(minutes=15),
        window_end=incident_time + timedelta(minutes=30),
        resolution_method="alert_pod_uid",
        resolution_path=["Alert", "Pod UID", "Deployment"],
        resolution_quality=ResolutionQuality.HIGH,
        allowed_namespaces=["production"],
    )


@unittest.skipUnless(os.getenv("AIOPS_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class ResourcePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with SessionLocal() as session:
            session.add(ModelSettings(
                id=1,
                provider="openai-compatible",
                base_url="https://model.invalid",
                model="disabled-model",
                enabled=False,
            ))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def _incident(self, *, alertname: str, title: str, annotations: dict[str, str]) -> int:
        when = target().incident_time
        async with SessionLocal() as session:
            incident = Incident(
                grouping_key=f"prod-a|production|payment-api|{alertname}",
                title=title,
                status="open",
                severity="critical",
                labels={"cluster": "prod-a", "namespace": "production", "service": "payment-api"},
                alert_count=1,
                first_seen_at=when,
                last_seen_at=when,
            )
            session.add(incident)
            await session.flush()
            alert = AlertInstance(
                fingerprint=f"fp-{alertname}",
                status="firing",
                alertname=alertname,
                severity="critical",
                labels={
                    "namespace": "production",
                    "service": "payment-api",
                    "pod": "payment-api-abc",
                    "uid": "pod-uid-1",
                    "container": "main",
                    **({"reason": "OOMKilled"} if "OOM" in alertname else {}),
                },
                annotations=annotations,
                starts_at=when,
                last_seen_at=when,
            )
            session.add(alert)
            await session.flush()
            session.add(IncidentAlert(incident_id=incident.id, alert_instance_id=alert.id))
            await session.commit()
            return incident.id

    async def test_oom_run_persists_termination_and_memory_findings(self) -> None:
        incident_id = await self._incident(
            alertname="ContainerOOMKilled",
            title="[critical] payment-api - OOMKilled",
            annotations={"summary": "OOMKilled"},
        )
        ctx = target()
        termination = FakeTool("get_container_termination_status", ToolObservation(
            status=ToolStatus.FOUND,
            data={
                "restart_count": 3,
                "termination": {
                    "source": "last_state",
                    "reason": "OOMKilled",
                    "exitCode": 137,
                    "finishedAt": "2026-07-15T10:32:17Z",
                },
            },
            raw_output={"metadata": {"uid": ctx.pod_uid}},
            completeness=Completeness.COMPLETE,
            summary="OOMKilled",
        ))
        memory = FakeTool("get_memory_usage_vs_limit", ToolObservation(
            status=ToolStatus.FOUND,
            data={
                "observed_peak_bytes": 33_000_000,
                "observed_peak_at": "2026-07-15T10:32:10Z",
                "peak_source": "max_usage",
                "memory_limit_bytes": 33_554_432,
                "peak_limit_ratio": 0.9835,
                "first_working_set_bytes": 8_000_000,
                "sampling_warning": "sampled",
            },
            raw_output={"status": "success"},
            completeness=Completeness.COMPLETE,
            summary="near limit",
        ))
        restart = FakeTool("get_container_restart_history", ToolObservation(
            status=ToolStatus.FOUND,
            data={"current_restart_count": 4, "window_restart_delta": 3, "restart_event_count": 3,
                  "restart_events": [{"time": "2026-07-15T10:32:18Z"}], "repeated": True},
            raw_output={"status": "success"}, completeness=Completeness.COMPLETE, summary="restarts",
        ))
        rollout = FakeTool("get_recent_rollouts", ToolObservation(
            status=ToolStatus.FOUND,
            data={"rollout_preceded_incident": True, "minutes_before_incident": 7,
                  "nearest_rollout_before_incident": "2026-07-15T10:25:00Z",
                  "recent_replicaset_count": 1, "recent_change_count": 1,
                  "revision_changed": True, "previous_revision": 1, "current_revision": 2,
                  "image_changed": True, "previous_images": ["v1"], "current_images": ["v2"],
                  "no_recent_rollout": False},
            raw_output={"status": "success"}, completeness=Completeness.COMPLETE, summary="rollout",
        ))
        logs = FakeTool("search_container_logs", ToolObservation(
            status=ToolStatus.FOUND,
            data={"category_counts": {"oom": 1, "runtime_error": 1}, "prompt_injection_redacted_count": 1},
            raw_output={"untrusted_input": True}, completeness=Completeness.COMPLETE, summary="logs",
        ))
        registry = ToolRegistry()
        registry.register(termination, parsers=[parse_oom_killed_findings])
        registry.register(memory, parsers=[parse_memory_usage_findings])
        registry.register(restart, parsers=[parse_restart_findings])
        registry.register(rollout, parsers=[parse_rollout_findings])
        registry.register(logs, parsers=[parse_log_findings])
        resolution = ResolutionOutcome(ToolStatus.FOUND, ctx, "resolved", {})
        with patch("app.investigation.service.resolve_target_context", new=AsyncMock(return_value=resolution)), patch(
            "app.investigation.service.build_default_registry", return_value=registry
        ):
            run_id = await run_oom_investigation(incident_id)
        self.assertIsNotNone(run_id)
        async with SessionLocal() as session:
            run = await session.get(InvestigationAnalysisRun, run_id)
            tools = list(await session.scalars(select(InvestigationToolExecution).where(InvestigationToolExecution.analysis_run_id == run_id).order_by(InvestigationToolExecution.sequence_number)))
            findings = list(await session.scalars(select(InvestigationFinding).where(InvestigationFinding.analysis_run_id == run_id)))
            diagnosis = await session.get(InvestigationDiagnosisResult, run_id)
            replay = await session.scalar(select(InvestigationReplaySnapshot).where(InvestigationReplaySnapshot.analysis_run_id == run_id))
            self.assertIsNotNone(replay)
            self.assertEqual(replay.validation_status, "VALID")
            self.assertEqual(replay.snapshot_json["analysis_run"]["id"], run_id)
            self.assertIsNotNone(run.run_input_json)
            self.assertEqual(run.run_input_source_mode, "native_frozen")
            self.assertEqual(run.run_input_schema_version, "1.0.0")
            self.assertEqual(run.run_input_source_hash, run.input_snapshot_hash)
            self.assertEqual(replay.snapshot_json["run_input_source_mode"], "native_frozen")
            self.assertEqual(replay.snapshot_json["run_input_source_hash"], run.run_input_source_hash)
            self.assertEqual(run.engine, "deterministic_oom_v2")
            self.assertEqual(run.status, "COMPLETED_PARTIAL")
            self.assertEqual(len(tools), 5)
            types = {item.finding_type for item in findings}
            self.assertIn("container_oom_killed", types)
            self.assertIn("memory_limit_reached", types)
            self.assertIn("container_repeatedly_restarted", types)
            self.assertIn("rollout_preceded_incident", types)
            self.assertIn("revision_changed", types)
            self.assertIn("image_changed", types)
            self.assertIn("oom_log_observed", types)
            self.assertNotIn("rollout_caused_incident", types)
            self.assertEqual(set(diagnosis.fact_refs_json), {item.id for item in findings})
            self.assertTrue(all(tool.model_visible_output_json["finding_ids"] for tool in tools))
            self.assertEqual(run.budget_usage_json["tool_calls_used"], 5)
            self.assertEqual([termination.calls, memory.calls, restart.calls, rollout.calls, logs.calls], [1, 1, 1, 1, 1])

    async def test_cpu_run_persists_spike_and_throttling_findings(self) -> None:
        incident_id = await self._incident(
            alertname="ContainerCPUSpike",
            title="[critical] payment-api - CPU Spike",
            annotations={"summary": "High CPU spike"},
        )
        ctx = target()
        cpu = FakeTool("get_cpu_usage_vs_request_limit", ToolObservation(
            status=ToolStatus.FOUND,
            data={
                "spike_detected": True,
                "baseline_median_cores": 0.02,
                "peak_cores": 0.19,
                "peak_at": "2026-07-15T10:32:10Z",
                "baseline_multiplier": 9.5,
                "absolute_delta_cores": 0.17,
                "elevated_duration_seconds": 90,
                "cpu_request_cores": 0.05,
                "cpu_limit_cores": 0.2,
                "peak_request_ratio": 3.8,
                "peak_limit_ratio": 0.95,
            },
            raw_output={"status": "success"},
            completeness=Completeness.COMPLETE,
            summary="CPU spike",
        ))
        throttling = FakeTool("get_cpu_throttling", ToolObservation(
            status=ToolStatus.FOUND,
            data={
                "period_ratio_peak": 0.48,
                "period_ratio_average": 0.40,
                "peak_at": "2026-07-15T10:32:11Z",
                "throttling_sustained": True,
                "throttling_observed": True,
                "sustained_duration_seconds": 90,
                "seconds_metric_available": False,
            },
            raw_output={"status": "success"},
            completeness=Completeness.COMPLETE,
            summary="throttled",
        ))
        restart = FakeTool("get_container_restart_history", ToolObservation(
            status=ToolStatus.FOUND,
            data={"current_restart_count": 0, "window_restart_delta": 0, "restart_event_count": 0,
                  "restart_events": [], "repeated": False},
            raw_output={"status": "success"}, completeness=Completeness.COMPLETE, summary="stable",
        ))
        rollout = FakeTool("get_recent_rollouts", ToolObservation(
            status=ToolStatus.FOUND,
            data={"rollout_preceded_incident": True, "minutes_before_incident": 7,
                  "nearest_rollout_before_incident": "2026-07-15T10:25:00Z",
                  "recent_replicaset_count": 1, "recent_change_count": 1,
                  "revision_changed": True, "previous_revision": 1, "current_revision": 2,
                  "image_changed": False, "no_recent_rollout": False},
            raw_output={"status": "success"}, completeness=Completeness.COMPLETE, summary="rollout",
        ))
        logs = FakeTool("search_container_logs", ToolObservation(
            status=ToolStatus.FOUND,
            data={"category_counts": {"runtime_error": 1, "cpu_hot_loop_hint": 1}, "prompt_injection_redacted_count": 1},
            raw_output={"untrusted_input": True}, completeness=Completeness.COMPLETE, summary="logs",
        ))
        replicas = FakeTool("compare_cpu_across_replicas", ToolObservation(
            status=ToolStatus.FOUND,
            data={"replica_count": 3, "anomalous_pods": ["payment-api-abc"],
                  "median_incident_cpu_cores": 0.1, "max_incident_cpu_cores": 0.6,
                  "single_replica_anomaly": True, "subset_replicas_anomaly": False,
                  "all_replicas_increased": False, "new_revision_cpu_higher": True,
                  "latest_revision": 2, "revision_means": {"1": 0.1, "2": 0.4}},
            raw_output={"status": "success"}, completeness=Completeness.COMPLETE, summary="replicas",
        ))
        red = FakeTool("get_application_red_metrics", ToolObservation(
            status=ToolStatus.FOUND,
            data={"request_rate_stable": True, "request_rate_increased": False,
                  "error_rate_increased": True, "latency_increased": True,
                  "signals": {"request_rate": {"baseline_mean": 10, "incident_mean": 10.2},
                              "error_rate": {"baseline_mean": 0.003, "incident_mean": 0.05},
                              "latency": {"baseline_mean": 0.18, "incident_mean": 1.2}}},
            raw_output={"status": "success"}, completeness=Completeness.COMPLETE, summary="red",
        ))
        registry = ToolRegistry()
        registry.register(cpu, parsers=[parse_cpu_usage_findings])
        registry.register(throttling, parsers=[parse_cpu_throttling_findings])
        registry.register(restart, parsers=[parse_restart_findings])
        registry.register(rollout, parsers=[parse_rollout_findings])
        registry.register(logs, parsers=[parse_log_findings])
        registry.register(replicas, parsers=[parse_replica_cpu_findings])
        registry.register(red, parsers=[parse_red_findings])
        resolution = ResolutionOutcome(ToolStatus.FOUND, ctx, "resolved", {})
        with patch("app.investigation.service.resolve_target_context", new=AsyncMock(return_value=resolution)), patch(
            "app.investigation.service.build_default_registry", return_value=registry
        ):
            run_id = await run_cpu_investigation(incident_id)
        self.assertIsNotNone(run_id)
        async with SessionLocal() as session:
            run = await session.get(InvestigationAnalysisRun, run_id)
            tools = list(await session.scalars(select(InvestigationToolExecution).where(InvestigationToolExecution.analysis_run_id == run_id).order_by(InvestigationToolExecution.sequence_number)))
            findings = list(await session.scalars(select(InvestigationFinding).where(InvestigationFinding.analysis_run_id == run_id)))
            diagnosis = await session.get(InvestigationDiagnosisResult, run_id)
            replay = await session.scalar(select(InvestigationReplaySnapshot).where(InvestigationReplaySnapshot.analysis_run_id == run_id))
            self.assertIsNotNone(replay)
            self.assertEqual(replay.validation_status, "VALID")
            self.assertEqual(replay.snapshot_json["analysis_run"]["id"], run_id)
            self.assertIsNotNone(run.run_input_json)
            self.assertEqual(run.run_input_source_mode, "native_frozen")
            self.assertEqual(run.run_input_schema_version, "1.0.0")
            self.assertEqual(run.run_input_source_hash, run.input_snapshot_hash)
            self.assertEqual(replay.snapshot_json["run_input_source_mode"], "native_frozen")
            self.assertEqual(replay.snapshot_json["run_input_source_hash"], run.run_input_source_hash)
            self.assertEqual(run.engine, "deterministic_cpu_v1")
            self.assertEqual(len(tools), 7)
            types = {item.finding_type for item in findings}
            self.assertIn("container_cpu_spike", types)
            self.assertIn("cpu_request_saturated", types)
            self.assertIn("cpu_near_limit", types)
            self.assertIn("cpu_throttling_sustained", types)
            self.assertIn("container_restart_stable", types)
            self.assertIn("rollout_preceded_incident", types)
            self.assertIn("runtime_error_log_observed", types)
            self.assertIn("single_replica_cpu_anomaly", types)
            self.assertIn("new_revision_cpu_higher", types)
            self.assertIn("request_rate_stable", types)
            self.assertIn("error_rate_increased", types)
            self.assertIn("latency_increased", types)
            self.assertNotIn("confirmed_root_cause", types)
            self.assertEqual(set(diagnosis.fact_refs_json), {item.id for item in findings})
            self.assertTrue(all(tool.model_visible_output_json["finding_ids"] for tool in tools))
            self.assertIn("CPU Spike", diagnosis.summary)
            self.assertIn("throttling", diagnosis.summary)
            self.assertEqual(run.budget_usage_json["tool_calls_used"], 7)
            model = await session.get(ModelSettings, 1)
            self.assertFalse(model.enabled)


if __name__ == "__main__":
    unittest.main()
