ALTER TABLE investigation_analysis_runs
    ADD COLUMN IF NOT EXISTS parent_run_id BIGINT REFERENCES investigation_analysis_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS run_kind VARCHAR(32) NOT NULL DEFAULT 'deterministic',
    ADD COLUMN IF NOT EXISTS source_snapshot_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS agent_validation_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS agent_validation_report_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_investigation_runs_parent
    ON investigation_analysis_runs (parent_run_id, created_at);

CREATE TABLE IF NOT EXISTS investigation_model_invocations (
    id BIGSERIAL PRIMARY KEY,
    analysis_run_id BIGINT NOT NULL REFERENCES investigation_analysis_runs(id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    invocation_type VARCHAR(64) NOT NULL,
    runtime_name VARCHAR(128) NOT NULL,
    runtime_version VARCHAR(64) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model VARCHAR(255) NOT NULL,
    model_parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    prompt_version VARCHAR(128) NOT NULL,
    request_snapshot_uri VARCHAR(1000) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    response_snapshot_uri VARCHAR(1000),
    response_hash VARCHAR(64),
    status VARCHAR(32) NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    error_code VARCHAR(128),
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_investigation_model_invocation_sequence UNIQUE (analysis_run_id, sequence_number)
);
CREATE INDEX IF NOT EXISTS ix_investigation_model_invocations_run
    ON investigation_model_invocations (analysis_run_id, started_at);

CREATE TABLE IF NOT EXISTS investigation_agent_evaluations (
    id BIGSERIAL PRIMARY KEY,
    analysis_run_id BIGINT NOT NULL REFERENCES investigation_analysis_runs(id) ON DELETE CASCADE,
    parent_run_id BIGINT NOT NULL REFERENCES investigation_analysis_runs(id) ON DELETE CASCADE,
    suite_version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    gates_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_investigation_agent_evaluation_suite UNIQUE (analysis_run_id, suite_version)
);
CREATE INDEX IF NOT EXISTS ix_investigation_agent_evaluations_run
    ON investigation_agent_evaluations (analysis_run_id, created_at);
