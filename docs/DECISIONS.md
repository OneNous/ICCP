# Firmware decisions (hub)

Per [`claude.md`](../claude.md): log **architectural** choices here with date, author/agent, reasoning, and consequences.

## Template

```markdown
### YYYY-MM-DD — Short title

**Decision:** …

**Context:** …

**Consequences:** …
```

### 2026-04-30 — Close remaining `claude.md` gaps (cloud queue, tech API shell, logging policy)

**Decision:** (1) Add a **sidecar** SQLite queue `LOG_DIR/cloud_queue.db` with a background **Supabase flush worker** (default **off** via `COILSHIELD_CLOUD_SYNC=0`), enqueueing JSON snapshots after each successful `latest.json` write—never blocking the control loop. (2) Register a Flask **Blueprint** [`src/tech_api.py`](../src/tech_api.py) under `/tech` when `COILSHIELD_TECH_API=1`, with unauthenticated `GET /tech/info` and HMAC-gated `GET /tech/status` using `COILSHIELD_TECH_BOND_KEY` (hex) until BLE bond storage exists. (3) Document **stdout vs structured logger** policy; route JSONL thermal notices through `cli_events.emit` where trivial. (4) Add `codegen/gen_types.py` + optional CI drift check.

**Context:** `.claude/cloud-sync.md` and `.claude/tech-api.md` describe target behavior; validation phase requires defaults **off** and no control-path coupling.

**Consequences:** Optional `pip install -e ".[supabase]"` on devices that upload; Pi must set env before enabling sync. Tech app must set bond key on bench. See [`SECURITY.md`](SECURITY.md) for key rotation if history ever contained secrets.

### 2026-04-30 — Credential exposure and git history

**Decision:** Treat any file that contained **private keys** (e.g. SSH `pi` / `pi.pub` that were once tracked) as **burned** until rotated; prefer **rotation + filter-repo** over “delete file” alone.

**Context:** Removing files from `main` does not remove blobs from clones or forks.

**Consequences:** Owners rotate keys; follow [`SECURITY.md`](SECURITY.md); no secrets in committed `.env`.

### 2026-04-29 — Repo layout aligned to `claude.md` + optional Supabase

**Decision:** Move all application Python under `src/` (with `pyproject.toml` `package-dir`), keep `config/` at repo root, add `.claude/` task rules, `systemd/coilshield.service`, `schemas/` + `schemas/README.md`, doc hubs (`docs/ARCHITECTURE.md`, etc.), and optional Supabase wiring (`src/cloud_sync.py`, `iccp supabase-ping`, `.env.example`). Canonical agent hub is root `claude.md`.

**Context:** `claude.md` describes `coilshield-firmware` layout and validation-phase constraints; this ICCP repo is the same product surface with a different package name (`coilshield-iccp`).

**Consequences:** Tests and CLI prepend `src/` on `sys.path`; docs links use `../src/*.py`; Pi installs use `pip install -e .` as before. Supabase remains **reporting-only**—no control-path dependency (rules 3 and 5). Duplicate `files/*.md` mirror removed in favor of `.claude/` + root `claude.md` only.

## Related logs elsewhere

- Cross-cutting product / IoT ADRs: [iot-dual-system/adrs/](iot-dual-system/adrs/)
- Field and session notes (historical): [coilshield-field-session-notes.md](coilshield-field-session-notes.md)
- Session summary pointer file: [coilshield-session-summary.md](../coilshield-session-summary.md)

*(Add new dated entries after the Template block and before “Related logs”, newest first.)*
