CREATE TABLE IF NOT EXISTS investigation_analysis_runs (
    id BIGSERIAL PRIMARY KEY,
    incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL,
    stop_reason VARCHAR(64),
    degradation_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_context_json JSONB,
    engine VARCHAR(64) NOT NULL DEFAULT 'deterministic_oom_v1',
    engine_version VARCHAR(64) NOT NULL DEFAULT '1',
    input_snapshot_hash VARCHAR(64),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_investigation_runs_incident_created
    ON investigation_analysis_runs (incident_id, created_at);

CREATE TABLE IF NOT EXISTS investigation_tool_executions (
    id BIGSERIAL PRIMARY KEY,
    analysis_run_id BIGINT NOT NULL REFERENCES investigation_analysis_runs(id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    tool_name VARCHAR(128) NOT NULL,
    tool_version VARCHAR(64) NOT NULL,
    normalized_input_hash VARCHAR(64) NOT NULL,
    input_json JSONB NOT NULL,
    status VARCHAR(32) NOT NULL,
    structured_output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_visible_output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_output_json JSONB,
    raw_artifact_uri VARCHAR(1000),
    raw_artifact_hash VARCHAR(64),
    cost_units INTEGER NOT NULL DEFAULT 1,
    is_truncated BOOLEAN NOT NULL DEFAULT false,
    error_code VARCHAR(128),
    error_message TEXT,
    retryable BOOLEAN NOT NULL DEFAULT false,
    reused_execution_id BIGINT REFERENCES investigation_tool_executions(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_investigation_tool_sequence UNIQUE (analysis_run_id, sequence_number)
);
CREATE INDEX IF NOT EXISTS ix_investigation_tool_run
    ON investigation_tool_executions (analysis_run_id, created_at);

CREATE TABLE IF NOT EXISTS investigation_findings (
    id VARCHAR(64) PRIMARY KEY,
    analysis_run_id BIGINT NOT NULL REFERENCES investigation_analysis_runs(id) ON DELETE CASCADE,
    tool_execution_id BIGINT NOT NULL REFERENCES investigation_tool_executions(id) ON DELETE CASCADE,
    finding_type VARCHAR(128) NOT NULL,
    subject_ref_json JSONB NOT NULL,
    value_json JSONB NOT NULL,
    polarity VARCHAR(16) NOT NULL,
    quality VARCHAR(16) NOT NULL,
    event_time TIMESTAMPTZ,
    parser_version VARCHAR(64) NOT NULL,
    confirmation_rule VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_investigation_findings_run
    ON investigation_findings (analysis_run_id, created_at);

CREATE TABLE IF NOT EXISTS investigation_diagnosis_results (
    analysis_run_id BIGINT PRIMARY KEY REFERENCES investigation_analysis_runs(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    fact_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    hypotheses_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommended_checks_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    risk_notes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    degradation_reasons_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    validated_output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
