"""
Consistent anode naming for logs, faults, telemetry, and operator-facing copy.

``ch`` is always the **firmware channel index** (0 .. ``NUM_CHANNELS`` - 1).
**Anode N** is the harness / field label (N = ch + 1).
"""

from __future__ import annotations

import config.settings as cfg


def anode_label(ch: int) -> str:
    """Compact label: harness anode number plus 0-based firmware index."""
    return f"Anode {ch + 1} (idx {ch})"


def anode_hw_label(ch: int) -> str:
    """
    Wiring-oriented label: GPIO, INA219 address, optional TCA9548A downstream port.

    Matches ``PWM_GPIO_PINS``, ``INA219_ADDRESSES``, ``I2C_MUX_CHANNELS_INA219`` in settings.
    """
    pins = getattr(cfg, "PWM_GPIO_PINS", ())
    addrs = getattr(cfg, "INA219_ADDRESSES", ())
    mux = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
    try:
        pin = int(pins[ch])
    except (IndexError, TypeError, ValueError):
        pin_str = "?"
    else:
        pin_str = str(pin)
    try:
        addr_s = f"0x{int(addrs[ch]):02x}"
    except (IndexError, TypeError, ValueError):
        addr_s = "?"
    mux_s = ""
    if isinstance(mux, (tuple, list)) and ch < len(mux):
        mux_s = f", TCA9548A port {int(mux[ch])}"
    return f"Anode {ch + 1} (idx {ch}, GPIO {pin_str}, INA {addr_s}{mux_s})"
