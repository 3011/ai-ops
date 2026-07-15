#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/aiops-console
NS=aiops-dev
[[ $(hostname) == k8s-cp01 ]] || { echo "must run on k8s-cp01" >&2; exit 1; }
kubectl apply -f "$ROOT/deploy/dev/00-base.yaml"
if ! kubectl get secret aiops-secrets -n "$NS" >/dev/null 2>&1; then
  PASSWORD=$(openssl rand -hex 24)
  kubectl create secret generic aiops-secrets -n "$NS" \
    --from-literal=POSTGRES_PASSWORD="$PASSWORD" \
    --from-literal=DATABASE_URL="postgresql+asyncpg://aiops:${PASSWORD}@postgresql:5432/aiops" \
    --from-literal=LLM_API_KEY="" \
    --from-literal=SETTINGS_ENCRYPTION_KEY="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
fi
if [[ -z "$(kubectl get secret aiops-secrets -n "$NS" -o jsonpath='{.data.SETTINGS_ENCRYPTION_KEY}' 2>/dev/null)" ]]; then
  ENCRYPTION_KEY="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  kubectl patch secret aiops-secrets -n "$NS" --type merge \
    -p "$(printf '{"stringData":{"SETTINGS_ENCRYPTION_KEY":"%s"}}' "$ENCRYPTION_KEY")" >/dev/null
fi
kubectl apply -f "$ROOT/deploy/dev/10-postgresql.yaml"
kubectl apply -f "$ROOT/deploy/dev/20-backend.yaml"
kubectl apply -f "$ROOT/deploy/dev/30-frontend.yaml"
kubectl apply -f "$ROOT/deploy/dev/40-security.yaml"
kubectl apply -f "$ROOT/deploy/dev/50-alertmanager-config.yaml"
kubectl rollout status statefulset/postgresql -n "$NS" --timeout=180s
kubectl rollout status deployment/aiops-api -n "$NS" --timeout=300s
kubectl rollout status deployment/aiops-worker -n "$NS" --timeout=300s
kubectl rollout status deployment/aiops-web -n "$NS" --timeout=300s
kubectl get pods,svc,pvc -n "$NS" -o wide
echo "Web: http://172.30.10.11:30300"
echo "API docs: http://172.30.10.11:30801/docs"
