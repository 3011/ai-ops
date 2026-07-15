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
    dynamic_planner_enabled: bool = True
    dynamic_planner_max_prometheus_queries: int = 4
    dynamic_planner_max_loki_queries: int = 2
    release_webhook_token: str | None = None
    change_lookback_minutes: int = 120
    trace_query_limit: int = 20
    auth_session_secret: str | None = None
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str | None = None
    session_hours: int = 8
    cookie_secure: bool = False
    app_version: str = "0.9.0"
    app_release_title: str = "可信 Agent Shadow 调查与离线重放"
    app_release_summary: str = "在确定性调查之外增加独立 Agent Shadow、离线 Snapshot Replay、模型调用审计、比较界面和安全评估门槛。"
    app_release_changes: str = '["框架无关 InvestigationAgent Protocol","Agent 输入输出契约与独立校验","模型请求响应 Artifact 审计","离线 Snapshot Agent Replay","独立实时 Agent Shadow Run","确定性与 Agent 比较界面","OOM/CPU 安全与效果评估门槛","默认 investigation_mode=shadow"]'
    git_commit: str = ""
    investigation_raw_json_max_bytes: int = 65_536
    investigation_collection_max_items: int = 200
    investigation_string_max_chars: int = 4_000
    investigation_mode: str = "shadow"
    agent_model_timeout_seconds: float = 45.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
