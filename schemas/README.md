# Schemas (firmware copy)

Per [`claude.md`](../claude.md) **rule 2**: JSON (and similar) schemas that define contracts with the monorepo backend **are authored in the `coilshield` monorepo**, then **copied here** for validation, codegen, and agent context. Do not treat this folder as the source of truth for cross-repo contracts.

## Workflow

1. Change the canonical schema in `coilshield/schemas/` (monorepo).
2. Sync the updated file(s) into this directory (same relative names when possible).
3. Regenerate Python types via [`codegen/`](../codegen/) → `src/generated/schema_types.py` (see [.claude/schemas.md](../.claude/schemas.md)).

## Current copies

| File | Notes |
|------|--------|
| [uplink-envelope-v1.schema.json](uplink-envelope-v1.schema.json) | Copied from `docs/iot-dual-system/schemas/` for a non-empty root `schemas/` tree; keep in sync with that path or replace both from monorepo when you formalize sync. |
