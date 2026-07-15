#!/usr/bin/env bash
set -euo pipefail
ROOT=${ROOT:-/root/aiops-console}
NS=aiops-dev
API=${API:-http://127.0.0.1:30801/api/v1}
source "$ROOT/deploy/dev/scenarios/scenario_auth.sh"
ensure_scenario_session

kubectl apply -f "$ROOT/deploy/dev/scenarios/trace-mock.yaml"
kubectl rollout status deployment/aiops-trace-mock -n "$NS" --timeout=180s
scenario_curl -fsS -X PUT "$API/settings/traces" -H 'Content-Type: application/json' \
  -d '{"provider":"tempo","base_url":"http://aiops-trace-mock.aiops-dev.svc:3200","enabled":true,"service_tag":"service.name"}' >/dev/null
for _ in $(seq 1 20); do
  if scenario_curl -fsS -X POST "$API/settings/traces/test" -H 'Content-Type: application/json' \
      -d '{"provider":"tempo","base_url":"http://aiops-trace-mock.aiops-dev.svc:3200","enabled":true,"service_tag":"service.name"}' >/dev/null; then
    break
  fi
  sleep 2
done

kubectl apply -f "$ROOT/deploy/dev/scenarios/rollout-change.yaml"
kubectl rollout status deployment/aiops-scenario-rollout -n "$NS" --timeout=180s
TOKEN=$(kubectl get secret aiops-secrets -n "$NS" -o jsonpath='{.data.RELEASE_WEBHOOK_TOKEN}' | base64 -d)
post_release() {
  local version=$1 commit=$2 image=$3 occurred=$4
  curl -fsS -X POST "$API/webhooks/deployment-events" \
    -H 'Content-Type: application/json' -H "X-AIOps-Token: $TOKEN" \
    -d "{\"source\":\"gitlab\",\"event_type\":\"deployment\",\"cluster\":\"kubernetes\",\"namespace\":\"aiops-dev\",\"service\":\"aiops-scenario-rollout\",\"environment\":\"development\",\"workload_kind\":\"Deployment\",\"workload_name\":\"aiops-scenario-rollout\",\"version\":\"$version\",\"commit_sha\":\"$commit\",\"image\":\"$image\",\"actor\":\"ci-bot\",\"title\":\"发布 aiops-scenario-rollout $version\",\"occurred_at\":\"$occurred\"}" >/dev/null
}
post_release v1 1111111 busybox:1.36 "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
kubectl patch configmap aiops-scenario-rollout-config -n "$NS" --type merge -p '{"data":{"APP_MESSAGE":"release-v2"}}'
kubectl patch deployment aiops-scenario-rollout -n "$NS" --type merge -p '{"spec":{"template":{"metadata":{"annotations":{"release.aiops/version":"v2"}},"spec":{"containers":[{"name":"app","image":"busybox:1.36.1","command":["sh","-c","echo release=$APP_MESSAGE; sleep 3600"],"envFrom":[{"configMapRef":{"name":"aiops-scenario-rollout-config"}}],"resources":{"requests":{"cpu":"5m","memory":"8Mi"},"limits":{"cpu":"50m","memory":"32Mi"}}}]}}}}'
kubectl rollout status deployment/aiops-scenario-rollout -n "$NS" --timeout=180s
post_release v2 2222222 busybox:1.36.1 "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
