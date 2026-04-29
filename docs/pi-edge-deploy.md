# Raspberry Pi edge: BLE provisioning, cloud register, MQTT

Pi-only components aligned with ICCP `latest.json`, `LOG_INTERVAL_S`, and `cli_events` JSONL. No mobile app in this repo.

Downstream **InfluxDB / Telegraf** subscribers can map `iccp.telemetry.v1` snapshots and `iccp.cli.event.v1` commission lines without touching the real-time PWM loop.

## Quick sanity check

```bash
iccp-edge-doctor              # human-readable report
iccp-edge-doctor --json        # machine-readable
iccp-edge-doctor --strict     # exit 1 if MQTT endpoint or TLS files are missing
```

## Optional install

```bash
pip install -e ".[ble,cloud]"
```

- **`[ble]`** — `dbus-python` + PyGObject for BlueZ GATT (Wi‑Fi provisioning).
- **`[cloud]`** — `paho-mqtt` + on Linux `inotify-simple` (telemetry directory watch).

## Bootstrap order (suggested)

1. **Directories (optional)** — `sudo bash deploy/bootstrap-iccp-edge.sh` creates `/etc/iccp`, `/etc/iccp/aws-iot`, and a commission spool dir hint.
2. **BLE → Wi‑Fi** — `iccp-ble-provision` (or systemd `iccp-ble-provision.service`) with flag file / `ICCP_BLE_PROVISIONING=1`.
3. **HTTPS register** — `iccp-cloud-register` (see `deploy/iccp-cloud-register.service`; optional auto-spawn after Wi‑Fi via `ICCP_REGISTER_AFTER_WIFI=1` on the BLE service).
4. **MQTT** — Copy TLS files per `deploy/aws-iot/README.md`. Endpoint from env **or** `mqtt_endpoint` / `mqtt_host` in `cloud.conf` (merged by default; disable with `ICCP_MERGE_CLOUD_CONF=0`). Then `iccp-commission-mqtt` / `iccp-telemetry-mqtt`.

### Register bearer token (not in logs)

- **`ICCP_CLOUD_REGISTER_TOKEN`** — inline secret (avoid in committed unit files).
- **`ICCP_CLOUD_REGISTER_TOKEN_FILE`** — path to a **0600** file readable by the register process (recommended on images).

systemd **LoadCredential=** can inject a file under `$CREDENTIALS_DIRECTORY/`; set `ICCP_CLOUD_REGISTER_TOKEN_FILE` in an **`ExecStart` wrapper** or **`EnvironmentFile`** generated at boot so the path matches where systemd mounted the credential.

## Wi‑Fi stack: `wpa_cli` vs NetworkManager

| Backend | Env | Notes |
|---------|-----|--------|
| **wpa_cli** (default) | `COILSHIELD_WIFI_BACKEND=wpa_cli` or unset | Classic Raspberry Pi OS / `wpasupplicant`. Removes saved networks when `replace_all_networks` (BLE path). |
| **nmcli** | `COILSHIELD_WIFI_BACKEND=nmcli` (aliases: `nm`, `networkmanager`) | Creates/replaces a saved connection named **`CoilShield-ICCP`**. Use one stack per image (plan risk register). |

`wpa_supplicant` **drop-ins** under `/etc/wpa_supplicant/conf.d` are outside this Python module; `wifi_wpa` uses the live daemon via `wpa_cli save_config`.

