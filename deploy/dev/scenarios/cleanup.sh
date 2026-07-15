#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
API=${API:-http://127.0.0.1:30801/api/v1}
source "$ROOT/deploy/dev/scenarios/scenario_auth.sh"
ensure_scenario_session
kubectl delete -f "$ROOT/deploy/dev/scenarios/crashloop.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/oom.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/oom-sampled.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/gate3-app.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/cpu-spike.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/rollout-change.yaml" --ignore-not-found
kubectl delete -f "$ROOT/deploy/dev/scenarios/trace-mock.yaml" --ignore-not-found
scenario_curl -fsS -X PUT "$API/settings/traces" -H 'Content-Type: application/json' \
  -d '{"provider":"tempo","base_url":"","enabled":false,"service_tag":"service.name"}' >/dev/null || true
python3 "$ROOT/deploy/dev/scenarios/manual_scenarios.py" resolve || true
# Cancel only future test-incident follow-up jobs. Historical jobs and production jobs are untouched.
cancel_scheduled_test_jobs() {
  kubectl exec -i -n aiops-dev postgresql-0 -- psql -v ON_ERROR_STOP=1 -U aiops -d aiops >/dev/null <<'SQL'
UPDATE outbox_jobs AS job
SET status = 'skipped',
    finished_at = now(),
    locked_at = NULL,
    locked_by = NULL,
    last_error = 'scenario cleanup cancelled scheduled test follow-up'
WHERE job.status IN ('pending', 'retry')
  AND job.job_type = 'analyze_incident'
  AND EXISTS (
      SELECT 1
      FROM incidents AS incident
      WHERE incident.id = NULLIF(job.payload->>'incident_id', '')::bigint
        AND lower(coalesce(incident.labels->>'aiops_test', 'false')) = 'true'
  );
SQL
}

cancel_scheduled_test_jobs
for _ in $(seq 1 90); do
  processing=$(kubectl exec -n aiops-dev postgresql-0 -- psql -U aiops -d aiops -Atq -c "
    SELECT count(*)
    FROM outbox_jobs AS job
    WHERE job.status = 'processing'
      AND job.job_type = 'analyze_incident'
      AND EXISTS (
          SELECT 1 FROM incidents AS incident
          WHERE incident.id = NULLIF(job.payload->>'incident_id', '')::bigint
            AND lower(coalesce(incident.labels->>'aiops_test', 'false')) = 'true'
      );
  ")
  [[ "$processing" == "0" ]] && break
  sleep 2
done
# A processing analysis can create its 30-minute follow-up during the wait.
cancel_scheduled_test_jobs
echo "Scenario resources removed. Alertmanager resolved notifications can take several minutes."
