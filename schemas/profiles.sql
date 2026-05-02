-- App-facing profile row keyed by auth.users (display name + email for UI).
--
-- Version: 1.2.0
-- Last modified: 2026-05-01
-- DO NOT EDIT GENERATED CONSUMERS — UPDATE HERE AND RUN `pnpm run codegen:all`

-- RLS Policies:
-- Owner: read all
-- Authenticated: SELECT/UPDATE own row only

CREATE TABLE profiles (
  id UUID PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  display_name TEXT,
  email TEXT,
  company TEXT,
  service_region TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