## Environment variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `ICCP_BLE_PROVISIONING` | `iccp-ble-provision` | Set `1` to allow start without a flag file (dev). |
| `ICCP_BLE_PROVISION_FLAG` | BLE | Path to enable file (default `/etc/iccp/ble_provision.enable`). |
| `ICCP_BLE_WINDOW_S` | BLE | Session length in seconds (default `600`; `0` = until killed). |
| `ICCP_BLE_LOCAL_NAME` | BLE | Advertised GAP local name. |
| `ICCP_BLE_STATUS_HEARTBEAT_S` | BLE | If `>0`, STATUS notify repeats at this interval while subscribed (optional heartbeat). |
| `ICCP_WIFI_IFACE` / `--iface` | BLE / Wi‑Fi | Interface (default `wlan0`). |
| `COILSHIELD_WIFI_BACKEND` / `ICCP_WIFI_BACKEND` | `wifi_wpa` | `wpa_cli` (default) or `nmcli`. |
| `ICCP_REGISTER_AFTER_WIFI` | BLE | If `1`, spawn `iccp-cloud-register` in background after Wi‑Fi success (requires PATH). |
| `ICCP_CLOUD_API_URL` | register | API base, e.g. `https://api.example.com` (POST `/devices/register`). |
| `ICCP_CLOUD_REGISTER_URL` | register | Full register URL override (skips path join). |
| `ICCP_CLOUD_REGISTER_METHOD` | register | `POST` (default) or `PUT` for idempotent register. |
| `ICCP_CLOUD_CONF` | register | Persisted JSON path (default `/etc/iccp/cloud.conf`, mode `0600`). |
| `ICCP_CLOUD_REGISTER_TOKEN` | register | Optional `Authorization: Bearer …` for bootstrap. |
| `ICCP_CLOUD_REGISTER_TOKEN_FILE` | register | Read bearer token from this file if `ICCP_CLOUD_REGISTER_TOKEN` is empty (use 0600, root-only). |
| `ICCP_IOT_ENDPOINT` | MQTT | Broker hostname (AWS IoT endpoint). |
| `ICCP_MQTT_HOST` | MQTT | Alias for `ICCP_IOT_ENDPOINT`. |
| `ICCP_MERGE_CLOUD_CONF` | MQTT | Default `1`: fill endpoint/port from `/etc/iccp/cloud.conf` when env unset. |
| `ICCP_CLOUD_CONF` | MQTT / register | Path to merged JSON (default `/etc/iccp/cloud.conf`). |
| `ICCP_IOT_PORT` | MQTT | TLS port (default `8883`). |
| `ICCP_IOT_CA_PATH` | MQTT | CA bundle (default `/etc/iccp/aws-iot/AmazonRootCA1.pem`). |
| `ICCP_IOT_CERT_PATH` | MQTT | Device certificate. |
| `ICCP_IOT_KEY_PATH` | MQTT | Private key. |
| `ICCP_MQTT_COMMISSION_TOPIC` | commission bridge | Override topic (default `iccp/<serial>/commission/jsonl`). Plan alternative: `devices/<serial>/commission/events`. |
| `ICCP_COMMISSION_MQTT_SPOOL` | commission bridge | If set, directory for `pending.jsonl` when publish fails; drained next connect. |
| `ICCP_MQTT_TELEMETRY_TOPIC` | telemetry sidecar | Override topic (default `iccp/<serial>/telemetry/latest`). |
| `COILSHIELD_LOG_DIR` / `ICCP_LOG_DIR` | telemetry | Directory containing `latest.json` (must match controller). |
| `ICCP_TELEMETRY_INTERVAL_S` | telemetry | Publish cadence seconds (default: `LOG_INTERVAL_S` from settings or `120`). |
| `ICCP_TELEMETRY_INOTIFY` | telemetry | Set `1` to use `inotify-simple` on the log directory (Linux; optional dep). |
| `ICCP_TELEMETRY_POLL_S` | telemetry | Override short poll slice when not using inotify (seconds). |
| `ICCP_COMMISSION_CMD` | commission bridge | Override full `iccp` argv prefix (space-separated). |

Schemas:

- BLE status notify: `iccp.ble.status.v1` (`state`, `uptime_s`, `version`, `ip`, `last_error`).
- Commission MQTT: each line is `iccp.cli.event.v1` (same as `ICCP_OUTPUT=jsonl`).
- Telemetry MQTT envelope: `iccp.telemetry.v1` with `snapshot` (curated `latest.json` keys for ref, channels, diag, commissioning flags).

## systemd examples

| File | Role |
|------|------|
| `deploy/iccp-ble-provision.service` | BLE + `wpa_cli` / env backend (often **root** for Wi‑Fi control). |
| `deploy/iccp-telemetry-mqtt.service` | Sidecar; match `User` + `COILSHIELD_LOG_DIR` with `iccp`. |
| `deploy/iccp-commission-mqtt.service` | Oneshot example wrapping `iccp commission …`. |

**BLE + controller:** Coexistence is usually fine on Pi 3B+/4/5 (BLE + Wi‑Fi). Do not run two custom GATT servers on one adapter.

## Acceptance checklist (Pi image)

1. **BLE:** With no Wi‑Fi, create `/etc/iccp/ble_provision.enable`, start `iccp-ble-provision`, connect with nRF Connect / similar, write SSID + password (encrypted writes), observe STATUS notify → `wifi_ok` and IP; optional heartbeat if `ICCP_BLE_STATUS_HEARTBEAT_S` set; remove flag file after success.
2. **Register:** Set `ICCP_CLOUD_API_URL`, run `iccp-cloud-register`; verify `/etc/iccp/cloud.conf` exists with mode `0600` and survives reboot; test idempotent `409` + JSON if your API uses that pattern.
3. **Commission MQTT:** With certs and endpoint set, run  
   `iccp-commission-mqtt -- commission --help` (or real args); broker receives JSON lines. With `ICCP_COMMISSION_MQTT_SPOOL`, disconnect broker mid-run and confirm lines land in `pending.jsonl`, then drain on retry.
4. **Telemetry MQTT:** Run controller + `iccp-telemetry-mqtt`; consumer receives messages at ~`ICCP_TELEMETRY_INTERVAL_S` with stable `iccp.telemetry.v1` JSON. Enable `ICCP_TELEMETRY_INOTIFY=1` on Linux after `pip install inotify-simple`.

## Security notes

- BLE provisioning is a credential surface: short window, flag file, LE encrypted writes (`encrypt-write`), disable service when done.
- Never log Wi‑Fi passwords; `wifi_wpa` and BLE paths avoid logging credentials.
- Store device certs and `cloud.conf` root-owned `0600` under `/etc/iccp/`.
- Prefer **systemd `Credential=`** or **Age**-encrypted secrets for production images (not wired in this repo).
