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
    app_version: str = "0.9.0-dev.3"
    app_release_title: str = "Snapshot Replay 与结果校验"
    app_release_summary: str = "新增不可变模型可见快照、离线重放和确定性结果校验，Agent 仍未启用。"
    app_release_changes: str = '["模型可见 Snapshot Replay","Result Validator 身份与作用域校验","ToolExecution/Finding/Diagnosis 引用一致性","Finding 类型白名单","日志不可信标记验证","历史 Run 离线回填","事件详情 Replay UI"]'
    git_commit: str = ""
    investigation_raw_json_max_bytes: int = 65_536
    investigation_collection_max_items: int = 200
    investigation_string_max_chars: int = 4_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
