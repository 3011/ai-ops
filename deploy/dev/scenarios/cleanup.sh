#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
kubectl delete -f "$ROOT/deploy/dev/scenarios/crashloop.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/oom.yaml" --ignore-not-found
python3 "$ROOT/deploy/dev/scenarios/manual_scenarios.py" resolve || true
echo "Scenario resources removed. Alertmanager resolved notifications can take several minutes."
