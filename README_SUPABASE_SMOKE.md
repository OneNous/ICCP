# Pi / firmware → Supabase (bench smoke)

Bench validation uses **PostgREST** with the **service role** key on the device only (see `.claude/backend.md` BE-11). Do not embed that key in mobile apps.

## Same contract as the smoke script

From the **monorepo root**, the shell script proves inserts work against hosted Supabase:

```bash
source .env   # SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
./scripts/smoke_service_role_reading.sh YOUR_SERIAL_HERE
```

Python firmware should mirror that flow:

1. **`POST /rest/v1/devices`** with `Prefer: resolution=merge-duplicates` and a row keyed by **`serial`** (≥ 8 chars), **`tech_id`** = commissioning technician’s `auth.uid()` as text when known.
2. **`POST /rest/v1/readings`** with `serial`, `observed_at`, polarization / channel columns (bench smoke and one-off tools — not the same path as background cloud sync).
3. **Background cloud sync** (`COILSHIELD_CLOUD_SYNC=1`): queued `latest.json` snapshots are inserted into **`public.telemetry_points`** by default (`COILSHIELD_CLOUD_TELEMETRY_TABLE`, default `telemetry_points`). Each row must include **`COILSHIELD_SERIAL`** (≥ 8 chars) on the Pi; the worker maps `ref_shift_mv` → `shift_mV`, `total_ma` → `total_mA`, `ts_unix` → `time`, and stores the full snapshot JSON in **`payload_json`**. **`devices` must already exist** for that `serial` (FK) — run step 1 once per device before expecting telemetry rows. Set `COILSHIELD_CLOUD_TELEMETRY_TABLE` to another table name only if you run a custom PostgREST schema that accepts raw JSON rows.
4. **Optional `readings` mirror** (`COILSHIELD_CLOUD_READINGS=1`, default `0`): after each successful `telemetry_points` batch, the worker inserts matching **`public.readings`** rows (`polarization_mv`, `channel_1_ma`…`channel_4_ma`, `observed_at`) derived from the same snapshot so Command Center / SQL queries on `readings` stay warm. A failure on `readings` does not re-queue telemetry (see `device-firmware/src/cloud_worker.py`).

Use **`https://<project-ref>.supabase.co`** and headers:

- `apikey: <service_role>`
- `Authorization: Bearer <service_role>`
- `Content-Type: application/json`

The stub MQTT path under `pi-ble/` does not yet push to Supabase. For **HTTP → PostgREST** from the Pi venv, use:

```bash
cd device-firmware/pi-ble
export SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… COILSHIELD_SERIAL=SMOKE00000001
python3 supabase_rest.py smoke
```

That upserts `devices`, inserts one `readings` row, and one `events` row (same contract as `scripts/smoke_service_role_reading.sh`).
