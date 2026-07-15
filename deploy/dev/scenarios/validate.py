from __future__ import annotations
import json
import sys
from urllib.request import urlopen

API = "http://127.0.0.1:30801/api/v1"


def get(path: str) -> dict:
    with urlopen(API + path, timeout=20) as response:
        return json.load(response)


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
