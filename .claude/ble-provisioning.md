# BLE provisioning (firmware)

**Do not duplicate UUID tables here.** They drifted from production once already.

| What | Where |
|------|--------|
| **Frozen GATT contract** (UUIDs, payloads, opcodes) | Monorepo [`packages/api-contract/ble-protocol.md`](../../packages/api-contract/ble-protocol.md) |
| **Process / hand-off rules** (mDNS, LAN, ops, **QR/SN → BLE name matching**) | Monorepo [`.claude/ble-provisioning.md`](../../.claude/ble-provisioning.md) |
| **BlueZ peripheral (Linux Pi)** | `src/pi_edge/uuids.py`, `src/pi_edge/ble_gatt_bluez.py`, entry `iccp-ble-provision` → `pi_edge.ble_provision` |
| **Bench stub (bless)** | `pi-ble/ble_provision.py` (must stay aligned with `ble-protocol.md`) |

When you change the contract, update **api-contract + `pi_edge` + `pi-ble` + mobile apps** in one PR.
