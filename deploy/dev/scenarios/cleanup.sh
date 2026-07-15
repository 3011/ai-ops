#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
API=${API:-http://127.0.0.1:30801/api/v1}
kubectl delete -f "$ROOT/deploy/dev/scenarios/crashloop.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/oom.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/oom-sampled.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/gate3-app.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/cpu-spike.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/rollout-change.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/trace-mock.yaml" --ignore-not-found
curl -fsS -X PUT "$API/settings/traces" -H 'Content-Type: application/json' \
  -d '{"provider":"tempo","base_url":"","enabled":false,"service_tag":"service.name"}' >/dev/null || true
python3 "$ROOT/deploy/dev/scenarios/manual_scenarios.py" resolve || true
echo "Scenario resources removed. Alertmanager resolved notifications can take several minutes."
