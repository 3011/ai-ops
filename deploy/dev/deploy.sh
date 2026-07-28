#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/aiops-console
NS=aiops-dev
[[ $(hostname) == k8s-cp01 ]] || { echo "must run on k8s-cp01" >&2; exit 1; }
kubectl apply -f "$ROOT/deploy/dev/00-base.yaml"
GIT_COMMIT="${GIT_COMMIT:-}"
if [[ -z "$GIT_COMMIT" && -f "$ROOT/.git/HEAD" ]]; then
  HEAD_VALUE="$(cat "$ROOT/.git/HEAD")"
  if [[ "$HEAD_VALUE" == ref:* ]]; then
    REF_PATH="${HEAD_VALUE#ref: }"
    [[ -f "$ROOT/.git/$REF_PATH" ]] && GIT_COMMIT="$(cat "$ROOT/.git/$REF_PATH")"
  else
    GIT_COMMIT="$HEAD_VALUE"
  fi
fi
if [[ -n "$GIT_COMMIT" ]]; then
  PATCH="$(python3 - "$GIT_COMMIT" <<'PY2'
import json, sys
print(json.dumps({"data": {"GIT_COMMIT": sys.argv[1]}}))
PY2
)"
  kubectl patch configmap aiops-config -n "$NS" --type merge -p "$PATCH" >/dev/null
fi
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
bash "$ROOT/deploy/dev/ensure-auth-secrets.sh" "$NS"
if [[ -z "$(kubectl get secret aiops-secrets -n "$NS" -o jsonpath='{.data.RELEASE_WEBHOOK_TOKEN}' 2>/dev/null)" ]]; then
  RELEASE_TOKEN="$(openssl rand -hex 24)"
  kubectl patch secret aiops-secrets -n "$NS" --type merge \
    -p "$(printf '{"stringData":{"RELEASE_WEBHOOK_TOKEN":"%s"}}' "$RELEASE_TOKEN")" >/dev/null
fi
kubectl apply -f "$ROOT/deploy/dev/10-postgresql.yaml"
kubectl apply -f "$ROOT/deploy/dev/40-security.yaml"
kubectl rollout status statefulset/postgresql -n "$NS" --timeout=180s

for migration in "$ROOT"/backend/migrations/*.sql; do
  [[ -f "$migration" ]] || continue
  echo "Applying migration: $(basename "$migration")"
  kubectl exec -i -n "$NS" postgresql-0 -- \
    psql -v ON_ERROR_STOP=1 -U aiops -d aiops < "$migration"
done

kubectl apply -f "$ROOT/deploy/dev/20-backend.yaml"
kubectl apply -f "$ROOT/deploy/dev/30-frontend.yaml"
kubectl apply -f "$ROOT/deploy/dev/50-alertmanager-config.yaml"
# hostPath source updates do not restart the long-running worker or reload environment variables.
kubectl rollout restart deployment/aiops-api deployment/aiops-worker -n "$NS"
kubectl rollout status deployment/aiops-api -n "$NS" --timeout=300s
kubectl rollout status deployment/aiops-worker -n "$NS" --timeout=300s
kubectl rollout status deployment/aiops-web -n "$NS" --timeout=300s
kubectl get pods,svc,pvc -n "$NS" -o wide
echo "Web: http://172.30.10.11:30300"
echo "API docs: http://172.30.10.11:30801/docs"
