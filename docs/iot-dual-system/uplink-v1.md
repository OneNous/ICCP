# Uplink v1 — DTOs, sampling, queue, deduplication

This document defines the **v1 device→fleet** message contract. The canonical machine-readable form is [schemas/uplink-envelope-v1.schema.json](schemas/uplink-envelope-v1.schema.json) (JSON Schema 2020-12).

**Principles**

- **Versioned** at the **envelope** level (`uplink_schema_version = 1`) so nested payloads can evolve.  
- **Device ground truth** remains local `latest.json` + SQLite; the uplink is a **projected** copy, not a second control path.  
- **No PII** in the device payload (see [adrs/0004-pii-and-data-classification.md](adrs/0004-pii-and-data-classification.md)).

## Envelope (every submission)

| Field | Type | Description |
| ----- | ---- | ----------- |
| `uplink_schema_version` | integer | Must be `1` for this document. |
| `event_id` | string (UUID) | **Unique** per logical transmission attempt; used for idempotent ingest. |
| `event_type` | string | `telemetry_snapshot` (primary), `heartbeat` (no payload, optional), or `log_batch` (future). |
| `device_id` | string | Logical device id (see [adrs/0003-device-identity-and-trust.md](adrs/0003-device-identity-and-trust.md)). |
| `emitted_at` | string | RFC 3339 **UTC** when the **agent** formed the event (not the same as sample time if queued). |
| `source` | object | `latest_json_path`, `log_dir_resolved` (optional echo of `resolved_telemetry_paths()` for support). |
| `sample` | object | `policy`, `mode`, `interval_s`, `tick_ts_unix` (from `latest.json` `ts_unix` for snapshots). |
| `sequence` | integer | **Monotonic** per device (64-bit). Ingest may reject out-of-order duplicates. |
| `queue` | object | If this event was **replayed** from disk queue: `queued_at`, `replay_of_event_id` (optional). |
| `payload` | object | `telemetry_snapshot` when `event_type` is `telemetry_snapshot`. |

**Deduplication (ingest side)**

- Primary idempotency key: **`(device_id, event_id)`** — second delivery with the same `event_id` is a no-op.  
- Ordering guard: **`sequence`** monotonic; if `sequence` is lower than the last seen for `device_id`, treat as **replay** or **clock issue**; fleet policy may quarantine.  
- **Tick alignment:** `sample.tick_ts_unix` should match `latest.json` `ts_unix` for a snapshot; use for de-dupe if the agent sends the same tick twice (same `tick_ts_unix` + different `event_id` may still be valid if policy changed—fleet may collapse by `tick_ts_unix` per device).

**Offline queue (device side)**

- Agent **append-only** spool (files or sqlite) of serialized envelopes; **cap** size (e.g. last N MB or N hours).  
- On reconnect, **FIFO** with exponential backoff; preserve **original** `event_id` for idempotency or document a **new** `event_id` with `queue.replay_of_event_id` (both allowed if ingest supports either).  
- Ingest must not assume wall-clock order of `emitted_at` for queued events.

## `payload.telemetry_snapshot` (v1)

Carries a **copy** of the fields needed for fleet analytics. v1 is a **curated** subset; optional fields are omitted if absent in `latest.json`.

| Block | `latest.json` / SQLite source | Notes |
| ----- | ------------------------------ | ----- |
| System | `ts`, `ts_unix`, `wet`, `wet_channels`, `all_protected`, `any_active`, `any_overprotected`, `fault_latched`, `faults`, `system_alerts`, `telemetry_incomplete` (if present), `ref_*`, `temp_f`, `native_*`, `t_to_system_protected_s`, `cross` | `telemetry_incomplete` and `last_valid_*` matter for operator interpretability. |
| Channels | `channels` map (`"0"`..`"N-1"`) | Include spec-v2 fields when present: `state_v2`, `shift_mv`, `fault_reason`, per-channel `ma`, `duty`, `impedance_ohm`, etc. |
| Firmware | (not in `latest.json` today) | v1: optional `firmware_artifact` object supplied by **agent** from `iccp version` or package env — not required from controller. |

**Sampling policies (`sample.mode`)**

- `raw_tick` — one event per new `ts_unix` read (throttled to agent’s max send rate, e.g. 1–10 Hz if controller runs fast).  
- `throttled_1s` / `throttled_60s` — emit at most one snapshot per window while still **advancing** `sequence`.  
- `on_change` — only when a hash of selected fields changes (higher CPU on device; optional).

**Mapping from SQLite `readings` (optional for agent)**

- Full historical backfill is **not** required for v1. If implemented, map columns `chK_*` and system columns per [`logger.py`](../../logger.py) `_write_db` / row builder; `ts` / `ts_unix` for timestamps.

**Fields intentionally not in v1**

- **humidity** — not first-class in controller ([roadmap-fleet-telemetry.md](../roadmap-fleet-telemetry.md)). Add in `uplink_schema_version = 2` when firmware exposes it, or as an optional `extensions` object with feature flags.  
- **“Energy per cycle”** — not defined as a first-class product metric; today use **`chN_energy_today_j` / daily rollups** from `latest.json` for proxies.

## Interop

- BFF and consumer services **do not** parse this payload directly in the app; they read **rollups** produced by the fleet ([bff-consumer.md](bff-consumer.md)). This contract is **device ↔ fleet** only.

## JSON Schema

See [schemas/uplink-envelope-v1.schema.json](schemas/uplink-envelope-v1.schema.json).
