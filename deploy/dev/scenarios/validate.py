from __future__ import annotations
import base64
import http.cookiejar
import json
import os
import subprocess
import sys
from urllib.request import HTTPCookieProcessor, Request, build_opener

API = os.getenv("AIOPS_API", "http://127.0.0.1:30801/api/v1")
COOKIE_JAR = http.cookiejar.CookieJar()
OPENER = build_opener(HTTPCookieProcessor(COOKIE_JAR))


def bootstrap_login() -> None:
    username = os.getenv("AIOPS_TEST_USERNAME", "admin")
    password = os.getenv("AIOPS_TEST_PASSWORD")
    if not password:
        encoded = subprocess.check_output([
            "kubectl", "get", "secret", "aiops-secrets", "-n", "aiops-dev",
            "-o", "jsonpath={.data.BOOTSTRAP_ADMIN_PASSWORD}",
        ], text=True).strip()
        password = base64.b64decode(encoded).decode()
    request = Request(
        API + "/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with OPENER.open(request, timeout=20) as response:
        payload = json.load(response)
    if payload.get("user", {}).get("must_change_password"):
        print("SKIP scenario validation: bootstrap admin must change password through UI first")
        sys.exit(0)


def get(path: str) -> dict:
    with OPENER.open(API + path, timeout=20) as response:
        return json.load(response)


bootstrap_login()


incidents = get("/incidents?include_test=true&limit=500")["items"]
checks: list[tuple[bool, str]] = []


def find(fragment: str):
    return next((item for item in incidents if fragment in item["title"]), None)


for fragment, expected_signal, min_score in [
    ("CrashLoop", "BackOff", 75),
    ("OOMKilled", "OOMKilled", 75),
    ("节点自动发现", "node/", 70),
    ("缺失标签", None, 0),
]:
    row = find(fragment)
    if not row:
        checks.append((False, f"missing scenario: {fragment}"))
        continue
    detail = get(f"/incidents/{row['id']}")
    analysis = detail["analyses"][0]
    result = analysis.get("result") or {}
    coverage = result.get("analysis_coverage") or {}
    ok = (
        analysis.get("status") in ("succeeded", "evidence_ready")
        and coverage.get("score", 0) >= min_score
    )
    serialized = json.dumps(detail, ensure_ascii=False)
    if expected_signal:
        ok = ok and expected_signal in serialized
    if fragment == "缺失标签":
        ok = (
            ok
            and coverage.get("level") in ("low", "medium")
            and bool(coverage.get("missing"))
        )
    checks.append(
        (ok, f"{fragment}: analysis={analysis.get('status')} coverage={coverage.get('score')}")
    )

oom_row = find("OOMKilled")
if oom_row:
    detail = get(f"/incidents/{oom_row['id']}")
    trusted = (detail.get("trusted_investigations") or [None])[0]
    target = (trusted or {}).get("target_context") or {}
    findings = (trusted or {}).get("findings") or []
    tools = (trusted or {}).get("tool_executions") or []
    replay = (trusted or {}).get("replay_snapshot") or {}
    oom_finding = next((item for item in findings if item.get("finding_type") == "container_oom_killed"), None)
    tool_ids = {item.get("id") for item in tools}
    checks.append((
        bool(trusted)
        and trusted.get("status") in ("COMPLETED", "COMPLETED_PARTIAL")
        and target.get("resolution_quality") == "high"
        and bool(target.get("pod_uid"))
        and bool(target.get("container_name"))
        and any(item.get("status") == "FOUND" for item in tools)
        and bool(oom_finding)
        and oom_finding.get("tool_execution_id") in tool_ids
        and oom_finding.get("confirmation_rule") == "container_oom_killed_v1"
        and (trusted or {}).get("budget_usage", {}).get("tool_calls_used") == len(tools)
        and (trusted or {}).get("budget_usage", {}).get("total_cost_units_used") == sum(int(item.get("cost_units") or 0) for item in tools)
        and oom_finding.get("id") in ((next(item for item in tools if item.get("tool_name") == "get_container_termination_status").get("result_summary") or {}).get("finding_ids") or [])
        and {"get_container_termination_status", "get_memory_usage_vs_limit"}.issubset({item.get("tool_name") for item in tools})
        and any((item.get("structured_output") or {}).get("peak_limit_ratio") is not None for item in tools if item.get("tool_name") == "get_memory_usage_vs_limit")
        and all(bool(item.get("raw_artifact_hash")) for item in tools)
        and trusted.get("run_input_source_mode") == "native_frozen"
        and bool(trusted.get("run_input_source_hash"))
        and replay.get("validation_status") in {"VALID", "VALID_WITH_WARNINGS"},
        f"trusted OOM: run={(trusted or {}).get('status')} quality={target.get('resolution_quality')} uid={bool(target.get('pod_uid'))} tools={len(tools)} findings={len(findings)} cost={(trusted or {}).get('budget_usage', {}).get('total_cost_units_used')}",
    ))
else:
    checks.append((False, "missing scenario: trusted OOMKilled"))


sampled_oom_row = find("内存采样")
if sampled_oom_row:
    detail = get(f"/incidents/{sampled_oom_row['id']}")
    trusted = next((item for item in (detail.get("trusted_investigations") or []) if item.get("engine") == "deterministic_oom_v2"), None)
    findings = (trusted or {}).get("findings") or []
    tools = (trusted or {}).get("tool_executions") or []
    types = {item.get("finding_type") for item in findings}
    memory_tool = next((item for item in tools if item.get("tool_name") == "get_memory_usage_vs_limit"), None)
    memory_data = (memory_tool or {}).get("structured_output") or {}
    fact_refs = set(((trusted or {}).get("diagnosis") or {}).get("fact_refs") or [])
    checks.append((
        bool(trusted)
        and trusted.get("status") == "COMPLETED_PARTIAL"
        and "container_oom_killed" in types
        and bool(types & {"memory_usage_increased", "memory_near_limit", "memory_limit_reached"})
        and isinstance(memory_data.get("observed_peak_bytes"), (int, float))
        and isinstance(memory_data.get("memory_limit_bytes"), (int, float))
        and isinstance(memory_data.get("peak_limit_ratio"), (int, float))
        and {item.get("id") for item in findings}.issubset(fact_refs)
        and bool((memory_tool or {}).get("raw_artifact_hash")),
        f"sampled OOM memory: findings={sorted(types)} peak={memory_data.get('observed_peak_bytes')} limit={memory_data.get('memory_limit_bytes')} ratio={memory_data.get('peak_limit_ratio')}",
    ))
else:
    checks.append((False, "missing scenario: sampled OOM memory evidence"))


cpu_row = find("CPU Spike")
if cpu_row:
    detail = get(f"/incidents/{cpu_row['id']}")
    trusted = next((item for item in (detail.get("trusted_investigations") or []) if item.get("engine") == "deterministic_cpu_v1"), None)
    findings = (trusted or {}).get("findings") or []
    tools = (trusted or {}).get("tool_executions") or []
    types = {item.get("finding_type") for item in findings}
    tool_names = {item.get("tool_name") for item in tools}
    replay = (trusted or {}).get("replay_snapshot") or {}
    checks.append((
        bool(trusted)
        and trusted.get("status") == "COMPLETED_PARTIAL"
        and (trusted.get("target_context") or {}).get("resolution_quality") == "high"
        and "container_cpu_spike" in types
        and "cpu_request_saturated" in types
        and {
            "get_cpu_usage_vs_request_limit", "get_cpu_throttling",
            "get_container_restart_history", "get_recent_rollouts", "search_container_logs",
            "compare_cpu_across_replicas", "get_application_red_metrics",
        }.issubset(tool_names)
        and trusted.get("budget_usage", {}).get("tool_calls_used") == 7
        and trusted.get("budget_usage", {}).get("total_cost_units_used") == 23
        and all(bool(item.get("raw_artifact_hash")) for item in tools)
        and trusted.get("run_input_source_mode") == "native_frozen"
        and bool(trusted.get("run_input_source_hash"))
        and replay.get("validation_status") in {"VALID", "VALID_WITH_WARNINGS"},
        f"trusted CPU: run={(trusted or {}).get('status')} findings={sorted(types)} tools={sorted(tool_names)} cost={(trusted or {}).get('budget_usage', {}).get('total_cost_units_used')}",
    ))
else:
    checks.append((False, "missing scenario: trusted CPU Spike"))


gate3_row = find("Gate 3 多副本应用 CPU Spike")
if gate3_row:
    detail = get(f"/incidents/{gate3_row['id']}")
    trusted = next((item for item in (detail.get("trusted_investigations") or []) if item.get("engine") == "deterministic_cpu_v1"), None)
    findings = (trusted or {}).get("findings") or []
    tools = (trusted or {}).get("tool_executions") or []
    types = {item.get("finding_type") for item in findings}
    tool_names = {item.get("tool_name") for item in tools}
    by_name = {item.get("tool_name"): item for item in tools}
    replay = (trusted or {}).get("replay_snapshot") or {}
    fact_refs = set(((trusted or {}).get("diagnosis") or {}).get("fact_refs") or [])
    required_tools = {
        "get_cpu_usage_vs_request_limit", "get_cpu_throttling",
        "get_container_restart_history", "get_recent_rollouts", "search_container_logs",
        "compare_cpu_across_replicas", "get_application_red_metrics",
    }
    required_findings = {
        "container_cpu_spike", "cpu_throttling_sustained", "container_restart_increased",
        "rollout_preceded_incident", "revision_changed",
        "runtime_error_log_observed", "cpu_hot_loop_hint_log_observed", "request_timeout_log_observed",
        "single_replica_cpu_anomaly", "error_rate_increased", "latency_increased",
    }
    log_data = (by_name.get("search_container_logs") or {}).get("structured_output") or {}
    restart_data = (by_name.get("get_container_restart_history") or {}).get("structured_output") or {}
    rollout_data = (by_name.get("get_recent_rollouts") or {}).get("structured_output") or {}
    replica_data = (by_name.get("compare_cpu_across_replicas") or {}).get("structured_output") or {}
    red_data = (by_name.get("get_application_red_metrics") or {}).get("structured_output") or {}
    checks.append((
        bool(trusted)
        and trusted.get("status") == "COMPLETED_PARTIAL"
        and (trusted.get("target_context") or {}).get("resolution_quality") == "high"
        and required_tools == tool_names
        and required_findings.issubset(types)
        and trusted.get("budget_usage", {}).get("tool_calls_used") == 7
        and trusted.get("budget_usage", {}).get("total_cost_units_used") == 23
        and restart_data.get("window_restart_delta", 0) >= 1
        and rollout_data.get("revision_changed") is True
        and rollout_data.get("rollout_preceded_incident") is True
        and rollout_data.get("recent_change_count", 0) >= 1
        and log_data.get("prompt_injection_redacted_count", 0) >= 1
        and replica_data.get("replica_count", 0) >= 3
        and replica_data.get("single_replica_anomaly") is True
        and len(replica_data.get("anomalous_pods") or []) == 1
        and isinstance(((red_data.get("signals") or {}).get("request_rate") or {}).get("incident_mean"), (int, float))
        and red_data.get("error_rate_increased") is True
        and red_data.get("latency_increased") is True
        and {item.get("id") for item in findings}.issubset(fact_refs)
        and all(bool(item.get("raw_artifact_hash")) for item in tools)
        and replay.get("validation_status") == "VALID",
        f"trusted Gate 3: run={(trusted or {}).get('status')} tools={sorted(tool_names)} findings={sorted(types)} restart={restart_data.get('window_restart_delta')} rollout={rollout_data.get('revision_changed')} injection={log_data.get('prompt_injection_redacted_count')} replicas={replica_data.get('replica_count')}/{replica_data.get('anomalous_replica_count')} red={red_data.get('profile')}",
    ))
else:
    checks.append((False, "missing scenario: trusted Gate 3 application correlation"))


http_row = find("HTTP 5xx")
if http_row:
    detail = get(f"/incidents/{http_row['id']}")
    result = (detail["analyses"][0].get("result") or {})
    plan = result.get("dynamic_query_plan") or {}
    checks.append(
        (
            not plan.get("error") and bool(plan.get("accepted_prometheus")),
            f"HTTP dynamic planner: prom={len(plan.get('accepted_prometheus') or [])} loki={len(plan.get('accepted_loki') or [])} rejected={len(plan.get('rejected') or [])}",
        )
    )
else:
    checks.append((False, "missing scenario: HTTP dynamic planner"))

lifecycle = find("生命周期回归")
checks.append(
    (
        bool(lifecycle) and lifecycle.get("status") == "resolved",
        f"fingerprint lifecycle: status={lifecycle.get('status') if lifecycle else 'missing'}",
    )
)


rollout = find("rollout") or find("发布变更")
if rollout:
    detail = get(f"/incidents/{rollout['id']}")
    analysis = detail["analyses"][0]
    latest_ids = set((analysis.get("result") or {}).get("evidence_refs") or [])
    latest_evidence = [item for item in detail.get("evidence", []) if item.get("id") in latest_ids]
    k8s = next((item for item in latest_evidence if item.get("source_type") == "kubernetes"), None)
    changes = next((item for item in latest_evidence if item.get("source_type") == "changes"), None)
    traces = next((item for item in latest_evidence if item.get("source_type") == "traces"), None)
    revisions = {str(item.get("revision")) for item in ((k8s or {}).get("summary") or {}).get("rollout_history", [])}
    images = {image for item in ((k8s or {}).get("summary") or {}).get("rollout_history", []) for image in (item.get("images") or [])}
    configmaps = ((k8s or {}).get("summary") or {}).get("configmaps") or []
    change_count = ((changes or {}).get("summary") or {}).get("event_count", 0)
    trace_count = ((traces or {}).get("summary") or {}).get("trace_count", 0)
    checks.append((
        analysis.get("status") in ("succeeded", "evidence_ready")
        and {"1", "2"}.issubset(revisions)
        and {"busybox:1.36", "busybox:1.36.1"}.issubset(images)
        and bool(configmaps)
        and change_count >= 2
        and trace_count >= 1,
        f"rollout/change/trace: revisions={sorted(revisions)} images={sorted(images)} configmaps={len(configmaps)} changes={change_count} traces={trace_count}",
    ))
else:
    checks.append((False, "missing scenario: rollout/change/trace"))

changes_api = get("/change-events?include_test=true&namespace=aiops-dev&service=aiops-scenario-rollout")
checks.append((changes_api.get("total", 0) >= 2, f"change events API: total={changes_api.get('total', 0)}"))
trace_settings = get("/settings/traces")
checks.append((trace_settings.get("provider") in ("tempo", "jaeger"), f"trace settings API: provider={trace_settings.get('provider')} enabled={trace_settings.get('enabled')}"))

jobs = get("/analysis-jobs?include_test=true&limit=500")["items"]
checks.append(
    (not any(job["status"] == "dead" for job in jobs), "no dead analysis jobs")
)
default_summary = get("/dashboard/summary")
checks.append(
    (
        default_summary.get("open_incidents") == 0
        and default_summary.get("hidden_test_count", 0) > 0,
        "production dashboard excludes test events",
    )
)

for ok, message in checks:
    print(("PASS" if ok else "FAIL"), message)
if not all(ok for ok, _ in checks):
    sys.exit(1)
