from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    Principal,
    clear_session_cookie,
    hash_password,
    principal_for_user,
    record_audit,
    set_session_cookie,
    verify_password,
)
from app.db import get_session
from app.models import (
    AuditLog,
    Permission,
    ReleaseNote,
    Role,
    RolePermission,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/v1")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "未登录或会话已失效")
    return principal


class LoginInput(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=512)


class ChangePasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=10, max_length=512)


class UserCreateInput(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=10, max_length=512)
    role_ids: list[int] = Field(min_length=1)
    is_active: bool = True
    must_change_password: bool = True


class UserUpdateInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    role_ids: list[int] = Field(min_length=1)
    is_active: bool = True
    must_change_password: bool | None = None
    new_password: str | None = Field(default=None, min_length=10, max_length=512)


class RoleCreateInput(BaseModel):
    name: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    permission_ids: list[int] = Field(default_factory=list)


class RoleUpdateInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    permission_ids: list[int] = Field(default_factory=list)


class ReleaseCreateInput(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=4000)
    changes: list[str] = Field(min_length=1, max_length=100)
    commit_sha: str | None = Field(default=None, max_length=64)
    is_current: bool = True
    released_at: datetime | None = None


async def role_rows_for_user(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    roles = (
        await session.scalars(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.name)
        )
    ).all()
    return [
        {"id": role.id, "name": role.name, "display_name": role.display_name}
        for role in roles
    ]


async def user_payload(session: AsyncSession, user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "roles": await role_rows_for_user(session, user.id),
    }


async def role_payload(session: AsyncSession, role: Role) -> dict[str, Any]:
    permissions = (
        await session.scalars(
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
            .order_by(Permission.category, Permission.code)
        )
    ).all()
    user_count = await session.scalar(
        select(func.count(UserRole.user_id)).where(UserRole.role_id == role.id)
    )
    return {
        "id": role.id,
        "name": role.name,
        "display_name": role.display_name,
        "description": role.description,
        "is_system": role.is_system,
        "user_count": int(user_count or 0),
        "permissions": [
            {
                "id": item.id,
                "code": item.code,
                "name": item.name,
                "category": item.category,
                "description": item.description,
            }
            for item in permissions
        ],
    }


