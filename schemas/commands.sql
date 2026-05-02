-- Command-center → device control path. Each row is a command targeted at one device.
-- The Pi polls this table every 10–30 s, filters to its own `device_serial` rows in a
-- non-terminal state (`pending` or `acknowledged`), executes them, and writes the
-- terminal state + `result_payload`. The table doubles as the immutable audit log.
--
-- Version: 1.0.0
-- Last modified: 2026-04-30
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`
--
-- HARD RULE — POLARIZATION SAFETY CUTOFF.
-- The −1080 mV vs Ag/AgCl polarization cutoff and per-channel current limits are
-- enforced in firmware regardless of any command content. A `set_polarization_override`
-- request below the cutoff is rejected with `state = 'invalid'` and the reason in
-- `result_payload.error`; it is NEVER clamped silently.
--
-- Allowed `command_type` values during validation:
--   'clear_fault'                — clear a non-latched fault on a channel
--   'force_probe'                — 3% duty pulse on a channel (wet-detection test)
--   'request_diagnostic'         — uploads recent logs + 24h readings CSV; returns URL
--   'reboot'                     — `systemctl reboot`. Last resort
--   're_commission'              — re-enter commissioning state
--   'disable_channel'            — admin-off a channel (still safety-cutoff-checked)
--   'enable_channel'             — admin-on a channel
--   'set_polarization_override'  — adjust target polarization within safety window
--
-- Forbidden (firmware MUST refuse, even with a valid row):
--   - anything that bypasses the polarization cutoff
--   - reading or modifying BLE bond keys
--   - reading or modifying the Supabase service key / device config
--   - pushing firmware updates (separate path, post-validation)
--   - arbitrary shell command execution

CREATE TABLE commands (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_serial TEXT NOT NULL REFERENCES devices (serial) ON DELETE CASCADE,
  command_type TEXT NOT NULL CHECK (command_type IN (
    'clear_fault',
    'force_probe',
    'request_diagnostic',
    'reboot',
    're_commission',
    'disable_channel',
    'enable_channel',
    'set_polarization_override'
  )),
  params JSONB NOT NULL DEFAULT '{}'::jsonb,
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
    'pending', 'acknowledged', 'succeeded', 'failed', 'invalid'
  )),
  result_payload JSONB,
  issued_by UUID REFERENCES auth.users (id) ON DELETE SET NULL,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  acknowledged_at TIMESTAMPTZ,
  executed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_commands_serial_state
  ON commands (device_serial, state);

-- Hot-path index the Pi uses for its 10–30 s poll: open commands for one serial,
-- oldest first.
CREATE INDEX idx_commands_poll_queue
  ON commands (device_serial, issued_at)
  WHERE state IN ('pending', 'acknowledged');
