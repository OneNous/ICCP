# ADR-0003: Device identity, bootstrap, and trust

## Status

Accepted (planning / architecture). Detailed certificate formats and HSM use are TBD in the fleet implementation repos.

## Context

Fleet and consumer services must identify **which physical device** they are talking to, authenticate it, and support **revocation** and **rotation** without re-flashing a full user story on every key change. The on-device **controller** is intentionally simple and must not own complex TLS identity logic in-process.

## Decision

1. **Device identity (logical)**  
   - A stable **device_id** in a namespace owned by the OEM (e.g. UUID, or `COIL_{serial}`), **provisioned** at manufacturing or first commissioning, stored in a protected store on the Pi (path TBD; not the same as user PII in ADR-0004).

2. **Cryptographic trust**  
   - The **uplink agent** (not the ICCP loop) holds or loads credentials used to **authenticate to fleet ingest** (e.g. X.509 client cert, or JWT obtained via a bootstrap secret).  
   - **Short-lived tokens** at the application layer are preferred for HTTPS API patterns; **mutual TLS** is preferred for high-volume MQTT in regulated deployments. Exact choice is a fleet infrastructure decision, but the **split** “agent holds keys, controller does not” is fixed.

3. **Bootstrap modes** (choose one or support multiple SKUs)  
   - **Claim code:** one-time user-entered or installer-entered string ties `device_id` to a **tenant** in fleet after install.  
   - **Factory provisioning:** key material and `device_id` preloaded for OEM B2B rolls.  
   - **RMA / replacement:** old device revoked; new device gets new `device_id` or a **rebind** event with auditable log.

4. **Rotation and revoke**  
   - Fleet must support **credential rotation** without bricking the device: overlap window for two valid certs, or re-fetch of a **device token** using a long-lived second factor stored on device.  
   - **Revoke** must be immediate for stolen devices: ingest rejects identity; agent surfaces a **durable** local “auth failed / contact support” state (does not stop ICCP — ADR-0002).

5. **Consumer BFF**  
   - **End users** do not authenticate “as the device.” They use normal user accounts; the BFF maps **user ↔ device** with your account database. That mapping is out of band from device TLS identity.

## Consequences

- A **single** shared secret baked into the firmware for all devices is **not** acceptable for production; any factory secret must be **per-device** or per-batch with secure injection.  
- OTA and agent updates may share an update channel but **identity** policy remains in fleet documentation.

## Alternatives considered

- **Symmetric key only, same for all devices** — Rejected for fleet-scale security.  
- **IP-based trust** — Rejected; residential NAT breaks this.
