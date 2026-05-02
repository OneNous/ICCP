-- Field / install tickets for technicians (validation scope per .claude/backend.md smoke tests).
--
-- Version: 1.0.3
-- Last modified: 2026-04-30
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`

-- RLS Policies:
-- Owner: full access
-- Tech: SELECT open pool (unassigned) or own assignments; UPDATE to claim (set assigned_tech_id) or progress assigned rows
-- `updated_at` is set on every UPDATE via trigger `trg_tickets_touch_updated_at` (migration).
-- `opened_by_client_id` — homeowner-submitted service tickets (see `20260504100100_devices_claim_for_client_app.sql`).

CREATE TABLE tickets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  body TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'blocked', 'done', 'cancelled')),
  priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  assigned_tech_id TEXT,
  installation_id UUID REFERENCES installations (id) ON DELETE SET NULL,
  device_serial TEXT REFERENCES devices (serial) ON DELETE SET NULL,
  opened_by_client_id UUID REFERENCES auth.users (id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tickets_assigned ON tickets (assigned_tech_id, status);
CREATE INDEX idx_tickets_installation ON tickets (installation_id);
CREATE INDEX idx_tickets_device_serial ON tickets (device_serial);
CREATE INDEX idx_tickets_opened_by_client ON tickets (opened_by_client_id)
  WHERE opened_by_client_id IS NOT NULL;
