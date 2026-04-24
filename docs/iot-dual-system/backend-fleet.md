# Fleet / operator backend — design outline

**Audience:** implementers of cloud services (not in this repository). Ties to [uplink-v1.md](uplink-v1.md) and [adrs/0001-dual-product-surfaces.md](adrs/0001-dual-product-surfaces.md).

## Ingest

- **MQTT** broker (per-tenant or shared with ACLs) **or** **HTTPS** `POST` to a regional API.  
- **Idempotent** write using `(device_id, event_id)` from the envelope.  
- **Version gate:** reject unknown `uplink_schema_version` into a **quarantine** topic/table for triage, not the hot path.  
- **Time:** store server receive time and `sample.tick_ts_unix`; do not re-order solely by `emitted_at` (clock skew, offline queue).

## Data stores (logical)

| Store | Content | Retention (example) |
| ----- | ------- | -------------------- |
| **Time-series** | Per-device numeric series: `ma`, `impedance_ohm`, `ref_shift_mv`, `temp_f`, per-channel, plus sparse events (faults) | Hot: 6–12 months full resolution; downsampled beyond |
| **Relational (Postgres)** | Device registry, `device_id` ↔ org, RMA, firmware channel, last-seen, subscription tier | Indefinite with soft-delete |
| **Object / cold** | Parquet/CSV export for warranty bundles | 2+ years, jurisdiction-dependent |

**Hot / cold policy:** per [roadmap-fleet-telemetry.md](../roadmap-fleet-telemetry.md); use tiered storage for cost (e.g. S3 + Athena, or Influx with retention + downsampling rules).

## Operator experience

- **Per-install view:** last N days impedance trend, ref stability, wet/protecting time, fault timeline.  
- **Fleet health:** “silent devices” = no ingest with `tick_ts_unix` advancing past SLAs.  
- **Warranty / export:** bundle CSV + redacted PII; separate from [bff-consumer.md](bff-consumer.md).

## Alerting and SLOs

- Device offline (no uplink) vs “controller may still run” — derive from `tick_ts_unix` lag vs **expected** `SAMPLE_INTERVAL_S` (see [firmware-export-contract.md](firmware-export-contract.md) for `SAMPLE_INTERVAL_S` source: `config.settings`).  
- Ingest error rate, broker saturation, and dead-letter from schema validation.

## Security and multi-tenancy

- **RBAC:** roles map to OEM, distributor, and internal L2.  
- **Audit** support actions: “viewed device X” in regulated contexts.  
- **Network:** mTLS to broker; least-privilege IoT policy per `device_id` topic prefix `tenant/{tid}/device/{device_id}/telemetry`.

## What this backend does *not* do

- It does not serve **end-user** mobile clients directly with full detail — that is the **consumer BFF** ([bff-consumer.md](bff-consumer.md)), fed by **materialized rollups** or stream processors.
