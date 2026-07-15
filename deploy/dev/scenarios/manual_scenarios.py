from __future__ import annotations
import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_API = "http://127.0.0.1:30801/api/v1"
STATE = Path("/tmp/aiops-manual-scenarios-state.json")


def request_json(url: str, payload: dict | None = None) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload else "GET",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def make_alert(
    name: str,
    fingerprint: str,
    labels: dict,
    summary: str,
    expr: str,
    starts_at: str,
) -> dict:
    return {
        "version": "4",
        "receiver": "aiops-scenario-runner",
        "status": "firing",
        "groupKey": f"scenario:{fingerprint}",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": name, "aiops_test": "true", **labels},
                "annotations": {
                    "summary": summary,
                    "description": "AIOps 自动分析边界场景测试。",
                },
                "startsAt": starts_at,
                "endsAt": "0001-01-01T00:00:00Z",
                "fingerprint": fingerprint,
                "generatorURL": f"http://prometheus/graph?g0.expr={quote(expr)}&g0.tab=1",
            }
        ],
    }


def fire(api: str) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    starts_at = now.isoformat().replace("+00:00", "Z")
    payloads = {
        "node": make_alert(
            "AIOpsNodeDiscoveryScenario",
            "aiops-node-discovery-suite",
            {
                "severity": "info",
                "namespace": "aiops-dev",
                "service": "k8s-worker01",
                "node": "k8s-worker01",
                "environment": "development",
            },
            "AIOps 节点自动发现测试",
            'kube_node_status_condition{node="k8s-worker01",condition="Ready",status="true"} == 1',
            starts_at,
        ),
        "sparse": make_alert(
            "AIOpsSparseLabelsScenario",
            "aiops-sparse-labels-suite",
            {"severity": "warning"},
            "AIOps 缺失标签降级测试",
            "vector(1)",
            starts_at,
        ),
        "http": make_alert(
            "HighHTTP5xxAndLatencyScenario",
            "aiops-http-planner-suite",
            {
                "severity": "warning",
                "namespace": "aiops-dev",
                "service": "aiops-api",
                "environment": "development",
            },
            "AIOps 应用层 HTTP 5xx 与延迟自动规划测试",
            'sum(rate(http_requests_total{namespace="aiops-dev",service="aiops-api",status=~"5.."}[5m])) > 1',
            starts_at,
        ),
    }
    results = {
        name: request_json(f"{api}/webhooks/alertmanager", payload)
        for name, payload in payloads.items()
    }

    lifecycle_a = make_alert(
        "AIOpsLifecycleRegression",
        "aiops-lifecycle-regression-suite",
        {
            "severity": "info",
            "namespace": "aiops-dev",
            "service": "lifecycle-regression",
        },
        "AIOps fingerprint 生命周期回归测试",
        "vector(1)",
        (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
    )
    lifecycle_b = json.loads(json.dumps(lifecycle_a))
    lifecycle_b["status"] = "resolved"
    lifecycle_b["alerts"][0]["status"] = "resolved"
    lifecycle_b["alerts"][0]["startsAt"] = (
        now - timedelta(minutes=1)
    ).isoformat().replace("+00:00", "Z")
    lifecycle_b["alerts"][0]["endsAt"] = starts_at
    results["lifecycle_firing"] = request_json(
        f"{api}/webhooks/alertmanager", lifecycle_a
    )
    results["lifecycle_resolved"] = request_json(
        f"{api}/webhooks/alertmanager", lifecycle_b
    )

    STATE.write_text(json.dumps(payloads, ensure_ascii=False))
    print(json.dumps(results, ensure_ascii=False, indent=2))


def resolve(api: str) -> None:
    if not STATE.exists():
        print("state file does not exist; nothing to resolve")
        return
    payloads = json.loads(STATE.read_text())
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    results = {}
    for name, payload in payloads.items():
        payload["status"] = "resolved"
        payload["alerts"][0]["status"] = "resolved"
        payload["alerts"][0]["endsAt"] = now
        results[name] = request_json(f"{api}/webhooks/alertmanager", payload)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["fire", "resolve"])
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args()
    (fire if args.action == "fire" else resolve)(args.api.rstrip("/"))
