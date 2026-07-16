from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import os
import secrets
from typing import Any

from fastapi import HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AuditLog,
    Permission,
    ReleaseNote,
    Role,
    RolePermission,
    User,
    UserRole,
)

settings = get_settings()
SESSION_COOKIE = "aiops_session"
PBKDF2_ITERATIONS = 310_000

PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("dashboard.view", "查看运维总览", "基础访问"),
    ("incidents.view", "查看事件、告警和任务", "事件管理"),
    ("incidents.analyze", "提交事件重新分析", "事件管理"),
    ("changes.view", "查看变更记录", "变更管理"),
    ("settings.view", "查看平台集成配置", "平台设置"),
    ("settings.manage", "修改模型和集成配置", "平台设置"),
    ("users.view", "查看用户和角色", "访问控制"),
    ("users.manage", "管理用户、角色和权限", "访问控制"),
    ("audit.view", "查看审计日志", "访问控制"),
    ("versions.view", "查看版本说明", "版本管理"),
    ("versions.manage", "维护版本说明", "版本管理"),
)

RELEASE_HISTORY: tuple[dict[str, Any], ...] = (
    {"version": "0.1.0", "title": "可靠告警接入 MVP", "summary": "完成 Alertmanager、事件生命周期和 Outbox 基线。", "changes": ["Alertmanager webhook 幂等接入", "告警实例与事件聚合", "PostgreSQL Outbox Worker"], "commit_sha": "27e568d", "released_at": "2026-07-14T18:00:00+00:00"},
    {"version": "0.2.0", "title": "证据采集与结构化 AI", "summary": "接入 Prometheus、Loki 和 DeepSeek。", "changes": ["证据快照", "结构化根因假设", "模型失败安全降级"], "commit_sha": "268fb3f", "released_at": "2026-07-14T21:00:00+00:00"},
    {"version": "0.3.0", "title": "模型配置中心", "summary": "支持前台维护 OpenAI-compatible 模型配置。", "changes": ["API Key 加密保存", "模型连接测试", "配置无需重启立即生效"], "commit_sha": None, "released_at": "2026-07-14T22:00:00+00:00"},
    {"version": "0.4.0", "title": "AIOps 运维工作台", "summary": "增加总览、事件、原始告警、投递和任务页面。", "changes": ["运维总览", "事件详情图表化", "测试数据来源标识"], "commit_sha": "dd26ca1", "released_at": "2026-07-14T23:00:00+00:00"},
    {"version": "0.5.1", "title": "免模板动态证据分析", "summary": "自动发现 Kubernetes 目标并由模型规划受限查询。", "changes": ["CrashLoop/OOM/Node 自动发现", "Kubernetes current/previous logs", "动态 PromQL/LogQL 安全规划"], "commit_sha": "d151ba3", "released_at": "2026-07-15T04:30:00+00:00"},
    {"version": "0.6.0", "title": "发布变更与 Trace 关联", "summary": "接入 rollout、镜像、ConfigMap、CI/CD 和可选 Trace。", "changes": ["Deployment rollout 历史", "发布事件 webhook", "Tempo/Jaeger 查询", "事件详情四段式体验"], "commit_sha": "bd2b049", "released_at": "2026-07-15T05:23:00+00:00"},
)


ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "admin": {
        "display_name": "平台管理员",
        "description": "拥有平台全部权限。",
        "permissions": [item[0] for item in PERMISSIONS],
    },
    "operator": {
        "display_name": "运维人员",
        "description": "可查看并分析事件、查看变更和版本。",
        "permissions": [
            "dashboard.view", "incidents.view", "incidents.analyze",
            "changes.view", "settings.view", "versions.view",
        ],
    },
    "viewer": {
        "display_name": "只读观察者",
        "description": "仅查看总览、事件、变更和版本。",
        "permissions": [
            "dashboard.view", "incidents.view", "changes.view", "versions.view",
        ],
    },
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("密码至少需要 10 个字符")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_value, digest_value = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _b64decode(salt_value), int(rounds)
        )
        return hmac.compare_digest(digest, _b64decode(digest_value))
    except Exception:
        return False


# Unknown users still execute the same password verifier path. This avoids a
# fast-fail timing signal that could otherwise reveal whether an account exists.
DUMMY_PASSWORD_HASH = hash_password("AIOps-dummy-password-check-only")


def _session_secret() -> bytes:
    value = settings.auth_session_secret or ""
    if len(value) < 32:
        raise RuntimeError("AUTH_SESSION_SECRET 未配置或长度不足")
    return value.encode()


