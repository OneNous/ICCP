# ADR-0004: PII and data classification (fleet vs consumer)

## Status

Accepted (planning / architecture). Legal labels (PII, CCPA, GDPR) require counsel; this ADR is an **engineering** classification to drive APIs.

## Context

Engineering telemetry can indirectly identify **location and behavior** of a site (e.g. patterns of wet/dry, time zones, fault codes). End-user **accounts** in the consumer app are classic **PII** (email, phone, name). Fleet operators are **internal or partner** identities with a different access pattern.

## Decision

**Data classes**

| Class | Examples | Default placement |
| ----- | -------- | ----------------- |
| **A — Device technical** | Per-channel mA, impedance, `ref_raw_mv`, faults, `latest.json` hashes | **Fleet** only; long retention for warranty |
| **B — Aggregated / non-identifying status** | “Protected yes/no this week,” % uptime, alert “reference unstable” (no channel detail) | **Consumer BFF** after **aggregation**; short-lived tokens to app |
| **C — User PII** | Name, email, support tickets | **Account / CRM**; not written to the device time-series as raw columns |
| **D — Site address / facility location** (often PII) | Service address, geohash | **Fleet + CRM**; **not** in consumer API unless the user explicitly consents to display “my unit” in-app |

**Rules**

1. **Homeowner and facility apps** receive only **class B** (and C only through normal **user profile** APIs, not via raw telemetry).  
2. **Fleet** may store **A +** provisioning metadata, RMA, firmware channel. Access **RBAC**-scoped (OEM, partner, L2; not every engineer sees all tenants).  
3. **Uplink payloads** (see [uplink-v1.md](../uplink-v1.md)) must **not** require customer name or address on the wire from the device; that lives in **fleet/CRM** keyed by `device_id`. If an installer flow embeds a label in a **separate** installer app API, that is **not** the same as stuffing PII into MQTT topics.  
4. **Export / deletion**: consumer **account** deletion is an account-DB process; **device** telemetry deletion for GDPR may require “delete this device’s series” in fleet; define retention windows in [backend-fleet.md](../backend-fleet.md).

**Consumer vs ground truth (semantic)**

- The **BFF** exposes business meanings (“Your coil is in a protecting state” / “Service recommended”) using **rollups** from [bff-consumer.md](../bff-consumer.md), not a second measurement path.  
- **“Protected / not”** for the app must be defined in product spec: e.g. from `all_protected` and/or FSM in `latest.json` (see [firmware-export-contract.md](../firmware-export-contract.md)), with explicit lag vs fleet.

## Consequences

- Split **storage**: device TS vs user profile DB is a hard boundary in backend design.  
- Marketing and legal copy must not promise “we never collect location” if telemetry is effectively location-revealing; consent flows are a product+legal task.

## Alternatives considered

- **Ship full `latest.json` to the app for transparency** — Rejected per ADR-0001.  
- **Anonymize by stripping device_id in consumer** — Still need user↔device binding; anonymization is handled by **BFF** projections, not by making `device_id` public to the app client.
