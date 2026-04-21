#!/usr/bin/env python3
"""
Best-effort: drive anode MOSFET BCM pins to OUTPUT LOW before the main controller runs.

RPi.GPIO leaves pins as INPUT until configured; a floating N-channel gate can sit in an
undefined or ON state, so **bus voltage can appear on the anode path** while the Pi is
powered but no ICCP process has run yet. That invalidates “gates closed” commissioning
checks (high shunt mA) and is unsafe in the field.

**This script is not a substitute for hardware:** add a **strong gate-to-source pull-down**
(typically 47 kΩ–100 kΩ per FET, layout-dependent) so devices default OFF at power-up,
reset, and whenever the SoC is not driving the pin.

Exits **without** ``GPIO.cleanup()`` so the kernel may keep pins configured as outputs
LOW until another program reconfigures them (see Raspberry Pi / RPi.GPIO behavior for
your OS build).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    if os.environ.get("COILSHIELD_SIM", "0") == "1":
        print("[anode_gates_hold_low] COILSHIELD_SIM=1 — skipping GPIO.")
        return 0

    import config.settings as cfg

    try:
        import RPi.GPIO as GPIO  # noqa: N814
    except (ImportError, RuntimeError) as e:
        print(f"[anode_gates_hold_low] RPi.GPIO unavailable ({e}) — cannot drive pins.")
        return 1

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    pins = tuple(getattr(cfg, "PWM_GPIO_PINS", ()))
    for pin in pins:
        try:
            p = int(pin)
        except (TypeError, ValueError):
            continue
        GPIO.setup(p, GPIO.OUT)
        GPIO.output(p, GPIO.LOW)
    print(
        f"[anode_gates_hold_low] BCM pins {list(pins)} → OUTPUT LOW "
        f"({len(pins)} channel(s)); cleanup() skipped intentionally."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
