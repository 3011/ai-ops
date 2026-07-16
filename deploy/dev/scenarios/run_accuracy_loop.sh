#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
NS=${NS:-aiops-dev}
API=${API:-http://127.0.0.1:30801/api/v1}
WAIT_SECONDS=${WAIT_SECONDS:-900}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
STATE=${STATE:-/tmp/aiops-accuracy-${RUN_ID}.json}
REPORT_DIR=${REPORT_DIR:-$ROOT/reports/accuracy}

cleanup() {
  python3 "$ROOT/deploy/dev/scenarios/accuracy_scenarios.py" resolve --api "$API" --state "$STATE" >/dev/null 2>&1 || true
  bash "$ROOT/deploy/dev/scenarios/cleanup.sh" >/dev/null 2>&1 || true
  kubectl delete -f "$ROOT/deploy/dev/scenarios/negative-controls.yaml" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

bash "$ROOT/deploy/dev/scenarios/cleanup.sh" >/dev/null
kubectl delete -f "$ROOT/deploy/dev/scenarios/negative-controls.yaml" --ignore-not-found >/dev/null 2>&1 || true
python3 - "$STATE" "$RUN_ID" <<'PY'
import json,sys
from datetime import UTC,datetime
from pathlib import Path
path=Path(sys.argv[1])
path.write_text(json.dumps({"run_id":sys.argv[2],"started_at":datetime.now(UTC).replace(microsecond=0).isoformat()},indent=2))
PY

kubectl apply -f "$ROOT/deploy/dev/scenarios/crashloop.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/oom.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/oom-sampled.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/cpu-spike.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/negative-controls.yaml"
kubectl rollout status deployment/aiops-scenario-oom-negative -n "$NS" --timeout=180s
kubectl rollout status deployment/aiops-scenario-cpu-negative -n "$NS" --timeout=180s
python3 "$ROOT/deploy/dev/scenarios/accuracy_scenarios.py" fire --api "$API" --state "$STATE" --run-id "$RUN_ID"

set +e
python3 "$ROOT/deploy/dev/scenarios/accuracy_evaluate.py" \
  --api "$API" \
  --ground-truth "$ROOT/deploy/dev/scenarios/accuracy-ground-truth.json" \
  --state "$STATE" \
  --report-dir "$REPORT_DIR" \
  --wait-seconds "$WAIT_SECONDS"
status=$?
set -e
exit "$status"
