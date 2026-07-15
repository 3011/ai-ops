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
    app_version: str = "0.9.0-dev.4"
    app_release_title: str = "冻结调查输入与历史 Replay 适配"
    app_release_summary: str = "在 AnalysisRun 创建时冻结真实输入，并使用版本化适配器恢复 0.8.x/0.9.x 历史 Replay。"
    app_release_changes: str = '["AnalysisRun 原生输入冻结","数据库不可变触发器","native_frozen/historical_reconstructed/legacy_incomplete 来源模式","0.8.x 与 0.9.x 历史输入适配","Incident 后续变化不影响旧 Snapshot","真实 legacy contract violation 保留"]'
    git_commit: str = ""
    investigation_raw_json_max_bytes: int = 65_536
    investigation_collection_max_items: int = 200
    investigation_string_max_chars: int = 4_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
