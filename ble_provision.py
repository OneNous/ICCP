#!/usr/bin/env python3
"""Thin shim for the Pi BLE Wi‑Fi provisioner (see ``pi_edge.ble_provision``)."""

from pi_edge.ble_provision import main

if __name__ == "__main__":
    raise SystemExit(main())
