# Firmware export contract (local → uplink, no cloud in-loop)

**Purpose:** define what the **uplink agent** and cloud teams can rely on from the CoilShield **firmware** repository without conflating it with the inner control loop. Complements [iccp-requirements.md](../iccp-requirements.md) §9 (the evolving `latest.json` contract) and [roadmap-fleet-telemetry.md](../roadmap-fleet-telemetry.md).

## What stays “firmware” truth

- **Path resolution:** `resolved_telemetry_paths()` in [`config/settings.py`](../../config/settings.py) — returns absolute paths, including `latest_json` and the SQLite DB name under the **same** `LOG_DIR`.  
- **Staleness / freshness:** the dashboard and TUI use `ts` / `ts_unix` in `latest.json` and, where implemented, `telemetry_incomplete` and `last_valid_channel_snapshot_ts*` (see [`logger.py`](../../logger.py) `recovery_touch_latest` / `record`).  
- **Control cadence:** `SAMPLE_INTERVAL_S` in `config.settings` (single source for how often `Logger.record` is expected to run when the loop is healthy).

## Stable export for fleet v1 (subset)

Uplink v1 should mirror the **envelope** + **system** + **channel** map described in [uplink-v1.md](uplink-v1.md) and the JSON schema. The **ground-truth** files are:

1. **`latest.json`** — primary read for near-real-time snapshots.  
2. **SQLite `readings` table** — optional for historical backfill; schema grows via `ALTER` migrations in `logger` (forward-compatible clients must tolerate new columns).  
3. **`iccp version`** (CLI) — optional for `source.package_version` in the envelope; not written by the controller to `latest.json` today.

## Gaps and proxies (per roadmap)

| Desired product field | In firmware today? | v1 approach |
| -------------------- | ------------------ | ----------- |
| **Humidity** | No first-class field | Omitted; add in firmware + `uplink_schema_version` bump when a sensor exists, or BFF `unknown` |
| **Energy per cycle** (condenser cycle) | **Partial:** `chN_energy_today_j` in `latest.json` and daily energy accumulation in [`logger.py`](../../logger.py) | Use **calendar-day** or rolling integrals; define “cycle” in product, not in v1 DTOs |
| **“Protected” for consumer** | `all_protected` + FSM in channels | Use spec-v2 and legacy fields; document the **business** rule in the BFF ([bff-consumer.md](bff-consumer.md)) |

## What firmware will **not** do (for this initiative)

- **No** MQTT, HTTPS, or long-lived sockets inside [`iccp_runtime.py`](../../iccp_runtime.py) or the `Logger` hot path.  
- **No** “fleet remote management” inside the process that owns PWM (see [iccp-requirements.md](../iccp-requirements.md) §10).  
- **No** embedding customer PII into `latest.json` or SQLite.

## Change management

- When [iccp-requirements.md](../iccp-requirements.md) §9 evolves (dual-write, new keys), the **uplink** contract should:  
  - add optional properties in the JSON schema, or  
  - increment `uplink_schema_version` on breaking changes, with fleet ingest supporting both for a transition.

## Tests / verification (agent authors)

- Run `iccp start` (or `--sim`) and confirm the paths printed at startup include the same `latest.json` the agent will read.  
- Compare `GET /api/live` `telemetry_paths` from the dashboard to `resolved_telemetry_paths()` (same process env).
