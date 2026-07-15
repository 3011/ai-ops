#!/usr/bin/env bash
set -euo pipefail
NS=${1:-aiops-dev}

secret_has() {
  local key=$1
  [[ -n "$(kubectl get secret aiops-secrets -n "$NS" -o "jsonpath={.data.${key}}" 2>/dev/null)" ]]
}

if ! secret_has AUTH_SESSION_SECRET; then
  value="$(openssl rand -hex 32)"
  patch="$(python3 - "$value" <<'PY'
import json, sys
print(json.dumps({"stringData": {"AUTH_SESSION_SECRET": sys.argv[1]}}))
PY
)"
  kubectl patch secret aiops-secrets -n "$NS" --type merge -p "$patch" >/dev/null
fi

if ! secret_has BOOTSTRAP_ADMIN_PASSWORD; then
  value="$(openssl rand -hex 12)"
  patch="$(python3 - "$value" <<'PY'
import json, sys
print(json.dumps({"stringData": {
    "BOOTSTRAP_ADMIN_USERNAME": "admin",
    "BOOTSTRAP_ADMIN_PASSWORD": sys.argv[1],
}}))
PY
)"
  kubectl patch secret aiops-secrets -n "$NS" --type merge -p "$patch" >/dev/null
fi

echo "Authentication bootstrap secrets are present."
