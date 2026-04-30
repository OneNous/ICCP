# IoT dual-system planning (iot-dual-system-future)

This folder is the **implementation of the product roadmap** for a future “dual system” line: a **fleet/operator backend** (full engineering data) and a **consumer-facing BFF** (aggregated status), with the on-device ICCP stack remaining **local-first** (see [roadmap-fleet-telemetry.md](../roadmap-fleet-telemetry.md) for the one-page sketch).

| Document | Purpose |
| -------- | ------- |
| [adrs/0001-dual-product-surfaces.md](adrs/0001-dual-product-surfaces.md) | ADR — two product surfaces, separation of concerns |
| [adrs/0002-subscription-tiers-local-first.md](adrs/0002-subscription-tiers-local-first.md) | ADR — Basic vs cloud tiers, controller has no hard network dependency |
| [adrs/0003-device-identity-and-trust.md](adrs/0003-device-identity-and-trust.md) | ADR — device identity, bootstrap, key rotation |
| [adrs/0004-pii-and-data-classification.md](adrs/0004-pii-and-data-classification.md) | ADR — PII boundaries, what fleet vs consumer may see |
| [uplink-v1.md](uplink-v1.md) | Versioned uplink DTOs, mapping from `latest.json` / SQLite, deduplication |
| [schemas/uplink-envelope-v1.schema.json](schemas/uplink-envelope-v1.schema.json) | JSON Schema (Draft 2020-12) for the uplink **envelope** + **telemetry snapshot** |
| [agent-process-model.md](agent-process-model.md) | Read-only **uplink agent** process, paths, failure modes, isolation from the control loop |
| [backend-fleet.md](backend-fleet.md) | Fleet backend: ingest, time-series, relational store, operations |
| [bff-consumer.md](bff-consumer.md) | Consumer BFF and app: rollups, alerts, consent, API shape |
| [firmware-export-contract.md](firmware-export-contract.md) | Firmware expectations: stable export subset, `resolved_telemetry_paths`, gaps |

**In scope for this documentation:** cross-team contracts and system design. **Out of scope:** cloud service code (not in this repository per [roadmap-fleet-telemetry.md](../roadmap-fleet-telemetry.md)).

**Related in-repo:** [iccp-requirements.md](../iccp-requirements.md) §9 (`latest.json` contract, dual-write policy); §10 (fleet/remote still **out of scope** for the real-time control loop). Implementation touchpoints: [`logger.py`](../src/../logger.py), [`iccp_runtime.py`](../src/../iccp_runtime.py), [`config/settings.py`](../../config/settings.py) (`resolved_telemetry_paths`).
