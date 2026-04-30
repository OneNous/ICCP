# BLE Provisioning (Firmware Side)

> **Scope:** This file covers `ble_provisioning.py` — the GATT peripheral implementation. The protocol contract is defined in the monorepo's `.claude/ble-provisioning.md`. This file is the firmware-side implementation guide.

## Why BLE Matters

Without BLE provisioning, every install requires SSHing into the Pi to edit `wpa_supplicant.conf`. The owner correctly rejected hardcoding WiFi credentials — the install experience is what the validation phase is testing, and SSH is not part of that experience.

The flow:

1. Device powers on with no WiFi configured
2. Device starts BLE advertising the CoilShield Provisioning Service
3. Tech app discovers it via service UUID
4. Tech app pairs with the device
5. Tech app reads available WiFi networks (firmware scans on demand)
6. Tech app writes the chosen SSID + password
7. Firmware writes to `wpa_supplicant.conf`, restarts wpa_supplicant
8. Firmware reports back: connecting → connected → online_to_cloud
9. Firmware stops BLE advertising once online_to_cloud is achieved

## Stack

- **Library:** `bless` — built on top of `bleak`, adds peripheral/server capabilities. Bare `bleak` is a client library and won't work.
- **Underlying:** BlueZ via D-Bus on Linux.
- **Pi Bluetooth:** built-in (Pi 3B+ and later have integrated BLE).

Don't use `python-bluetooth-mesh`, `pybluez`, or other libraries — they're either deprecated or unsuitable.

## Protocol UUIDs

These come from the monorepo's `.claude/ble-provisioning.md`. They're frozen — don't change. If the monorepo changes them, update both simultaneously and bump the protocol version.

```python
# In src/ble_provisioning.py
SERVICE_UUID                    = '8A3F4B12-8C5D-4E2A-9B1E-7F3D5A8C6B41'
DEVICE_INFO_CHAR_UUID           = '8A3F4B12-8C5D-4E2A-9B1E-7F3D5A8C6B42'
WIFI_SCAN_CHAR_UUID             = '8A3F4B12-8C5D-4E2A-9B1E-7F3D5A8C6B43'
WIFI_PROVISION_CHAR_UUID        = '8A3F4B12-8C5D-4E2A-9B1E-7F3D5A8C6B44'
PROVISIONING_STATUS_CHAR_UUID   = '8A3F4B12-8C5D-4E2A-9B1E-7F3D5A8C6B45'
```

(Real UUIDs need to be generated via `uuidgen` and frozen — these are placeholders. The monorepo and firmware must agree.)

## Rule BLE-FW-1: Advertise Only When Unprovisioned

Don't advertise BLE forever. The provisioning service is only useful before the device is online.

State machine:

- Boot → check if WiFi credentials exist in config
  - No credentials → start BLE advertising
  - Yes credentials → try to connect; if successful, stay quiet on BLE
- Lost WiFi for >5 minutes while previously connected → re-start BLE advertising for recovery
- Successful provisioning + online_to_cloud → stop BLE advertising

The reason: BLE advertising is a security surface. Random nearby phones shouldn't see the device. Once provisioned, hide.

## Rule BLE-FW-2: Pairing Required for Sensitive Writes

The WIFI_PROVISION characteristic requires authenticated writes. `bless` configures this when defining the characteristic:

```python
char = BlessGATTCharacteristic(
    uuid=WIFI_PROVISION_CHAR_UUID,
    permissions=GATTCharacteristicPermissions.write_authenticated,
    flags=GATTCharacteristicProperties.write,
    value=None
)
```

This means the writing peer must have completed BLE pairing/bonding first. iOS handles this automatically; Android requires `device.createBond()` from the tech app side.

The DEVICE_INFO and WIFI_SCAN characteristics can be unauthenticated reads — there's no sensitive data exposed.

## Rule BLE-FW-3: Bond Storage

Bonded device keys are stored by BlueZ in `/var/lib/bluetooth/<adapter_mac>/<peer_mac>/info`. We don't manage this directly — BlueZ does.

