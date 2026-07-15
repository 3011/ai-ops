ALTER TABLE investigation_analysis_runs
    ADD COLUMN IF NOT EXISTS run_input_json JSONB,
    ADD COLUMN IF NOT EXISTS run_input_schema_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS run_input_source_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS run_input_source_mode VARCHAR(32);

CREATE INDEX IF NOT EXISTS ix_investigation_runs_input_hash
    ON investigation_analysis_runs (run_input_source_hash);

CREATE OR REPLACE FUNCTION prevent_investigation_run_input_mutation()
RETURNS trigger AS $$
BEGIN
    IF OLD.run_input_json IS NOT NULL AND (
        NEW.run_input_json IS DISTINCT FROM OLD.run_input_json OR
        NEW.run_input_schema_version IS DISTINCT FROM OLD.run_input_schema_version OR
        NEW.run_input_source_hash IS DISTINCT FROM OLD.run_input_source_hash OR
        NEW.run_input_source_mode IS DISTINCT FROM OLD.run_input_source_mode
    ) THEN
        RAISE EXCEPTION 'frozen investigation run input cannot be modified';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_investigation_run_input_immutable
    ON investigation_analysis_runs;
CREATE TRIGGER trg_investigation_run_input_immutable
BEFORE UPDATE ON investigation_analysis_runs
FOR EACH ROW EXECUTE FUNCTION prevent_investigation_run_input_mutation();
