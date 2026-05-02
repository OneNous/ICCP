-- Time-series ICCP readings (device → Supabase). Firmware typically inserts with service role.
--
-- Version: 1.0.0
-- Last modified: 2026-04-30
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`

-- RLS Policies:
-- Owner: read all; Tech: read rows for devices they installed (devices.tech_id match)

CREATE TABLE readings (
  id BIGSERIAL PRIMARY KEY,
  serial TEXT NOT NULL REFERENCES devices (serial) ON DELETE CASCADE,
  observed_at TIMESTAMPTZ NOT NULL,
  polarization_mv INTEGER,
  channel_1_ma DOUBLE PRECISION,
  channel_2_ma DOUBLE PRECISION,
  channel_3_ma DOUBLE PRECISION,
  channel_4_ma DOUBLE PRECISION
);

CREATE INDEX idx_readings_serial_observed ON readings (serial, observed_at DESC);
