# Cloud Sync (Supabase)

> **Scope:** This file covers `cloud_sync.py` and any code that talks to Supabase from the firmware. Read alongside `.claude/schemas.md` for what the data shapes look like.

## What Cloud Sync Does

The firmware pushes data to a Supabase Postgres database hosted in the cloud. Three categories of pushes:

1. **Readings** — high-volume time-series data. Polarization, channel currents, temperature, state. Pushed every reading cycle (every second or so).
2. **Events** — lower-volume, higher-importance. State transitions, faults, commissioning completions, wet event start/end.
3. **Commissioning runs** — discrete records when commissioning happens. Includes target/achieved polarization, success flag, log of attempts.

## Rule CS-1: Sync Is Best-Effort, Control Is Required

The control loop must keep running even if cloud sync fails completely. Network out for an hour? Control loop runs. Supabase down for a day? Control loop runs. SD card full of unsent data? Control loop runs.

**Cloud sync errors must NEVER cause control loop crashes or delays.**

The implementation pattern:

- Cloud sync runs in a separate thread (or async task) from the control loop
- Communication between them is via a queue: control loop produces, sync consumer
- If the queue fills up (sync is too slow), oldest items are dropped, NOT newest
- Sync errors are logged but never raised back to the control loop

## Rule CS-2: Pending Uploads Persist to Disk

When a push fails, the data goes into a local SQLite table `pending_uploads`. Schema:

```sql
CREATE TABLE pending_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT NOT NULL,    -- 'readings', 'events', or 'commissioning_runs'
    payload TEXT NOT NULL,         -- JSON-encoded row
    queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT
);
```

A background task runs every 60 seconds:

1. Selects up to 50 oldest pending uploads.
2. Posts them in batches to Supabase.
3. On success, deletes from pending_uploads.
4. On failure, increments retry_count and records the error.

## Rule CS-3: Cap the Pending Queue at 30 Days

If pending_uploads grows beyond 30 days of data, drop oldest first. The Pi's SD card is finite (32 GB typical) and a runaway queue can fill it up.

```sql
-- Run daily
DELETE FROM pending_uploads
WHERE queued_at < datetime('now', '-30 days');
```

If we lose 30 days of data because of a 30-day Supabase outage, that's a worse problem than data loss. The owner gets alerted via a separate monitoring path that the cloud is down.

## Rule CS-4: Use the Service Role Key

Firmware authenticates to Supabase using the service role key (full backend access). This bypasses Row Level Security.

**The service key lives in `/var/lib/coilshield/config.json`** with mode 600 (owner-only read/write). It's NEVER:

- Logged
- Sent over BLE
- Exposed in the local Flask dashboard
- Returned by any HTTP endpoint
- Hardcoded in source

If the service key leaks, the Supabase project is compromised. Treat it accordingly.

## Rule CS-5: Batching for Efficiency

Don't make one HTTP call per reading. Supabase REST API supports bulk inserts:

```python
# Good
response = supabase.table('readings').insert([
    {'device_id': '...', 'channel': 0, 'current_ma': 0.5, ...},
    {'device_id': '...', 'channel': 1, 'current_ma': 0.6, ...},
    # ... up to 50 rows
]).execute()
```

Batch size: 50 rows per request. Larger batches risk hitting Supabase's request size limits; smaller batches waste round trips.

## Rule CS-6: Time Stamps Are UTC From the Pi's Clock

The Pi's clock is set via NTP at boot. Timestamps in readings/events use Python's `datetime.now(timezone.utc)`.

If the Pi's clock is wrong (NTP failed), timestamps will be wrong. The first cloud sync after boot includes a "wall clock check" — Supabase's server clock vs the Pi's clock. If they disagree by >5 minutes, log a warning and adjust.

## Rule CS-7: Don't Mix Sync and Realtime

The firmware does **not** subscribe to Supabase Realtime. Realtime is for the command center to receive live updates. The firmware doesn't need to be told things by the cloud — it just pushes data.

