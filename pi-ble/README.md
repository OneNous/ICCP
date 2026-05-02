# Raspberry Pi BLE provisioning

Runs `ble_provision.py` on Raspberry Pi OS with BlueZ. Requires **Linux** (BlueZ D-Bus); macOS cannot host this peripheral.

## Install

```bash
sudo apt install python3-pip bluez
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
export COILSHIELD_SERIAL=CS-A4-7F-2C-91
sudo .venv/bin/python3 ble_provision.py --v
```

`nmcli` is preferred for WiFi joins. Without NetworkManager, the script appends a `network={}` block to `WPA_SUPPLICANT_CONF` (default `/etc/wpa_supplicant/wpa_supplicant.conf`) and runs `wpa_cli reconfigure` when available.

## Rollback / bad credentials

- If **nmcli** fails, the script reports `status=error` (code `3`) over BLE; the previous WiFi profile is unchanged unless `nmcli` created a partial profile named `coilshield-ble` (remove with `nmcli connection delete coilshield-ble` if needed).
- If **`wpa_supplicant` append** was used and the Pi no longer joins any network, boot with Ethernet or edit the SD card: remove the trailing `# coilshield-ble-provision` block from `wpa_supplicant.conf`, or restore a file backup taken before provisioning.

## Environment

| Variable | Meaning |
|----------|---------|
| `COILSHIELD_SERIAL` | Serial returned by `device_info` read |
| `WPA_SUPPLICANT_CONF` | Path for fallback WPA config |
| `WPA_IFACE` | Interface for `wpa_cli` (default `wlan0`) |

GATT UUIDs and payloads: [packages/api-contract/ble-protocol.md](../../packages/api-contract/ble-protocol.md).

## Supabase bench (HTTP)

```bash
export SUPABASE_URL=https://<ref>.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=…   # device / bench only — never ship in mobile apps
export COILSHIELD_SERIAL=SMOKE00000001
python3 supabase_rest.py smoke
```

See [../README_SUPABASE_SMOKE.md](../README_SUPABASE_SMOKE.md).

## MQTT helpers (Weeks 4–5)

- `mqtt_commissioning.py` — progress JSON to `devices/{serial}/commissioning` (`--demo` for a canned sequence).
- `mqtt_telemetry.py` — reads `LATEST_JSON` (default `/var/lib/coils/latest.json`) every `LOG_INTERVAL` seconds to `devices/{serial}/telemetry`.

See [docs/iot-setup.md](../../docs/iot-setup.md) and [docs/influx-telemetry.md](../../docs/influx-telemetry.md).

## systemd (optional)

After creating the venv and installing deps:

```bash
export COILSHIELD_SERIAL=CS-A4-7F-2C-91
export VENV_PY="$PWD/.venv/bin/python3"
sudo bash scripts/install-systemd.sh
sudo systemctl enable --now coilshield-ble-provision.service
# When cloud MQTT is reachable:
sudo systemctl enable --now coilshield-mqtt-commissioning.service
sudo systemctl enable --now coilshield-mqtt-telemetry.service
```

Edit the generated unit files if your install path or Python differ. BLE provisioning typically needs `CAP_NET_ADMIN` / running as root for `nmcli` on some images; adjust `User=` only if your policy allows unprivileged WiFi joins.
