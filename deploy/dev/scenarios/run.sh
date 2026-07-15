#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
NS=${NS:-aiops-dev}
BASE_SCENARIO_SETTLE_SECONDS=${BASE_SCENARIO_SETTLE_SECONDS:-150}
source "$ROOT/deploy/dev/scenarios/scenario_auth.sh"
ensure_scenario_session

# Start from a resource-clean state. Historical incidents and evidence remain in PostgreSQL.
bash "$ROOT/deploy/dev/scenarios/cleanup.sh" >/dev/null

kubectl apply -f "$ROOT/deploy/dev/scenarios/crashloop.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/oom.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/oom-sampled.yaml"
kubectl apply -f "$ROOT/deploy/dev/scenarios/cpu-spike.yaml"
bash "$ROOT/deploy/dev/scenarios/rollout_scenario.sh"
python3 "$ROOT/deploy/dev/scenarios/manual_scenarios.py" fire

echo "Base scenarios are active; waiting ${BASE_SCENARIO_SETTLE_SECONDS}s for Alertmanager and initial evidence collection..."
sleep "$BASE_SCENARIO_SETTLE_SECONDS"

# The namespace has a 4-Core limit quota. Release base workload CPU before the
# three-replica Gate 3 workload, while retaining alert rules and persisted evidence.
kubectl delete deployment -n "$NS" \
  aiops-scenario-crashloop aiops-scenario-oom aiops-scenario-oom-sampled \
  aiops-scenario-cpu aiops-scenario-rollout aiops-trace-mock --ignore-not-found
kubectl delete service -n "$NS" aiops-trace-mock --ignore-not-found
for _ in $(seq 1 90); do
  if [[ -z "$(kubectl get pods -n "$NS" -l aiops_test=true --no-headers 2>/dev/null)" ]]; then
    break
  fi
  sleep 2
done

bash "$ROOT/deploy/dev/scenarios/gate3_scenario.sh"
cat <<'MSG'
Scenarios started. Prometheus, Alertmanager and AI analysis may need several minutes.
Validate with:
  python3 /root/aiops-console/deploy/dev/scenarios/validate.py
Cleanup with:
  bash /root/aiops-console/deploy/dev/scenarios/cleanup.sh
MSG