If you find yourself wanting to use Realtime in firmware (e.g., to receive a "clear fault" command from the command center), that's the wrong design. Use the local HTTP API (see `.claude/tech-api.md`) for commands. Cloud → device commands are out of scope for validation.

## Rule CS-8: HTTPS Only, No Self-Signed Certificates

All Supabase traffic is HTTPS. Use the `supabase-py` library or `requests` with default certificate validation. Don't disable SSL verification "for testing" — that habit ports to production.

## Rule CS-9: Retry With Exponential Backoff

Network is flaky. Retry with backoff:

```python
def retry_with_backoff(func, max_attempts=5):
    delay = 1.0
    for attempt in range(max_attempts):
        try:
            return func()
        except (ConnectionError, Timeout) as e:
            if attempt == max_attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2  # 1s, 2s, 4s, 8s, 16s
```

Don't retry forever. After max attempts, the row goes into pending_uploads for the background task to handle.

Don't retry on non-network errors (4xx responses from Supabase). A 401 means auth is wrong; retrying won't fix it. A 422 means the row is malformed; retrying won't fix it.

## Rule CS-10: Health Check Endpoint

Implement a simple health check that the device runs every 5 minutes:

```python
def cloud_health_check():
    try:
        # Cheap read query
        response = supabase.table('devices').select('id').eq('id', DEVICE_ID).execute()
        return response.data is not None
    except Exception:
        return False
```

If the health check fails for >10 minutes, log a critical event. Owner gets alerted (eventually — the alert path itself depends on cloud sync working, so if cloud is down we can't alert via cloud; the alert is "cloud sync degraded for >10 min" once cloud comes back).

## Configuration

Cloud sync settings live in `config/settings.py`:

```python
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')

CLOUD_SYNC_ENABLED = True
CLOUD_BATCH_SIZE = 50
CLOUD_SYNC_INTERVAL_S = 60
CLOUD_HEALTH_CHECK_INTERVAL_S = 300
CLOUD_PENDING_QUEUE_MAX_DAYS = 30
CLOUD_REQUEST_TIMEOUT_S = 30
```

`SUPABASE_URL` and `SUPABASE_SERVICE_KEY` come from environment variables, set by systemd unit file from `/etc/coilshield/env`.

## Two Supabase Projects

The monorepo defines two projects (dev and prod). Firmware uses one or the other based on environment:

- Development units (your bench): `SUPABASE_URL` points at dev project
- Validation units (the 10 in the field): `SUPABASE_URL` points at prod project

Don't mix them up. A bench device pushing to prod pollutes the dataset. A validation device pushing to dev means the owner's command center misses real data.

The `device_id` in Supabase references which project the device belongs to. If you accidentally point a bench device at prod, delete the spurious data afterward.

## Common Cursor Pitfalls in Cloud Sync

- Suggesting `aiohttp` for async sync (the existing thread-based design is fine and simpler)
- Not handling the case where Supabase returns 200 OK but with empty data (rare but possible)
- Forgetting to set the timeout — default is None which means wait forever
- Using Supabase's Python client when `requests` is sufficient (the client adds dependencies for features we don't use)
- Batching reads (we don't need bulk reads — each device only reads its own state, single-row queries are fine)
- Treating 500 errors as permanent (they're often transient; retry with backoff)

## Smoke Test for Cloud Sync

Before declaring cloud sync "validation-ready":

1. Device with valid Supabase credentials successfully pushes a reading row
2. Reading row visible in Supabase dashboard within 5 seconds
3. Disconnect WiFi, generate 100 readings, reconnect — all 100 reach Supabase
4. Disconnect WiFi for 24 hours, generate readings, reconnect — readings reach Supabase, none lost
5. Try to push to Supabase with wrong credentials — error logged, no crash
6. Try to push malformed row — error logged, no crash, row not requeued
7. Delete pending_uploads row after >30 days — old rows are dropped, recent ones preserved
8. Health check correctly detects when Supabase is unreachable
9. Pending queue never grows beyond ~30 days × ~5760 readings/day = ~170k rows
10. Service key is never logged anywhere

If any step fails, cloud sync is not validation-ready.
