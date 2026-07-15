#!/usr/bin/env bash
set -euo pipefail

SCENARIO_NS=${SCENARIO_NS:-aiops-dev}

ensure_scenario_session() {
  if [[ -n "${AIOPS_SESSION_TOKEN:-}" ]]; then
    return 0
  fi
  local pod
  pod=$(kubectl get pod -n "$SCENARIO_NS" -l app=aiops-worker \
    -o jsonpath='{.items[?(@.status.phase=="Running")].metadata.name}' | awk '{print $1}')
  if [[ -z "$pod" ]]; then
    echo "no running aiops-worker pod for scenario authentication" >&2
    return 1
  fi
  AIOPS_SESSION_TOKEN=$(kubectl exec -i -n "$SCENARIO_NS" "$pod" -c worker -- python - 2>/dev/null <<'PY'
import asyncio
from sqlalchemy import select
from app.auth import create_session_token
from app.db import SessionLocal
from app.models import Role, User, UserRole

async def main():
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                Role.name == "admin",
                User.is_active.is_(True),
                User.must_change_password.is_(False),
            )
            .order_by(User.id)
            .limit(1)
        )
        if user is None:
            raise RuntimeError("no active admin principal for scenario validation")
        print(create_session_token(user))

asyncio.run(main())
PY
  )
  if [[ -z "$AIOPS_SESSION_TOKEN" ]]; then
    echo "failed to create scenario session" >&2
    return 1
  fi
  export AIOPS_SESSION_TOKEN
}

scenario_curl() {
  ensure_scenario_session
  curl -b "aiops_session=$AIOPS_SESSION_TOKEN" "$@"
}
