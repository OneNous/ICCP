-- Fleet device registry (PostgreSQL / Supabase).
-- `tech_id` SHOULD match `auth.uid()::text` (JWT sub) for the commissioning technician.
--
-- Version: 1.3.0
-- Last modified: 2026-04-30
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`
--
-- RLS / Realtime (authoritative SQL in `supabase/migrations/`):
-- - `authenticated`: owner + tech policies (JWT `app_metadata.role`) — see initial fleet migration.
-- - `anon`: SELECT on `devices` and `telemetry_points` for publishable-key clients (command center).
-- - Realtime publication `supabase_realtime` includes `devices`, `commissioning_results`, `telemetry_points`,
--   `readings`, `events`, `installations`, `tickets` (see `supabase/migrations/`).
-- - Client-app claim path: `claimed_by_user_id` + `claim_token` (+ expiry) — see migration
--   `20260504100100_devices_claim_for_client_app.sql` and `docs/DECISIONS.md`.

CREATE TABLE devices (
  serial TEXT PRIMARY KEY CHECK (length(serial) >= 8),
  install_date DATE NOT NULL,
  location_label TEXT,
  location_lat DOUBLE PRECISION,
  location_lon DOUBLE PRECISION,
  tech_id TEXT NOT NULL,
  registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  connection_state TEXT NOT NULL DEFAULT 'unknown'
    CHECK (connection_state IN ('unknown', 'offline', 'online', 'fault')),
  last_seen_at TIMESTAMPTZ,
  fault_summary TEXT,
  claimed_by_user_id UUID REFERENCES auth.users (id) ON DELETE SET NULL,
  claim_token TEXT,
  claim_token_expires_at TIMESTAMPTZ,
  claimed_at TIMESTAMPTZ
);

CREATE INDEX idx_devices_claimed_by_user ON devices (claimed_by_user_id)
  WHERE claimed_by_user_id IS NOT NULL;

CREATE TABLE commissioning_results (
  serial TEXT PRIMARY KEY REFERENCES devices (serial) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'passed', 'failed')),
  percent DOUBLE PRECISION NOT NULL DEFAULT 0,
  step TEXT,
  error TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE telemetry_points (
  id BIGSERIAL PRIMARY KEY,
  serial TEXT NOT NULL REFERENCES devices (serial) ON DELETE CASCADE,
  "time" TIMESTAMPTZ NOT NULL DEFAULT now(),
  shift_mV DOUBLE PRECISION,
  total_mA DOUBLE PRECISION,
  payload_json TEXT
);

CREATE INDEX idx_telemetry_serial_time ON telemetry_points (serial, "time");
