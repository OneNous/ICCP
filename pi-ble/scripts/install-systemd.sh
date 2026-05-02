#!/usr/bin/env bash
# Install systemd units for BLE provision + MQTT helpers (edit paths/serial before use).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
SERIAL="${COILSHIELD_SERIAL:-CS-A4-7F-2C-91}"
VENV_PY="${VENV_PY:-$ROOT/.venv/bin/python3}"

sudo tee "${UNIT_DIR}/coilshield-ble-provision.service" >/dev/null <<EOF
[Unit]
Description=CoilShield BLE WiFi provisioning
After=bluetooth.target network-pre.target
Wants=bluetooth.target

[Service]
Type=simple
Environment=COILSHIELD_SERIAL=${SERIAL}
WorkingDirectory=${ROOT}
ExecStart=${VENV_PY} ${ROOT}/ble_provision.py --v
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo tee "${UNIT_DIR}/coilshield-mqtt-commissioning.service" >/dev/null <<EOF
[Unit]
Description=CoilShield MQTT commissioning publisher
After=network-online.target

[Service]
Type=simple
Environment=COILSHIELD_SERIAL=${SERIAL}
WorkingDirectory=${ROOT}
ExecStart=${VENV_PY} ${ROOT}/mqtt_commissioning.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo tee "${UNIT_DIR}/coilshield-mqtt-telemetry.service" >/dev/null <<EOF
[Unit]
Description=CoilShield MQTT telemetry publisher
After=network-online.target

[Service]
Type=simple
Environment=COILSHIELD_SERIAL=${SERIAL}
WorkingDirectory=${ROOT}
ExecStart=${VENV_PY} ${ROOT}/mqtt_telemetry.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
echo "Installed units under ${UNIT_DIR}. Enable with:"
echo "  sudo systemctl enable --now coilshield-ble-provision.service"
echo "  sudo systemctl enable --now coilshield-mqtt-commissioning.service"
echo "  sudo systemctl enable --now coilshield-mqtt-telemetry.service"
