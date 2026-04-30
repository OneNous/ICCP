"""Small host / platform helpers shared by entry points."""

from __future__ import annotations

from pathlib import Path


def running_on_raspberry_pi() -> bool:
    """True when ``/proc/device-tree/model`` looks like a Raspberry Pi board."""
    try:
        model = Path("/proc/device-tree/model").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False
    return "Raspberry Pi" in model
