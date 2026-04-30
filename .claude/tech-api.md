# Tech App HTTP API (Local)

> **Scope:** This file covers `tech_api.py` — the Flask routes the tech app calls after the device is on WiFi. After BLE provisioning is complete, all communication moves to local HTTP on the customer's WiFi.

## Why Local HTTP and Not BLE?

BLE is great for initial provisioning (when there's no WiFi yet) but bad for ongoing communication:

- BLE has tiny throughput (a few KB/sec)
- BLE requires the phone to be physically near the device
- BLE state machine is fragile under poor conditions

Once on WiFi, switch to HTTP. The tech app already has WiFi access (it's how the tech is on the customer's network), and HTTP is fast, simple, and well-understood.

## Stack

- **Server:** Flask (the existing dashboard already uses it)
- **Discovery:** mDNS via Avahi (already enabled on Pi OS Lite)
- **Auth:** HMAC-SHA256 signature using the bond key established during BLE pairing
- **Transport:** HTTP (NOT HTTPS — see Rule TA-2)

## Endpoints

```
GET  /info              -> {serial, firmware_version, hardware_revision, uptime_seconds}
GET  /status            -> live readings (polarization, channel currents, state)
POST /commission        -> start commissioning sequence
GET  /commission/status -> poll commissioning progress
POST /clear-fault       -> clear a non-latched fault on a specific channel
GET  /events            -> recent events (faults, state transitions) — last 100
```

All endpoints require HMAC authentication except `/info` (which is intentionally unauthenticated for tech app discovery confirmation).

## Rule TA-1: mDNS Service Name

The device advertises itself via Avahi as:

```
coilshield-{last_4_of_serial}.local
```

Service type: `_coilshield._tcp` on port 8080.

Configure Avahi via `/etc/avahi/services/coilshield.service`:

```xml
<?xml version="1.0" standalone='no'?>
<service-group>
  <name replace-wildcards="yes">CoilShield-%h</name>
  <service>
    <type>_coilshield._tcp</type>
    <port>8080</port>
    <txt-record>serial=CS-2026-00001</txt-record>
  </service>
</service-group>
```

The tech app browses for `_coilshield._tcp` on the local network and shows the discovered devices.

## Rule TA-2: HTTP, Not HTTPS

Yes, HTTP. Here's why:

- We're on a private home WiFi, not the public internet.
- Generating per-device TLS certificates is operational complexity we don't need.
- Self-signed certs trigger trust prompts in the tech app, hurting UX.
- HMAC authentication on each request prevents replay/forge attacks.
- Sensitive data (the bond key, WiFi credentials) is never sent over this channel — they were already exchanged via BLE.

If we ever need TLS (for some compliance reason), use Let's Encrypt with mDNS-based domain names (.local resolution + ACME). But during validation, plain HTTP is correct.

## Rule TA-3: HMAC Authentication

Every authenticated request includes:

```
X-CoilShield-Signature: <HMAC-SHA256 of request body using bond_key>
X-CoilShield-Tech-ID: <the tech app's install_id>
X-CoilShield-Timestamp: <unix epoch seconds>
```

Server-side verification:

```python
def verify_hmac(request):
    tech_id = request.headers.get('X-CoilShield-Tech-ID')
    signature = request.headers.get('X-CoilShield-Signature')
    timestamp = int(request.headers.get('X-CoilShield-Timestamp'))
    
    # Reject requests older than 5 minutes (replay protection)
    if abs(time.time() - timestamp) > 300:
        abort(401)
    
    bond = get_bonded_device(tech_id)
    if not bond:
        abort(401)
    
    expected = hmac.new(
        bond.key.encode(),
        f"{timestamp}\n{request.get_data(as_text=True)}".encode(),
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected):
        abort(401)
```

## Rule TA-4: Bind to localhost + WiFi Interface Only

Don't bind Flask to 0.0.0.0. Bind to:

- `127.0.0.1` (localhost — for the dashboard, accessible only on the Pi itself)
- The WiFi interface IP (for tech app access from the LAN)

Don't expose on the Bluetooth interface. Don't expose on the Ethernet interface (if a Pi 4 with Ethernet is used) unless explicitly configured.

## Rule TA-5: Rate Limiting

Per-tech-id rate limit: 60 requests per minute. Use `flask-limiter`:

```python
from flask_limiter import Limiter
limiter = Limiter(
    key_func=lambda: request.headers.get('X-CoilShield-Tech-ID', 'anonymous'),
    default_limits=["60 per minute"]
)
```

This prevents a buggy tech app from hammering the device. The actual tech app should poll status no faster than once per second, well under the limit.

## Rule TA-6: Endpoint Specifications

### GET /info (unauthenticated)

Used by the tech app to verify it found the right device after mDNS discovery.

```json
{
  "serial": "CS-2026-00001",
  "firmware_version": "0.3.1",
  "hardware_revision": "B",
  "uptime_seconds": 3600,
  "ble_protocol_version": "1.0.0"
}
```

### GET /status (authenticated)

Live state of the device. Tech app polls this once per second during commissioning.

```json
{
  "device_id": "uuid",
  "timestamp": "2026-04-29T14:30:00Z",
  "channels": [
    {
      "channel": 0,
      "state": "PROTECTING",
      "current_ma": 0.5,
      "duty_percent": 8.4,
      "fault": null
    },
    ...
  ],
  "polarization_mv": -1019,
  "polarization_in_window": true,
  "temperature_c": 18.5,
  "wifi_rssi": -52,
  "cloud_sync_status": "ok"
}
```

### POST /commission (authenticated)

Starts the commissioning sequence. Returns immediately; sequence runs in background.

Request body:

```json
{
  "coil_metal": "aluminum",
  "coil_size_estimate_sqft": 20,
  "installer_id": "uuid"
}
```

Response: 202 Accepted, with the commissioning_run_id.

### GET /commission/status (authenticated)

Poll for commissioning progress.

```json
{
  "commissioning_run_id": "uuid",
  "started_at": "2026-04-29T14:30:00Z",
  "state": "ramping_current",  // "starting", "ramping_current", "achieving_target", "stabilizing", "complete", "failed"
  "elapsed_seconds": 45,
  "current_polarization_mv": -985,
  "target_polarization_mv": -1019,
  "log": [
    {"timestamp": "...", "message": "Starting commissioning"},
    {"timestamp": "...", "message": "Channel 0 wet, current 0.3 mA"},
    ...
  ]
}
```

### POST /clear-fault (authenticated, owner-only)

Clear a fault on a specific channel. Only the owner can do this — tech-role apps cannot.

Request body: `{"channel": 0}`

Response: 200 OK if cleared, 403 if not owner, 409 if fault is latched (cutoff-triggered).

The owner role is determined by the tech app's auth role — the request includes a Supabase JWT that identifies the user. Yes, this means the tech app needs to know if the current user is owner-role; that's a feature flag in the app.

### GET /events (authenticated)

Recent device events. Last 100.

```json
{
  "events": [
    {
      "timestamp": "...",
      "type": "wet_start",
      "channel": 0,
      "details": {}
    },
    {
      "timestamp": "...",
      "type": "fault",
      "channel": 1,
      "details": {"reason": "OVERCURRENT", "auto_recoverable": true}
    },
    ...
  ]
}
```

## Rule TA-7: Don't Expose Internal State

Don't add endpoints that expose:

- The Supabase service key (NEVER over HTTP)
- The bond key (NEVER, anywhere)
- WiFi credentials (NEVER)
- Raw I2C bus data (debugging only, behind a separate dev-only endpoint if needed)

The tech API is a controlled surface. It exposes what the tech app needs and nothing more.

## Rule TA-8: Log Every Authenticated Request

Every authenticated request gets logged with:

- Timestamp
- Tech ID
- Endpoint
- Response code
- Latency

This is the audit trail for what the tech app did to the device. Logs go to `tech_api.log` and (if cloud sync is up) to Supabase events table.

Don't log request bodies (might contain passwords during BLE-relay scenarios that we explicitly don't support, but defense in depth).

