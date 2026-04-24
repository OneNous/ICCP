# Uplink agent — process model (read-only, not in the hot loop)

**Goal:** ship telemetry to fleet **without** blocking, coupling, or sharing memory with the `iccp start` control loop. See [adrs/0002-subscription-tiers-local-first.md](adrs/0002-subscription-tiers-local-first.md).

## Placement

| Concern | Owner | Rule |
| ------- | ----- | ---- |
| I2C, PWM, FSM, `Logger.record` | `iccp` / [`iccp_runtime.py`](../../iccp_runtime.py) | **No** network I/O, **no** cloud libraries in this process. |
| Read `latest.json` / read-only SQLite, MQTT/HTTPS, queue | **separate** `coilshield-uplink` (name TBD) or `systemd` oneshot + timer | May crash/restart without affecting ICCP. |

## Recommended systemd layout

- `iccp.service` — unchanged: runs `iccp start` (or your wrapper) after `network-online.target` is **not** required.  
- `iccp-uplink.service` — `After=iccp.service network-online.target`; `Wants=` network; **separate** unit so failure does not mark ICCP failed.  
- **Optional** `Wants=iccp-uplink.service` from `iccp` — *do not* add a hard `Requires` from ICCP to uplink.

## Files and paths (contract)

- **Read-only watch:** the JSON file in `resolved_telemetry_paths()["latest_json"]` (see [firmware-export-contract.md](firmware-export-contract.md); implemented in [`config/settings.py`](../../config/settings.py) `resolved_telemetry_paths`).  
- **SQLite (optional throttled backfill):** `coilshield.db` under the same `LOG_DIR`; open with **`?mode=ro`** URI or a **read-only** file handle; expect schema migrations from the controller — agent must tolerate **unknown** columns (forward-compatible SELECT list).  
- **No writes** to I2C, `request_diag`, `clear_fault` paths, or `latest.json` by the agent.

## Failure modes and UX

| Symptom | User-visible | ICCP action |
| ------- | ------------ | ----------- |
| Uplink can’t read `latest.json` (permissions) | Uplink logs error; TUI/dash unchanged | None |
| Network down; queue spooling | Uplink may log `queue.spool_bytes` in envelope | None |
| Stale `latest.json` (controller stopped) | Fleet sees old `tick_ts_unix`; may alert “device silent” | None—this is a **liveness** issue, not a control bug |
| Auth failure to fleet (401/403) | Local spool; optional agent status file for support | None |
| Agent OOM / crash | systemd restarts; gap in cloud series | None |

**Distinguish:** “**telemetry_incomplete** / recovery merge” in [`logger.py`](../../logger.py) (partial write) vs “**uplink** failed but controller healthy”—only the latter is agent-only.

## Sequencing and backpressure

- The agent should **not** `poll` the hot path file faster than 1 / `SAMPLE_INTERVAL_S` in tight spin (CPU); use `inotify` (Linux) on `latest.json` or a wall-clock min interval.  
- **Max outbound rate** configurable (e.g. cap at 1 msg/s) even if controller runs faster.  
- **Spool cap:** when exceeded, **drop** oldest (documented) or downsample to `on_change` only; never block ICCP.

## Security

- Credentials and TLS live **only** in the agent (see [adrs/0003-device-identity-and-trust.md](adrs/0003-device-identity-and-trust.md)).  
- Harden: run agent as a **dedicated** Unix user with read access to `LOG_DIR` only.

## Optional in-repo code

A reference implementation is **out of scope** for the core firmware, but a thin `scripts/` or sibling repo is allowed per [roadmap-fleet-telemetry.md](../roadmap-fleet-telemetry.md). This document is the spec such an agent must satisfy.
