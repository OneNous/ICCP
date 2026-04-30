# Firmware decisions (hub)

Per [`claude.md`](../claude.md): log **architectural** choices here with date, author/agent, reasoning, and consequences.

## Template

```markdown
### YYYY-MM-DD — Short title

**Decision:** …

**Context:** …

**Consequences:** …
```

### 2026-04-29 — Repo layout aligned to `claude.md` + optional Supabase

**Decision:** Move all application Python under `src/` (with `pyproject.toml` `package-dir`), keep `config/` at repo root, add `.claude/` task rules, `systemd/coilshield.service`, `schemas/` + `schemas/README.md`, doc hubs (`docs/ARCHITECTURE.md`, etc.), and optional Supabase wiring (`src/cloud_sync.py`, `iccp supabase-ping`, `.env.example`). Canonical agent hub is root `claude.md`.

**Context:** `claude.md` describes `coilshield-firmware` layout and validation-phase constraints; this ICCP repo is the same product surface with a different package name (`coilshield-iccp`).

**Consequences:** Tests and CLI prepend `src/` on `sys.path`; docs links use `../src/*.py`; Pi installs use `pip install -e .` as before. Supabase remains **reporting-only**—no control-path dependency (rules 3 and 5). Duplicate `files/*.md` mirror removed in favor of `.claude/` + root `claude.md` only.

## Related logs elsewhere

- Cross-cutting product / IoT ADRs: [iot-dual-system/adrs/](iot-dual-system/adrs/)
- Field and session notes (historical): [coilshield-field-session-notes.md](coilshield-field-session-notes.md)
- Session summary pointer file: [coilshield-session-summary.md](../coilshield-session-summary.md)

*(Add new dated entries after the Template block and before “Related logs”, newest first.)*
