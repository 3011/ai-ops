CREATE TABLE IF NOT EXISTS change_events (
  id BIGSERIAL PRIMARY KEY,
  content_hash VARCHAR(64) NOT NULL UNIQUE,
  source VARCHAR(64) NOT NULL DEFAULT 'cicd',
  event_type VARCHAR(64) NOT NULL DEFAULT 'deployment',
  is_test BOOLEAN NOT NULL DEFAULT FALSE,
  cluster VARCHAR(255),
  namespace VARCHAR(255) NOT NULL,
  service VARCHAR(255) NOT NULL,
  environment VARCHAR(128),
  workload_kind VARCHAR(64),
  workload_name VARCHAR(255),
  version VARCHAR(255),
  commit_sha VARCHAR(255),
  image VARCHAR(1000),
  actor VARCHAR(255),
  url VARCHAR(2000),
  title VARCHAR(500) NOT NULL,
  description TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  occurred_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE change_events ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS ix_change_event_scope_time ON change_events(namespace, service, occurred_at);

CREATE TABLE IF NOT EXISTS trace_settings (
  id INTEGER PRIMARY KEY DEFAULT 1,
  provider VARCHAR(64) NOT NULL DEFAULT 'tempo',
  base_url VARCHAR(1000),
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  service_tag VARCHAR(255) NOT NULL DEFAULT 'service.name',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_tested_at TIMESTAMPTZ,
  last_test_status VARCHAR(32),
  last_test_message TEXT
);
