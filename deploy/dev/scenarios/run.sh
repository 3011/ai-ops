#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
kubectl apply -f "$ROOT/deploy/dev/scenarios/crashloop.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/oom.yaml"
python3 "$ROOT/deploy/dev/scenarios/manual_scenarios.py" fire
cat <<'EOF'
Scenarios started. Prometheus and Alertmanager may need several minutes.
Validate with:
  python3 /root/aiops-console/deploy/dev/scenarios/validate.py
Cleanup with:
  bash /root/aiops-console/deploy/dev/scenarios/cleanup.sh
EOF
