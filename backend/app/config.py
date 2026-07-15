from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)
    app_name: str = "AIOps Console"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://aiops:aiops@postgresql:5432/aiops"
    prometheus_url: str = "http://monitoring-kube-prometheus-prometheus.monitoring.svc:9090"
    loki_url: str = "http://loki.monitoring.svc:3100"
    alertmanager_url: str = "http://monitoring-kube-prometheus-alertmanager.monitoring.svc:9093"
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    settings_encryption_key: str | None = None
    worker_poll_seconds: float = 2.0
    worker_lock_timeout_seconds: int = 300
    worker_max_attempts: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
