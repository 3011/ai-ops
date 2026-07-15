from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import ModelSettings

settings = get_settings()


@dataclass(slots=True)
class RuntimeModelConfig:
    enabled: bool
    base_url: str
    model: str
    api_key: str | None
    source: str


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API Base URL 必须是有效的 http/https 地址")
    return normalized


def _fernet() -> Fernet:
    if not settings.settings_encryption_key:
        raise RuntimeError("SETTINGS_ENCRYPTION_KEY 未配置")
    return Fernet(settings.settings_encryption_key.encode("utf-8"))


def encrypt_api_key(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_api_key(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("模型 API Key 无法解密，请重新保存") from exc


def public_model_settings(row: ModelSettings | None) -> dict:
    return {
        "provider": row.provider if row else "openai-compatible",
        "base_url": row.base_url if row else settings.llm_base_url.rstrip("/"),
        "model": row.model if row else settings.llm_model,
        "enabled": row.enabled if row else bool(settings.llm_api_key),
        "api_key_configured": bool(row.api_key_encrypted) if row else bool(settings.llm_api_key),
        "source": "database" if row else "environment",
        "updated_at": row.updated_at if row else None,
        "last_tested_at": row.last_tested_at if row else None,
        "last_test_status": row.last_test_status if row else None,
        "last_test_message": row.last_test_message if row else None,
    }


def runtime_from_row(row: ModelSettings | None) -> RuntimeModelConfig:
    if row is None:
        return RuntimeModelConfig(
            enabled=bool(settings.llm_api_key),
            base_url=settings.llm_base_url.rstrip("/"),
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            source="environment",
        )
    return RuntimeModelConfig(
        enabled=row.enabled,
        base_url=normalize_base_url(row.base_url),
        model=row.model.strip(),
        api_key=decrypt_api_key(row.api_key_encrypted) or settings.llm_api_key,
        source="database",
    )


async def load_runtime_model_config(
    session: AsyncSession | None = None,
) -> RuntimeModelConfig:
    if session is not None:
        return runtime_from_row(await session.get(ModelSettings, 1))
    async with SessionLocal() as own_session:
        return runtime_from_row(await own_session.get(ModelSettings, 1))


def utcnow() -> datetime:
    return datetime.now(UTC)