def release_payload(row: ReleaseNote) -> dict[str, Any]:
    return {
        "id": row.id,
        "version": row.version,
        "title": row.title,
        "summary": row.summary,
        "changes": row.changes or [],
        "commit_sha": row.commit_sha,
        "is_current": row.is_current,
        "released_at": row.released_at,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


@router.post("/auth/login")
async def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    username = payload.username.strip().lower()
    user = await session.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        await record_audit(
            session,
            principal=None,
            username=username,
            action="login_failed",
            resource_type="session",
            details={"reason": "invalid_credentials"},
            request=request,
        )
        await session.commit()
        raise HTTPException(401, "用户名或密码错误")
    user.last_login_at = utcnow()
    principal = await principal_for_user(user, session)
    await record_audit(
        session,
        principal=principal,
        action="login_success",
        resource_type="session",
        request=request,
    )
    await session.commit()
    set_session_cookie(response, user)
    return {"user": principal.payload()}


@router.get("/auth/me")
async def me(request: Request) -> dict[str, Any]:
    return {"user": current_principal(request).payload()}


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    principal = current_principal(request)
    await record_audit(
        session,
        principal=principal,
        action="logout",
        resource_type="session",
        request=request,
    )
    await session.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/auth/change-password")
async def change_password(
    payload: ChangePasswordInput,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    principal = current_principal(request)
    user = await session.get(User, principal.user_id)
    if user is None or not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(400, "新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.token_version += 1
    await record_audit(
        session,
        principal=principal,
        action="password_changed",
        resource_type="user",
        resource_id=str(user.id),
        request=request,
    )
    await session.commit()
    new_principal = await principal_for_user(user, session)
    set_session_cookie(response, user)
    return {"user": new_principal.payload()}


@router.get("/users")
async def list_users(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (await session.scalars(select(User).order_by(User.username))).all()
    return {"items": [await user_payload(session, row) for row in rows], "total": len(rows)}


@router.post("/users", status_code=201)
async def create_user(
    payload: UserCreateInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    principal = current_principal(request)
    username = payload.username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(422, "用户名只能包含小写字母、数字、点、下划线和短横线")
    if await session.scalar(select(User.id).where(User.username == username)):
        raise HTTPException(409, "用户名已存在")
    roles = (
        await session.scalars(select(Role).where(Role.id.in_(set(payload.role_ids))))
    ).all()
    if len(roles) != len(set(payload.role_ids)):
        raise HTTPException(422, "包含不存在的角色")
    user = User(
        username=username,
        display_name=payload.display_name.strip(),
        email=(payload.email or "").strip().lower() or None,
        password_hash=hash_password(payload.password),
        is_active=payload.is_active,
        must_change_password=payload.must_change_password,
    )
    session.add(user)
    await session.flush()
    for role in roles:
        session.add(UserRole(user_id=user.id, role_id=role.id))
    await record_audit(
        session,
        principal=principal,
        action="user_created",
        resource_type="user",
        resource_id=str(user.id),
        details={"username": username, "roles": [role.name for role in roles]},
        request=request,
    )
    await session.commit()
    return await user_payload(session, user)


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: UserUpdateInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    principal = current_principal(request)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if user.id == principal.user_id and not payload.is_active:
        raise HTTPException(400, "不能停用当前登录用户")
    roles = (
        await session.scalars(select(Role).where(Role.id.in_(set(payload.role_ids))))
    ).all()
    if len(roles) != len(set(payload.role_ids)):
        raise HTTPException(422, "包含不存在的角色")
    if user.id == principal.user_id and not any(role.name == "admin" for role in roles):
        raise HTTPException(400, "当前管理员不能移除自己的管理员角色")
    user.display_name = payload.display_name.strip()
    user.email = (payload.email or "").strip().lower() or None
    user.is_active = payload.is_active
    if payload.must_change_password is not None:
        user.must_change_password = payload.must_change_password
    if payload.new_password:
        user.password_hash = hash_password(payload.new_password)
        user.must_change_password = True
        user.token_version += 1
    await session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    for role in roles:
        session.add(UserRole(user_id=user.id, role_id=role.id))
    await record_audit(
        session,
        principal=principal,
        action="user_updated",
        resource_type="user",
        resource_id=str(user.id),
        details={"username": user.username, "active": user.is_active, "roles": [role.name for role in roles]},
        request=request,
    )
    await session.commit()
    await session.refresh(user)
    return await user_payload(session, user)


@router.get("/permissions")
async def list_permissions(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (await session.scalars(select(Permission).order_by(Permission.category, Permission.code))).all()
    return {
        "items": [
            {"id": row.id, "code": row.code, "name": row.name, "category": row.category, "description": row.description}
            for row in rows
        ]
    }


@router.get("/roles")
async def list_roles(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (await session.scalars(select(Role).order_by(Role.name))).all()
    return {"items": [await role_payload(session, row) for row in rows], "total": len(rows)}


@router.post("/roles", status_code=201)
async def create_role(
    payload: RoleCreateInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    principal = current_principal(request)
    name = payload.name.strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise HTTPException(422, "角色标识格式不正确")
    if await session.scalar(select(Role.id).where(Role.name == name)):
        raise HTTPException(409, "角色已存在")
    permissions = (
        await session.scalars(select(Permission).where(Permission.id.in_(set(payload.permission_ids))))
    ).all() if payload.permission_ids else []
    if len(permissions) != len(set(payload.permission_ids)):
        raise HTTPException(422, "包含不存在的权限")
    role = Role(name=name, display_name=payload.display_name.strip(), description=payload.description, is_system=False)
    session.add(role)
    await session.flush()
    for permission in permissions:
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await record_audit(
        session,
        principal=principal,
        action="role_created",
        resource_type="role",
        resource_id=str(role.id),
        details={"name": name, "permissions": [item.code for item in permissions]},
        request=request,
    )
    await session.commit()
    return await role_payload(session, role)


@router.put("/roles/{role_id}")
async def update_role(
    role_id: int,
    payload: RoleUpdateInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    principal = current_principal(request)
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")
    permissions = (
        await session.scalars(select(Permission).where(Permission.id.in_(set(payload.permission_ids))))
    ).all() if payload.permission_ids else []
    if len(permissions) != len(set(payload.permission_ids)):
        raise HTTPException(422, "包含不存在的权限")
    if role.name == "admin" and len(permissions) != await session.scalar(select(func.count(Permission.id))):
        raise HTTPException(400, "系统管理员角色必须保留全部权限")
    role.display_name = payload.display_name.strip()
    role.description = payload.description
    await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    for permission in permissions:
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await record_audit(
        session,
        principal=principal,
        action="role_updated",
        resource_type="role",
        resource_id=str(role.id),
        details={"name": role.name, "permissions": [item.code for item in permissions]},
        request=request,
    )
    await session.commit()
    await session.refresh(role)
    return await role_payload(session, role)


@router.get("/audit-logs")
async def list_audit_logs(
    username: str | None = None,
    action: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if username:
        filters.append(AuditLog.username == username)
    if action:
        filters.append(AuditLog.action == action)
    limit = max(1, min(limit, 500))
    rows = (
        await session.scalars(
            select(AuditLog).where(*filters).order_by(AuditLog.created_at.desc()).limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "username": row.username,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "details": row.details,
                "ip_address": row.ip_address,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.get("/releases")
async def list_releases(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (await session.scalars(select(ReleaseNote).order_by(ReleaseNote.released_at.desc()))).all()
    return {"items": [release_payload(row) for row in rows], "current": next((release_payload(row) for row in rows if row.is_current), None)}


@router.post("/releases", status_code=201)
async def create_release(
    payload: ReleaseCreateInput,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    principal = current_principal(request)
    version = payload.version.strip()
    if await session.scalar(select(ReleaseNote.id).where(ReleaseNote.version == version)):
        raise HTTPException(409, "版本号已存在")
    if payload.is_current:
        for row in (await session.scalars(select(ReleaseNote).where(ReleaseNote.is_current.is_(True)))).all():
            row.is_current = False
    release = ReleaseNote(
        version=version,
        title=payload.title.strip(),
        summary=payload.summary,
        changes=[str(item)[:2000] for item in payload.changes],
        commit_sha=(payload.commit_sha or "").strip() or None,
        is_current=payload.is_current,
        released_at=payload.released_at or utcnow(),
        created_by=principal.username,
    )
    session.add(release)
    await session.flush()
    await record_audit(
        session,
        principal=principal,
        action="release_created",
        resource_type="release",
        resource_id=str(release.id),
        details={"version": version, "current": payload.is_current},
        request=request,
    )
    await session.commit()
    return release_payload(release)
