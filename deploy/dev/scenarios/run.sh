#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
kubectl apply -f "$ROOT/deploy/dev/scenarios/crashloop.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/oom.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/cpu-spike.yaml"
bash "$ROOT/deploy/dev/scenarios/rollout_scenario.sh"
python3 "$ROOT/deploy/dev/scenarios/manual_scenarios.py" fire
cat <<'EOF'
Scenarios started. Prometheus, Alertmanager and AI analysis may need several minutes.
Validate with:
  python3 /root/aiops-console/deploy/dev/scenarios/validate.py
Cleanup with:
  bash /root/aiops-console/deploy/dev/scenarios/cleanup.sh
EOF
