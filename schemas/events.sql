-- Fleet / device events (faults, wet events, state transitions). payload shape varies by event_type.
--
-- Version: 1.0.0
-- Last modified: 2026-04-30
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`

-- RLS Policies:
-- Owner: read all; Tech: read rows for devices they own (devices.tech_id match)
-- Inserts: firmware / service_role; optional owner insert for support tooling

CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  serial TEXT NOT NULL REFERENCES devices (serial) ON DELETE CASCADE,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARN', 'CRITICAL')),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_events_serial_time ON events (serial, occurred_at DESC);
CREATE INDEX idx_events_event_type ON events (event_type);
