from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import unittest
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal, engine
from app.investigation.catalog import TOOL_CATALOG_VERSION
from app.investigation.replay import backfill_replay_snapshots, create_replay_snapshot
from app.investigation.run_input import RUN_INPUT_SCHEMA_VERSION, build_native_run_input, legacy_v08_input, legacy_v09_input
from app.investigation.tool_runtime import stable_hash
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
)


@unittest.skipUnless(os.getenv("AIOPS_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class ReplayPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def _seed_run(
        self,
        *,
        native_frozen: bool = True,
        engine_version: str = "0.9.0-dev.4",
        legacy_schema: str | None = None,
        target_resolved: bool = True,
        seed_key: str = "default",
        time_offset_minutes: int = 0,
    ) -> int:
        incident_time = datetime(2026, 7, 15, 10, 0, tzinfo=UTC) + timedelta(minutes=time_offset_minutes)
        async with SessionLocal() as session:
            incident = Incident(
                grouping_key=f"replay-test-{seed_key}", title="CPU Spike replay test",
                status="resolved", severity="warning",
                labels={"cluster": "prod-a", "namespace": "production", "service": "payment-api"},
                alert_count=1, first_seen_at=incident_time, last_seen_at=incident_time,
                resolved_at=incident_time + timedelta(minutes=1),
            )
            session.add(incident)
            await session.flush()
            alert = AlertInstance(
                fingerprint=f"replay-alert-{seed_key}", alertname="ContainerCPUSpike", status="resolved", severity="warning",
                labels={"cluster": "prod-a", "namespace": "production", "service": "payment-api",
                        "pod": "payment-api-abc", "uid": "pod-uid-1", "container": "main"},
                annotations={}, starts_at=incident_time, ends_at=incident_time + timedelta(minutes=1),
            )
            session.add(alert)
            await session.flush()
            session.add(IncidentAlert(incident_id=incident.id, alert_instance_id=alert.id))
            native_input = build_native_run_input(
                incident, [alert], candidate_mode="cpu", tool_catalog_version=engine_version
            )
            if legacy_schema == "v08":
                run_input = legacy_v08_input(incident, [alert])
            elif legacy_schema == "v09":
                legacy_run = SimpleNamespace(engine="deterministic_cpu_v1", engine_version=engine_version)
                run_input = legacy_v09_input(incident, [alert], legacy_run)
            else:
                run_input = native_input
            target = {
                "cluster_id": "prod-a", "namespace": "production", "pod_name": "payment-api-abc",
                "pod_uid": "pod-uid-1", "container_name": "main", "service_name": "payment-api",
                "workload_kind": "Deployment", "workload_name": "payment-api", "workload_uid": "deploy-uid-1",
                "incident_time": incident_time.isoformat(),
                "window_start": (incident_time - timedelta(minutes=15)).isoformat(),
                "window_end": (incident_time + timedelta(minutes=30)).isoformat(),
                "resolution_method": "alert_pod_uid", "resolution_path": ["Alert", "Pod UID"],
                "resolution_quality": "high", "allowed_namespaces": ["production"],
            }
            target_ref = {key: target[key] for key in (
                "cluster_id", "namespace", "pod_name", "pod_uid", "container_name", "service_name",
                "workload_kind", "workload_name", "workload_uid",
            )}
            run = InvestigationAnalysisRun(
                incident_id=incident.id, status="COMPLETED_PARTIAL", stop_reason="AGENT_NOT_ENABLED",
                degradation_reasons=["AGENT_NOT_ENABLED"], target_context_json=target if target_resolved else {
                    "resolution_status": "TARGET_UNCERTAIN", "resolution_message": "legacy target unavailable"
                },
                engine="deterministic_cpu_v1", engine_version=engine_version,
                input_snapshot_hash=stable_hash(run_input),
                run_input_json=native_input if native_frozen else None,
                run_input_schema_version=RUN_INPUT_SCHEMA_VERSION if native_frozen else None,
                run_input_source_hash=stable_hash(native_input) if native_frozen else None,
                run_input_source_mode="native_frozen" if native_frozen else None,
                budget_json={}, budget_usage_json={},
                started_at=incident_time, completed_at=incident_time + timedelta(seconds=2),
            )
            session.add(run)
            await session.flush()
            finding_id = f"F-replay-valid-{seed_key}"
            visible = {"summary": "CPU spike", "status": "FOUND", "completeness": "complete",
                       "finding_ids": [finding_id], "error_code": None, "is_truncated": False,
                       "raw_artifact_uri": None}
            tool = InvestigationToolExecution(
                analysis_run_id=run.id, sequence_number=1,
                tool_name="get_cpu_usage_vs_request_limit", tool_version="1.0.0",
                normalized_input_hash="input-hash",
                input_json={"target": target_ref, "target_identity": target, "arguments": {},
                            "scope": {"allowed_namespaces": ["production"]}},
                status="FOUND", structured_output_json={"peak_cores": 1.0},
                model_visible_output_json=visible, raw_output_json={"must_not": "appear"},
                raw_artifact_hash="artifact-hash", cost_units=4, is_truncated=False,
                retryable=False, started_at=incident_time, completed_at=incident_time + timedelta(seconds=1),
            )
            session.add(tool)
            await session.flush()
            session.add(InvestigationFinding(
                id=finding_id, analysis_run_id=run.id, tool_execution_id=tool.id,
                finding_type="container_cpu_spike", subject_ref_json=target_ref,
                value_json={"peak_cores": 1.0}, polarity="positive", quality="high",
                event_time=incident_time, parser_version="1.0.0",
                confirmation_rule="container_cpu_spike_v1",
            ))
            diagnosis = {
                "summary": "confirmed", "fact_refs": [finding_id], "hypotheses": [],
                "missing_evidence": [], "recommended_checks": [], "risk_notes": [],
                "degradation_reasons": ["AGENT_NOT_ENABLED"], "analysis_mode": "deterministic_cpu_v1",
            }
            session.add(InvestigationDiagnosisResult(
                analysis_run_id=run.id, summary=diagnosis["summary"], fact_refs_json=diagnosis["fact_refs"],
                hypotheses_json=[], missing_evidence_json=[], recommended_checks_json=[], risk_notes_json=[],
                degradation_reasons_json=diagnosis["degradation_reasons"], validated_output_json=diagnosis,
            ))
            await session.commit()
            return run.id

    async def test_backfill_only_missing_is_idempotent(self) -> None:
        run_id = await self._seed_run()
        async with SessionLocal() as session:
            first = await backfill_replay_snapshots(session, only_missing=True)
            await session.commit()
            self.assertEqual(first["selected"], 1)
            self.assertEqual(first["created"], 1)
            self.assertEqual(first["failed"], 0)
            self.assertEqual(first["results"][0]["analysis_run_id"], run_id)

        async with SessionLocal() as session:
            second = await backfill_replay_snapshots(session, only_missing=True)
            await session.commit()
            self.assertEqual(second["selected"], 0)
            self.assertEqual(second["created"], 0)
            count = await session.scalar(select(func.count()).select_from(InvestigationReplaySnapshot))
            self.assertEqual(count, 1)

    async def test_snapshot_is_idempotent_and_detects_source_mutation(self) -> None:
        run_id = await self._seed_run()
        async with SessionLocal() as session:
            first = await create_replay_snapshot(session, run_id)
            await session.commit()
            self.assertEqual(first.validation_status, "VALID")
            self.assertNotIn("raw_output_json", str(first.snapshot_json))
            self.assertNotIn("must_not", str(first.snapshot_json))
            first_id = first.id
            first_hash = first.snapshot_hash

        async with SessionLocal() as session:
            second = await create_replay_snapshot(session, run_id)
            await session.commit()
            self.assertEqual(second.id, first_id)
            self.assertEqual(second.snapshot_hash, first_hash)
            count = await session.scalar(select(func.count()).select_from(InvestigationReplaySnapshot))
            self.assertEqual(count, 1)

        async with SessionLocal() as session:
            finding = await session.get(InvestigationFinding, "F-replay-valid-default")
            finding.finding_type = "deployment_caused_incident"
            await session.flush()
            third = await create_replay_snapshot(session, run_id)
            await session.commit()
            self.assertNotEqual(third.id, first_id)
            self.assertEqual(third.validation_status, "INVALID")
            codes = {item["code"] for item in third.validation_report_json["errors"]}
            self.assertIn("FINDING_TYPE_NOT_ALLOWED", codes)
            count = await session.scalar(select(func.count()).select_from(InvestigationReplaySnapshot))
            self.assertEqual(count, 2)


    async def test_native_frozen_snapshot_ignores_later_incident_and_alert_changes(self) -> None:
        run_id = await self._seed_run(native_frozen=True)
        async with SessionLocal() as session:
            first = await create_replay_snapshot(session, run_id)
            run = await session.get(InvestigationAnalysisRun, run_id)
            incident = await session.get(Incident, run.incident_id)
            original_input_hash = run.run_input_source_hash
            first_id = first.id
            first_hash = first.snapshot_hash
            incident.status = "open"
            incident.labels = {**incident.labels, "later_label": "must-not-enter-frozen-input"}
            incident.last_seen_at = incident.last_seen_at + timedelta(hours=2)
            later = AlertInstance(
                fingerprint="later-alert", alertname="ContainerCPUSpike", status="firing", severity="critical",
                labels={"cluster": "prod-a", "namespace": "production", "service": "payment-api",
                        "pod": "replacement-pod", "uid": "replacement-uid", "container": "main"},
                annotations={"summary": "later alert"},
                starts_at=run.started_at + timedelta(minutes=10),
            )
            session.add(later)
            await session.flush()
            session.add(IncidentAlert(incident_id=incident.id, alert_instance_id=later.id))
            await session.commit()

        async with SessionLocal() as session:
            second = await create_replay_snapshot(session, run_id)
            await session.commit()
            run = await session.get(InvestigationAnalysisRun, run_id)
            self.assertEqual(second.id, first_id)
            self.assertEqual(second.snapshot_hash, first_hash)
            self.assertEqual(run.run_input_source_hash, original_input_hash)
            self.assertEqual(second.snapshot_json["run_input_source_mode"], "native_frozen")
            self.assertNotIn("later_label", str(second.snapshot_json))
            self.assertNotIn("replacement-uid", str(second.snapshot_json))

    async def test_legacy_v08_and_v09_inputs_are_reconstructed_at_run_cutoff(self) -> None:
        cases = (("1.1.0", "v08"), ("0.9.0-dev.1", "v09"))
        for index, (engine_version, schema) in enumerate(cases):
            with self.subTest(engine_version=engine_version):
                run_id = await self._seed_run(
                    native_frozen=False, engine_version=engine_version, legacy_schema=schema,
                    seed_key=f"legacy-{index}", time_offset_minutes=index * 20,
                )
                async with SessionLocal() as session:
                    run = await session.get(InvestigationAnalysisRun, run_id)
                    incident = await session.get(Incident, run.incident_id)
                    later = AlertInstance(
                        fingerprint=f"later-{engine_version}", alertname="ContainerCPUSpike",
                        status="firing", severity="warning",
                        labels={"namespace": "production", "pod": "later", "uid": "later-uid", "container": "main"},
                        annotations={}, starts_at=run.started_at + timedelta(minutes=5),
                    )
                    session.add(later)
                    await session.flush()
                    session.add(IncidentAlert(incident_id=incident.id, alert_instance_id=later.id))
                    await session.commit()
                async with SessionLocal() as session:
                    snapshot = await create_replay_snapshot(session, run_id)
                    await session.commit()
                    self.assertEqual(snapshot.validation_status, "VALID")
                    self.assertEqual(snapshot.snapshot_json["run_input_source_mode"], "historical_reconstructed")
                    self.assertNotIn("later-uid", str(snapshot.snapshot_json))

    async def test_legacy_tool_finding_mismatch_remains_invalid(self) -> None:
        run_id = await self._seed_run(native_frozen=False, engine_version="1.0.0", legacy_schema="v08")
        async with SessionLocal() as session:
            tool = await session.scalar(select(InvestigationToolExecution).where(
                InvestigationToolExecution.analysis_run_id == run_id
            ))
            tool.model_visible_output_json = {**tool.model_visible_output_json, "finding_ids": []}
            flag_modified(tool, "model_visible_output_json")
            snapshot = await create_replay_snapshot(session, run_id)
            await session.commit()
            self.assertEqual(snapshot.validation_status, "INVALID")
            self.assertTrue(snapshot.validation_report_json["legacy_contract_violation"])
            codes = {item["code"] for item in snapshot.validation_report_json["errors"]}
            self.assertIn("TOOL_FINDING_REF_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
