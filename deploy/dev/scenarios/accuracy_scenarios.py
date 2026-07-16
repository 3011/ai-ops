from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_API = "http://127.0.0.1:30801/api/v1"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def request_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def pod_identity(namespace: str, selector: str, container: str) -> dict[str, str]:
    raw = subprocess.check_output(
        ["kubectl", "get", "pod", "-n", namespace, "-l", selector, "-o", "json"],
        text=True,
    )
    items = json.loads(raw).get("items") or []
    ready = []
    for pod in items:
        statuses = (pod.get("status") or {}).get("containerStatuses") or []
        if any(item.get("name") == container and item.get("ready") for item in statuses):
            ready.append(pod)
    if len(ready) != 1:
        raise RuntimeError(f"expected one ready pod for {selector}/{container}, got {len(ready)}")
    metadata = ready[0]["metadata"]
    return {"pod": metadata["name"], "pod_uid": metadata["uid"], "container": container}


def make_alert(*, alertname: str, fingerprint: str, summary: str, labels: dict[str, str], expr: str) -> dict:
    start = now_iso()
    return {
        "version": "4",
        "receiver": "aiops-accuracy-runner",
        "status": "firing",
        "groupKey": f"accuracy:{fingerprint}",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": alertname,
                "severity": "warning",
                "namespace": "aiops-dev",
                "environment": "development",
                "aiops_enabled": "true",
                "aiops_test": "true",
                **labels,
            },
            "annotations": {
                "summary": summary,
                "description": "准确性负对照：告警名称声称异常，但资源事实应拒绝对应确定性 Finding。",
            },
            "startsAt": start,
            "endsAt": "0001-01-01T00:00:00Z",
            "fingerprint": fingerprint,
            "generatorURL": f"http://prometheus/graph?g0.expr={quote(expr)}&g0.tab=1",
        }],
    }


def fire(api: str, state_path: Path, run_id: str) -> None:
    oom = pod_identity("aiops-dev", "app=aiops-scenario-oom-negative", "steady-memory")
    cpu = pod_identity("aiops-dev", "app=aiops-scenario-cpu-negative", "idle")
    common = {"scenario_run_id": run_id}
    payloads = {
        "oom-negative-control": make_alert(
            alertname="AIOpsOOMNegativeControl",
            fingerprint=f"aiops-accuracy-oom-negative-{run_id}",
            summary="AIOps OOMKilled 负对照场景",
            labels={**common, "service": "aiops-scenario-oom-negative", **oom},
            expr="vector(1)",
        ),
        "cpu-negative-control": make_alert(
            alertname="AIOpsHighCPUNegativeControl",
            fingerprint=f"aiops-accuracy-cpu-negative-{run_id}",
            summary="AIOps High CPU 负对照场景",
            labels={**common, "service": "aiops-scenario-cpu-negative", **cpu},
            expr="vector(1)",
        ),
    }
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state.update({"run_id": run_id, "negative_payloads": payloads})
    results = {name: request_json(f"{api}/webhooks/alertmanager", payload) for name, payload in payloads.items()}
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


def resolve(api: str, state_path: Path) -> None:
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text())
    ended = now_iso()
    results = {}
    for name, payload in (state.get("negative_payloads") or {}).items():
        payload["status"] = "resolved"
        payload["alerts"][0]["status"] = "resolved"
        payload["alerts"][0]["endsAt"] = ended
        results[name] = request_json(f"{api}/webhooks/alertmanager", payload)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["fire", "resolve"])
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    if args.action == "fire":
        if not args.run_id:
            parser.error("--run-id is required for fire")
        fire(args.api.rstrip("/"), args.state, args.run_id)
    else:
        resolve(args.api.rstrip("/"), args.state)
