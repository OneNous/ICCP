# Schemas (firmware copy)

Per [`claude.md`](../claude.md) **rule 2**: JSON (and similar) schemas that define contracts with the monorepo backend **are authored in the `coilshield` monorepo**, then **copied here** for validation, codegen, and agent context. Do not treat this folder as the source of truth for cross-repo contracts.

## Workflow

1. Change the canonical schema in `coilshield/schemas/` (monorepo).
2. Sync the updated file(s) into this directory (same relative names when possible).
3. Regenerate Python types via [`codegen/`](../codegen/) → `src/generated/schema_types.py` (see [.claude/schemas.md](../.claude/schemas.md)).

## Monorepo sync checklist (owner: firmware + platform)

| Step | Command / action |
|------|------------------|
| 1. Identify canonical path | Monorepo `coilshield/schemas/<name>.json` (or path agreed in platform docs). |
| 2. Copy into this repo | `cp <monorepo>/schemas/foo.json schemas/foo.json` — preserve filenames used by codegen. |
| 3. Diff review | `git diff schemas/` — confirm only intended contract changes. |
| 4. Regenerate stub | `python3 codegen/gen_types.py` from repo root. |
| 5. CI | `python3 codegen/gen_types.py --check` must pass (see `.github/workflows/ci.yml`). |

If the monorepo later owns **full** TypedDict / Pydantic generation, replace step 4 with that pipeline and keep this repo’s `codegen/gen_types.py` as a thin wrapper or delete it after documenting the handoff in `docs/DECISIONS.md`.

## Current copies

| File | Notes |
|------|--------|
| [uplink-envelope-v1.schema.json](uplink-envelope-v1.schema.json) | Copied from `docs/iot-dual-system/schemas/` for a non-empty root `schemas/` tree; keep in sync with that path or replace both from monorepo when you formalize sync. |
