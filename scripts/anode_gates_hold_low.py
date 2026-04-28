#!/usr/bin/env python3
"""
Optional systemd ExecStartPre hook for iccp.service.

Some deployments reference this path so anode gate BCM lines can be driven LOW before
``iccp start`` owns PWM (see docs/mosfet-off-verification.md). Hardware gate→source
pull-downs remain the reliable power-up default; extend this script with RPi.GPIO if
your board needs software hold-low before the main process starts.

By default this exits successfully so missing-file errors do not block ``iccp``.
"""

from __future__ import annotations

import sys


def main() -> int:
    # Example (uncomment and set BCM pins for your wiring):
    # import RPi.GPIO as GPIO
    # for bcm in (27, 22, 23, 24):
    #     GPIO.setmode(GPIO.BCM)
    #     GPIO.setup(bcm, GPIO.OUT)
    #     GPIO.output(bcm, GPIO.LOW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