For the firmware to know which devices have bonded with it, maintain a separate file:

```
/var/lib/coilshield/bonded_devices.json
```

```json
{
  "bonded": [
    {
      "address": "AA:BB:CC:DD:EE:FF",
      "tech_app_install_id": "uuid-from-app",
      "first_paired_at": "2026-04-29T10:30:00Z",
      "last_seen_at": "2026-04-29T10:35:00Z"
    }
  ]
}
```

This is for our own auditing and for HMAC authentication when the tech app talks to the local HTTP API later. Don't put this in BlueZ's storage area.

## Rule BLE-FW-4: WiFi Scan Implementation

When the tech app reads the WIFI_SCAN characteristic, the firmware needs to scan for available networks. This is done via `iw`:

```python
def scan_wifi():
    result = subprocess.run(
        ['sudo', 'iw', 'wlan0', 'scan'],
        capture_output=True, text=True, timeout=10
    )
    networks = parse_iw_output(result.stdout)
    return [
        {
            'ssid': n['ssid'],
            'rssi': n['signal'],
            'security': n['security']  # 'OPEN', 'WPA2', 'WPA3', etc.
        }
        for n in networks
        if n['ssid']  # filter out hidden networks
    ]
```

`iw scan` requires sudo. Configure passwordless sudo for this specific command in `/etc/sudoers.d/coilshield`:

```
onenous ALL=(ALL) NOPASSWD: /usr/sbin/iw wlan0 scan
```

Cache the scan result for 30 seconds — the tech app may read this characteristic multiple times during provisioning, and rescanning every read wastes time.

## Rule BLE-FW-5: WiFi Credential Persistence

When credentials are written via BLE, they go into `/etc/wpa_supplicant/wpa_supplicant.conf`:

```python
def add_wifi_network(ssid, password):
    network_block = f"""
network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
"""
    # Append to wpa_supplicant.conf
    with subprocess.Popen(
        ['sudo', 'tee', '-a', '/etc/wpa_supplicant/wpa_supplicant.conf'],
        stdin=subprocess.PIPE, text=True
    ) as proc:
        proc.communicate(input=network_block)
    
    # Restart wpa_supplicant to pick up the change
    subprocess.run(['sudo', 'systemctl', 'restart', 'wpa_supplicant'])
    
    # Wait for IP
    return wait_for_dhcp(timeout_seconds=30)
```

The sudo permissions needed:

```
onenous ALL=(ALL) NOPASSWD: /usr/bin/tee -a /etc/wpa_supplicant/wpa_supplicant.conf
onenous ALL=(ALL) NOPASSWD: /bin/systemctl restart wpa_supplicant
```

Don't try to use NetworkManager — Pi OS Lite uses dhcpcd + wpa_supplicant by default. Don't switch.

## Rule BLE-FW-6: Status Reporting via Notifications

The PROVISIONING_STATUS characteristic uses BLE notifications to push state changes to the tech app:

```python
async def update_status(status, **kwargs):
    payload = {'state': status, **kwargs}
    await server.update_value(
        SERVICE_UUID,
        PROVISIONING_STATUS_CHAR_UUID,
        json.dumps(payload).encode('utf-8')
    )
```

States the tech app expects:

- `idle` — waiting for provisioning request
- `connecting` — wpa_supplicant restarted, waiting for DHCP
- `connected` — DHCP succeeded, have an IP
- `online_to_cloud` — Supabase reachable, device fully online
- `failed` — something went wrong; include `reason` field

Don't skip states. The tech app shows a progress bar based on these.

## Rule BLE-FW-7: Failure Modes and Recovery

| Failure | Status reported | Recovery |
|---|---|---|
| SSID not found in scan | `failed`, reason=`network_not_found` | Tech app prompts user to choose another network |
| Wrong password | `failed`, reason=`wrong_password` | Tech app prompts user to retry |
| DHCP timeout | `failed`, reason=`dhcp_failed` | Tech app prompts user to check router |
| Supabase unreachable but WiFi works | `failed`, reason=`cloud_unreachable` | Tech app waits 30s and retries |
| BLE write disconnected mid-flight | (no notification possible) | Tech app reconnects and reads current status |

