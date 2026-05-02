-- One-time bench-issued install codes.
-- Bench installer prints a code on the Pi sticker; field tech enters it in BLE
-- provisioning; Pi exchanges it via the device-register Edge Function for a
-- per-device JWT (claim `device_serial`, 90-day exp). See plan §4 and
-- supabase/migrations/20260502090000_device_install_codes_and_jwt_rls.sql.
--
-- Version: 1.0.0
-- Last modified: 2026-05-02
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`

CREATE TABLE device_install_codes (
  code TEXT PRIMARY KEY CHECK (length(code) BETWEEN 8 AND 64),
  serial TEXT NOT NULL CHECK (length(serial) >= 8),
  tech_id TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 days'),
  consumed_at TIMESTAMPTZ,
  consumed_serial TEXT,
  created_by UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_device_install_codes_serial ON device_install_codes (serial);
CREATE INDEX idx_device_install_codes_unconsumed
  ON device_install_codes (expires_at)
  WHERE consumed_at IS NULL;