def create_session_token(user: User) -> str:
    payload = {
        "uid": user.id,
        "ver": user.token_version,
        "exp": int((utcnow() + timedelta(hours=settings.session_hours)).timestamp()),
        "nonce": secrets.token_hex(8),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64encode(hmac.new(_session_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def decode_session_token(token: str) -> dict[str, Any] | None:
    try:
        body, signature = token.split(".", 1)
        expected = _b64encode(hmac.new(_session_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64decode(body))
        if int(payload.get("exp") or 0) <= int(utcnow().timestamp()):
            return None
        return payload
    except Exception:
        return None


def set_session_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user),
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@dataclass(slots=True)
class Principal:
    user_id: int
    username: str
    display_name: str
    email: str | None
    must_change_password: bool
    roles: list[dict[str, Any]]
    permissions: set[str]

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "must_change_password": self.must_change_password,
            "roles": self.roles,
            "permissions": sorted(self.permissions),
        }


async def principal_for_user(user: User, session: AsyncSession) -> Principal:
    rows = (
        await session.execute(
            select(Role, Permission.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .outerjoin(Permission, Permission.id == RolePermission.permission_id)
            .where(UserRole.user_id == user.id)
            .order_by(Role.name, Permission.code)
        )
    ).all()
    role_map: dict[int, dict[str, Any]] = {}
    permissions: set[str] = set()
    for role, permission_code in rows:
        role_map.setdefault(role.id, {"id": role.id, "name": role.name, "display_name": role.display_name})
        if permission_code:
            permissions.add(permission_code)
    return Principal(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        must_change_password=user.must_change_password,
        roles=list(role_map.values()),
        permissions=permissions,
    )


async def principal_from_request(request: Request, session: AsyncSession) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
    if not token:
        return None
    payload = decode_session_token(token)
    if not payload:
        return None
    user = await session.get(User, int(payload.get("uid") or 0))
    if not user or not user.is_active or user.token_version != int(payload.get("ver") or -1):
        return None
    return await principal_for_user(user, session)


def require_permission(principal: Principal, permission: str) -> None:
    if permission not in principal.permissions:
        raise HTTPException(403, f"缺少权限：{permission}")


async def record_audit(
    session: AsyncSession,
    *,
    principal: Principal | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
    username: str | None = None,
) -> None:
    ip_address = request.client.host if request and request.client else None
    session.add(
        AuditLog(
            user_id=principal.user_id if principal else None,
            username=principal.username if principal else (username or "anonymous"),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
        )
    )


async def seed_auth_and_release(session: AsyncSession) -> None:
    permission_by_code: dict[str, Permission] = {}
    for code, name, category in PERMISSIONS:
        row = await session.scalar(select(Permission).where(Permission.code == code))
        if row is None:
            row = Permission(code=code, name=name, category=category, description=name)
            session.add(row)
            await session.flush()
        permission_by_code[code] = row

    role_by_name: dict[str, Role] = {}
    for role_name, definition in ROLE_DEFAULTS.items():
        role = await session.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            role = Role(
                name=role_name,
                display_name=definition["display_name"],
                description=definition["description"],
                is_system=True,
            )
            session.add(role)
            await session.flush()
        role_by_name[role_name] = role
        existing_permission_ids = set(
            (await session.scalars(select(RolePermission.permission_id).where(RolePermission.role_id == role.id))).all()
        )
        for code in definition["permissions"]:
            permission = permission_by_code[code]
            if permission.id not in existing_permission_ids:
                session.add(RolePermission(role_id=role.id, permission_id=permission.id))

    admin = await session.scalar(select(User).where(User.username == settings.bootstrap_admin_username))
    if admin is None:
        if not settings.bootstrap_admin_password:
            raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD 未配置")
        admin = User(
            username=settings.bootstrap_admin_username,
            display_name="平台管理员",
            email=None,
            password_hash=hash_password(settings.bootstrap_admin_password),
            is_active=True,
            must_change_password=True,
        )
        session.add(admin)
        await session.flush()
        session.add(UserRole(user_id=admin.id, role_id=role_by_name["admin"].id))
        await record_audit(
            session,
            principal=None,
            username=settings.bootstrap_admin_username,
            action="bootstrap_admin_created",
            resource_type="user",
            resource_id=str(admin.id),
        )

    for item in RELEASE_HISTORY:
        existing = await session.scalar(select(ReleaseNote).where(ReleaseNote.version == item["version"]))
        if existing is None:
            session.add(ReleaseNote(
                version=item["version"], title=item["title"], summary=item["summary"],
                changes=item["changes"], commit_sha=item["commit_sha"], is_current=False,
                released_at=datetime.fromisoformat(item["released_at"]), created_by="system",
            ))

    try:
        changes = json.loads(settings.app_release_changes or "[]")
    except json.JSONDecodeError:
        changes = [settings.app_release_changes]
    normalized_changes = changes if isinstance(changes, list) else [changes]

    release = await session.scalar(select(ReleaseNote).where(ReleaseNote.version == settings.app_version))
    await session.execute(
        select(ReleaseNote).where(ReleaseNote.is_current.is_(True)).with_for_update()
    )
    for old in (await session.scalars(select(ReleaseNote).where(ReleaseNote.is_current.is_(True)))).all():
        if release is None or old.id != release.id:
            old.is_current = False

    if release is None:
        release = ReleaseNote(
            version=settings.app_version,
            title=settings.app_release_title,
            summary=settings.app_release_summary,
            changes=normalized_changes,
            commit_sha=settings.git_commit or None,
            is_current=True,
            released_at=utcnow(),
            created_by="system",
        )
        session.add(release)
    else:
        # Development and release deployments can reuse the same semantic version.
        # Keep one row, but synchronize it with the code actually running now.
        release.title = settings.app_release_title
        release.summary = settings.app_release_summary
        release.changes = normalized_changes
        if settings.git_commit:
            release.commit_sha = settings.git_commit
        release.is_current = True
    await session.commit()