After a failure, the tech app can write new credentials without needing to re-pair. The bond persists; only the WiFi config is reset.

## Rule BLE-FW-8: Factory Reset Mechanism

A physical button (GPIO 4) on the device, held for 10 seconds, triggers factory reset:

```python
def factory_reset():
    # Clear WiFi config
    with open('/etc/wpa_supplicant/wpa_supplicant.conf', 'w') as f:
        f.write(WIFI_DEFAULT_HEADER)  # Just the country/control_interface lines
    
    # Clear bond storage
    if os.path.exists('/var/lib/coilshield/bonded_devices.json'):
        os.remove('/var/lib/coilshield/bonded_devices.json')
    
    # BlueZ bond storage (this needs sudo)
    subprocess.run(['sudo', 'rm', '-rf', '/var/lib/bluetooth/*/'], shell=True)
    
    # Restart networking and Bluetooth
    subprocess.run(['sudo', 'systemctl', 'restart', 'wpa_supplicant'])
    subprocess.run(['sudo', 'systemctl', 'restart', 'bluetooth'])
    
    # Restart this firmware process
    subprocess.run(['sudo', 'systemctl', 'restart', 'coilshield'])
```

The button must be debounced. Use `gpiozero.Button` with hold_time=10.

The LED should blink rapidly during the 10-second hold to give visual feedback. After reset, the device is back to its initial unprovisioned state.

## Rule BLE-FW-9: MTU and Chunking

BLE has a default MTU of 23 bytes. Modern phones negotiate up to 247-517 bytes. The WiFi scan list will exceed the default MTU.

`bless` handles MTU negotiation transparently. But for the WiFi scan characteristic, design the data format to fit reasonably:

- Each network is one entry: ~50 bytes serialized
- Send up to 10 networks max in the response (sufficient for any home environment)
- If the response exceeds MTU, BLE breaks it into multiple GATT operations automatically

Don't try to manually chunk. Trust the GATT layer.

## Rule BLE-FW-10: Logging Every BLE Event

Every BLE event gets logged to `bled.log` (BLE daemon log):

- Advertising started / stopped
- Peer connected / disconnected (with peer MAC)
- Pairing requested / completed / failed
- Characteristic read / written (with which UUID, payload size — NOT the contents for sensitive ones)
- Provisioning status transitions

This is the only way to debug field issues remotely. The tech app pushes its own logs to Supabase; we need the firmware side to correlate.

## Common Cursor Pitfalls in BLE Code

- Suggesting `pybluez` instead of `bless` — pybluez is for classic Bluetooth, not BLE
- Suggesting bare `bleak` — that's the client side, not peripheral
- Forgetting to handle disconnect events (memory leak waiting to happen)
- Synchronous BLE code in an async event loop (BlueZ via D-Bus is fundamentally async)
- Trying to set the BLE name to something user-customizable (don't, it complicates discovery)
- Hardcoding the MAC address (it's per-device, queried from BlueZ)
- Not clearing advertising state on errors (device gets stuck "discoverable" forever)

## Smoke Test for BLE Provisioning (Firmware Side)

Before declaring BLE provisioning "validation-ready":

1. Fresh boot with no WiFi config → BLE advertising starts within 30 seconds
2. iOS tech app discovers the device by service UUID
3. iOS tech app pairs successfully (one prompt, then auto-pairs on subsequent)
4. WIFI_SCAN read returns a list of nearby networks within 10 seconds
5. WIFI_PROVISION write with valid credentials triggers connecting → connected → online_to_cloud
6. Status notifications arrive at the tech app within 1 second of state changes
7. After online_to_cloud, BLE advertising stops
8. Disconnect WiFi for 5 minutes → BLE advertising resumes
9. WIFI_PROVISION write with invalid password reports failed/wrong_password correctly
10. Factory reset button held for 10 seconds clears credentials, restarts firmware, BLE advertising resumes

If any step fails, BLE provisioning is not validation-ready.
