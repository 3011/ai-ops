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
    app_version: str = "0.8.1"
    app_release_title: str = "可信 Tool Runtime 加固"
    app_release_summary: str = "将单工具调查重构为通用注册、预算、缓存、失败语义和 Artifact 管线。"
    app_release_changes: str = '["ToolRegistry 与通用 ToolRuntime.execute","InvestigationBudget 与 BudgetLedger","同 Run、同 Target、同参数缓存复用","Kubernetes 404/403/429/5xx/超时精确语义","Resolver 分页、直接 GET 与 controller owner","Finding 自动回填 ToolResult","Artifact 脱敏、截断、压缩和完整 Hash"]'
    git_commit: str = ""
    investigation_raw_json_max_bytes: int = 65_536
    investigation_collection_max_items: int = 200
    investigation_string_max_chars: int = 4_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
