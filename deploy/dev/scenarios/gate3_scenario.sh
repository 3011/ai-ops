#!/usr/bin/env sh
set -eu
ROOT=${ROOT:-/root/aiops-console}
NS=${NS:-aiops-dev}
APP=aiops-gate3-app
API=${API:-http://127.0.0.1:30801/api/v1}

kubectl apply -f "$ROOT/deploy/dev/scenarios/gate3-app.yaml"
kubectl -n "$NS" rollout status deployment/$APP --timeout=240s

echo "Gate 3 revision 1 is ready; collecting initial service metrics..."
sleep 75

kubectl -n "$NS" set env deployment/$APP SCENARIO_REVISION=v2 RESTART_ONCE=true >/dev/null
kubectl -n "$NS" rollout status deployment/$APP --timeout=240s

TOKEN=$(kubectl -n "$NS" get secret aiops-secrets -o jsonpath='{.data.RELEASE_WEBHOOK_TOKEN}' | base64 -d)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -fsS -X POST "$API/webhooks/deployment-events" \
  -H 'Content-Type: application/json' \
  -H "X-AIOps-Token: $TOKEN" \
  -d "{\"source\":\"gate3-scenario\",\"event_type\":\"deployment\",\"cluster\":\"independent-k8s\",\"namespace\":\"$NS\",\"service\":\"$APP\",\"environment\":\"development\",\"workload_kind\":\"Deployment\",\"workload_name\":\"$APP\",\"version\":\"v2\",\"commit_sha\":\"gate3-v2\",\"image\":\"python:3.12-alpine\",\"actor\":\"scenario-runner\",\"title\":\"Gate 3 revision 2 rollout\",\"description\":\"Trusted tool Gate 3 test rollout\",\"occurred_at\":\"$NOW\",\"is_test\":true}" >/dev/null
unset TOKEN

echo "Revision 2 is ready; waiting for controlled same-UID container restarts..."
sleep 105

TARGET=$(kubectl -n "$NS" get pod -l app.kubernetes.io/name=$APP -o json | python3 -c '
import json,sys
items=[]
for pod in json.load(sys.stdin).get("items", []):
    statuses=(pod.get("status") or {}).get("containerStatuses") or []
    if not statuses:
        continue
    status=statuses[0]
    if status.get("ready") and int(status.get("restartCount") or 0) >= 1:
        items.append(pod["metadata"]["name"])
if not items:
    raise SystemExit("no ready restarted Gate 3 pod")
print(sorted(items)[0])
')
TARGET_UID=$(kubectl -n "$NS" get pod "$TARGET" -o jsonpath='{.metadata.uid}')
RESTARTS=$(kubectl -n "$NS" get pod "$TARGET" -o jsonpath='{.status.containerStatuses[0].restartCount}')
kubectl -n "$NS" exec "$TARGET" -c app -- sh -c 'test ! -f /state/hot && touch /state/hot'
echo "TARGET_POD=$TARGET"
echo "TARGET_UID=$TARGET_UID"
echo "TARGET_RESTARTS=$RESTARTS"
echo "Hot loop enabled. Prometheus and Alertmanager normally need 1-3 minutes to create the trusted run."
