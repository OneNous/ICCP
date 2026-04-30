# ADR-0001: Dual product surfaces (fleet vs consumer)

## Status

Accepted (planning / architecture).

## Context

CoilShield’s controller is **local-first** on a Raspberry Pi: [`logger.py`](../src/../logger.py) writes SQLite and `latest.json` every control tick. A future product line needs **remote visibility** for two different audiences: **operators** (OEM, installers, support) and **homeowners or facility users** (non-engineering).

## Decision

We define **two separate product surfaces** backed by one physical device:

1. **Fleet / operator system**  
   - Ingests **engineering-oriented** telemetry (full-rate or **policy-defined** downsamples).  
   - Supports long retention, install validation, warranty analytics, and impedance trending.  
   - Access is **role-scoped** (OEM, distributor, L2 support); not a general public API for raw per-channel data.

2. **Consumer BFF and app**  
   - Serves **aggregated** state only: e.g. protected / not, uptime, alert summaries, “contact service” CTAs.  
   - Does **not** embed or expose the full per-channel time series to the mobile/web client.  
   - Receives **derived** facts from the fleet side (or from pre-aggregated materialized events), with a **minimal** API contract.

**Separation rule:** the homeowner app **never** talks to the same database or MQTT topics as the fleet “full detail” path without a **BFF** that enforces the aggregation boundary.

## Consequences

- The device-side **uplink path** (see [uplink-v1.md](../uplink-v1.md)) may still send a **single** rich schema to a **fleet** ingest point; the **consumer** surface is implemented by **downstream** systems (BFF, rollups), not by trusting the end-user app with the fleet payload.
- Two distinct **authorization** models: fleet (staff, multi-tenant) vs consumer (end-user, device-bound, minimal fields).

## Alternatives considered

- **Single monolithic “device API” for everyone** — Rejected: impossible to keep engineering detail out of the consumer client without a separate projection layer, and support tooling needs a richer model than a homeowner should see.
- **Homeowner app reads device over LAN only** — Valid for a subset of SKUs, but not a replacement for a cloud BFF for remote status; may coexist as a future add-on, not a substitute for ADR-0001.
