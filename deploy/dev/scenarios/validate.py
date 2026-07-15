from __future__ import annotations
import base64
import http.cookiejar
import json
import os
import subprocess
import sys
from urllib.request import HTTPCookieProcessor, Request, build_opener

API = "http://127.0.0.1:30801/api/v1"
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
