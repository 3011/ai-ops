ALTER TABLE investigation_analysis_runs
    ADD COLUMN IF NOT EXISTS budget_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS budget_usage_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_investigation_tool_cache_lookup
ON investigation_tool_executions (
    analysis_run_id,
    tool_name,
    tool_version,
    normalized_input_hash
);

CREATE TABLE IF NOT EXISTS investigation_artifacts (
    id VARCHAR(64) PRIMARY KEY,
    sha256 VARCHAR(64) NOT NULL UNIQUE,
    content_type VARCHAR(128) NOT NULL DEFAULT 'application/json',
    size_bytes BIGINT NOT NULL,
    compression VARCHAR(32) NOT NULL DEFAULT 'zlib',
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_investigation_artifacts_sha256
    ON investigation_artifacts (sha256);
