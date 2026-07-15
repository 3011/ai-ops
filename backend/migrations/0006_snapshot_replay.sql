CREATE TABLE IF NOT EXISTS investigation_replay_snapshots (
    id VARCHAR(64) PRIMARY KEY,
    analysis_run_id BIGINT NOT NULL REFERENCES investigation_analysis_runs(id) ON DELETE CASCADE,
    snapshot_version VARCHAR(64) NOT NULL,
    validator_version VARCHAR(64) NOT NULL,
    source_hash VARCHAR(64) NOT NULL,
    snapshot_hash VARCHAR(64) NOT NULL,
    snapshot_json JSONB NOT NULL,
    validation_status VARCHAR(32) NOT NULL,
    validation_report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_investigation_replay_source UNIQUE (analysis_run_id, snapshot_version, validator_version, source_hash)
);
CREATE INDEX IF NOT EXISTS ix_investigation_replay_run_created
    ON investigation_replay_snapshots (analysis_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_investigation_replay_snapshot_hash
    ON investigation_replay_snapshots (snapshot_hash);
