-- Installation records (who installed which device, where). Removal is modeled via events, not removed_at.
--
-- Version: 1.0.1
-- Last modified: 2026-04-29
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`

-- RLS Policies (see migration):
-- Owner: full access
-- Tech: SELECT/INSERT/UPDATE where tech_id = auth.uid()::text
-- Anon: SELECT (publishable key / command center); table in `supabase_realtime` for live rows.

CREATE TABLE installations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  serial TEXT NOT NULL REFERENCES devices (serial) ON DELETE CASCADE,
  tech_id TEXT NOT NULL,
  site_label TEXT,
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'voided')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_installations_serial ON installations (serial);
CREATE INDEX idx_installations_tech_created ON installations (tech_id, created_at DESC);
