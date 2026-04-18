#!/usr/bin/env bash
# CoilShield — I2C sanity check (run on the Pi from repo root or anywhere with i2c-tools).
# Usage: ./scripts/diagnose_i2c.sh
#        COILSHIELD_I2C_BUS=1 ./scripts/diagnose_i2c.sh
#        COILSHIELD_I2C_BUS=1 COILSHIELD_REF_I2C_BUS=3 ./scripts/diagnose_i2c.sh
set -euo pipefail

BUS="${COILSHIELD_I2C_BUS:-1}"
REF_BUS="${COILSHIELD_REF_I2C_BUS:-$BUS}"

echo "=== I2C adapters (header bus is usually the one named bcm2835 / i2c_arm; note the N in i2c-N) ==="
sudo i2cdetect -l || true
echo
echo "=== Scan anode bus ${BUS} (override with COILSHIELD_I2C_BUS=N; config I2C_BUS) ==="
sudo i2cdetect -y "${BUS}"
if [ "${REF_BUS}" != "${BUS}" ]; then
  echo
  echo "=== Scan reference bus ${REF_BUS} (COILSHIELD_REF_I2C_BUS; config REF_I2C_BUS) ==="
  sudo i2cdetect -y "${REF_BUS}"
fi
echo
echo "CoilShield anodes (header I2C, GPIO 2/3 on 40-pin):"
echo "  Four INA219 anode boards: 0x40 0x41 0x44 0x45 (defaults in INA219_ADDRESSES)"
echo "Reference INA219 (REF_INA219_ADDRESS, often 0x42 on shared bus; 0x40 OK if alone on gpio bus):"
echo "  Set REF_I2C_BUS in config/settings.py to match i2c-N for ref wiring."
echo
echo "If every cell shows -- : the adapter ran a probe but no slave ACKed."
echo "Check: devices powered, GND common with Pi, SDA/SCL not swapped, correct bus N for your header."
echo "Bus 2 is often NOT the 40-pin header I2C; prefer the N shown for i2c_arm / bcm2835 @7e804000."