## Rule TA-9: Concurrent Request Handling

Flask's default is single-threaded. For this device, that's fine — the tech app only makes one or two requests per second, and operations are fast.

If you need concurrency for some reason, use `gunicorn` with sync workers:

```bash
gunicorn -w 2 --bind 0.0.0.0:8080 'tech_api:app'
```

Two workers is plenty. Don't run more — memory is constrained on Pi 3.

## Rule TA-10: API Versioning

The API version is in the URL path: `/v1/info`, `/v1/status`, etc. The current version is v1. Don't break v1 once devices are in the field.

If a breaking change is needed, add v2 and keep v1 working. The tech app reads the firmware's `firmware_version` field and chooses which version of the API to call.

For validation, all 10 devices and the tech app are at v1.

## Common Cursor Pitfalls in Tech API Code

- Suggesting FastAPI to replace Flask (Flask is already in use, don't switch)
- Forgetting to validate JSON request bodies (Flask doesn't do this for you)
- Not handling concurrent commissioning requests (only one commissioning can run at a time)
- Returning sensitive data in error messages (e.g., logging an exception that includes the bond key)
- Using `flask.send_file` for status endpoints (return JSON, not files)
- Building a generic "execute any command" endpoint — never do this

## Smoke Test for Tech API

Before declaring tech API "validation-ready":

1. Device on WiFi advertises via mDNS
2. Tech app discovers the device via `_coilshield._tcp`
3. GET /info returns device serial and firmware version (unauthenticated)
4. GET /status with valid HMAC returns live readings
5. GET /status with invalid HMAC returns 401
6. GET /status with stale timestamp (>5 min old) returns 401
7. POST /commission triggers the sequence; subsequent GETs show progress
8. POST /clear-fault as owner clears a fault; same request as tech returns 403
9. POST /clear-fault on a latched cutoff fault returns 409
10. Rate limit: 61 requests in a minute returns 429 on the 61st

If any step fails, tech API is not validation-ready.
