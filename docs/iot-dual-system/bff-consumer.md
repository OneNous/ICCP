# Consumer BFF and app — design outline

**Role:** the **only** public-facing API for homeowners/facility users to see “how is my unit doing,” without receiving raw per-channel engineering streams. Grounded in [adrs/0001-dual-product-surfaces.md](adrs/0001-dual-product-surfaces.md) and [adrs/0004-pii-and-data-classification.md](adrs/0004-pii-and-data-classification.md).

## Data flow

1. **Fleet** ingests [uplink-v1.md](uplink-v1.md) events into TS + SQL.  
2. **Stream processor** or **batch ETL** builds **rollups** per `device_id` and time window: e.g. `pct_time_protected`, `fault_count_severity`, `last_alert`, `suggested_action`.  
3. **BFF** (GraphQL, REST, or tRPC) reads **rollups** + user↔device mapping from the **account** database — **not** the raw Influx/TS directly from the app.  
4. **Mobile / web** calls BFF with **user session** auth (OAuth, magic link, etc.).

## Suggested BFF resource shapes (illustrative, not an OpenAPI)

| Resource | Exposed fields (example) | Excluded by default |
| -------- | ------------------------ | ------------------- |
| `GET /me/devices` | `label`, `device_id` (opaque), `status: ok|degraded|offline|unknown`, `last_heartbeat_ago_s` | Per-channel mA, `ref_raw_mv` |
| `GET /me/devices/{id}/summary` | `protected_rollup`, `uptime_pct_7d`, `open_alerts[]` with human copy | FSM state strings, `impedance_ohm` time series |
| `POST /me/devices/{id}/alerts/ack` | idempotent ack of user-visible alert | n/a |

**“Protected / not” semantics (product, must match marketing):** Define once from `all_protected` / FSM in telemetry; document lag (aggregation window) in user-facing help.

## Notifications

- Push / email: driven by **rollup transitions** (e.g. `status` from `ok` to `degraded`) or explicit fault classes — **not** on every shunt mA change.  
- Throttle: min interval per device + user to avoid alert fatigue.

## Consent and legal

- Onboarding must capture **opt-in** for cloud telemetry where required by law; **Basic** tier = no BFF data path.  
- Link privacy policy; separate **user PII** (name/email) in account DB from device TS (see ADR-0004).

## Relationship to `iccp` dashboard

- The **on-LAN** [`dashboard.py`](../../dashboard.py) and TUI are **installers/owners with direct file access**—different trust boundary from the BFF. Do not equate the two in security design.
