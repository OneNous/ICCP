#!/usr/bin/env bash
# CoilShield — I2C sanity check (run on the Pi from repo root or anywhere with i2c-tools).
# Usage: ./scripts/diagnose_i2c.sh
#        COILSHIELD_I2C_BUS=1 ./scripts/diagnose_i2c.sh
set -euo pipefail

BUS="${COILSHIELD_I2C_BUS:-1}"

echo "=== I2C adapters (header bus is usually the one named bcm2835 / i2c_arm; note the N in i2c-N) ==="
sudo i2cdetect -l || true
echo
echo "=== Scan bus ${BUS} (override with COILSHIELD_I2C_BUS=N; config/settings.py uses I2C_BUS for SMBus/PCF8591) ==="
sudo i2cdetect -y "${BUS}"
echo
echo "CoilShield expects on the same SDA/SCL as GPIO pins 3 & 5:"
echo "  INA3221 at 0x40 and 0x41  |  PCF8591 (default) at 0x48"
echo
echo "If every cell shows -- : the adapter ran a probe but no slave ACKed."
echo "Check: devices powered, GND common with Pi, SDA/SCL not swapped, correct bus N for your header."
echo "Bus 2 is often NOT the 40-pin header I2C; prefer the N shown for i2c_arm / bcm2835 @7e804000."
