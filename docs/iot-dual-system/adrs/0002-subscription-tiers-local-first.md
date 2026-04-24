# ADR-0002: Subscription tiers and local-first control

## Status

Accepted (planning / architecture).

## Context

Commercialization may use **tiers** (e.g. Basic, Standard, Premium) that gate cloud features, mobile apps, or support SLAs. The [roadmap-fleet-telemetry.md](../roadmap-fleet-telemetry.md) line **“ICCP binary should stay runnable with no network for Basic”** is a product **invariant** for safety and field reliability: cathodic protection must not depend on Wi-Fi, MQTT, or a vendor API to **run**.

## Decision

1. **Basic tier (or equivalent “offline product”)**  
   - The device must operate **air-gapped**: `iccp start` runs, PWM/I2C loop, logging to local SQLite + `latest.json`, TUI and local dashboard, as today.  
   - No **hard** runtime dependency on cloud for control correctness.

2. **Cloud-enabled tiers**  
   - Add a **separate** optional process (uplink **agent**; see [agent-process-model.md](../agent-process-model.md)) that can be disabled, missing, or failing without taking down the controller.  
   - Subscription state (what the customer paid for) is enforced in **cloud and/or agent policy**, not by blocking local operation when billing disagrees. *Product policy* may choose to only **enable** uplink software when licensed; that is still **orthogonal** to the main binary’s local operation.

3. **Business vs engineering**  
   - Tiering is a **business** and packaging concern; the firmware repo’s responsibility is: **separate process**, **no I/O coupling** to the hot loop, documented export contract (see [firmware-export-contract.md](../firmware-export-contract.md)).

## Consequences

- The controller process must **not** import cloud SDKs or run continuous HTTPS/MQTT in the same process as the inner loop.  
- Feature flags for uplink are carried by the **agent** and/or `systemd` unit enablement, not by blocking `iccp start` when a network is absent.

## Alternatives considered

- **Require cloud for “unlocked” features in the same process** — Rejected: conflates billing with real-time control and increases risk of field outages.  
- **Phone-home before PWM allowed** — Rejected for the same reason as a hard air-gap violation.
