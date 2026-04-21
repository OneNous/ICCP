"""channel_labels: stable Anode N (idx M) wording."""

from __future__ import annotations

import config.settings as cfg
from channel_labels import anode_hw_label, anode_label


def test_anode_label_is_compact() -> None:
    assert anode_label(0) == "Anode 1 (idx 0)"
    assert anode_label(3) == "Anode 4 (idx 3)"


def test_anode_hw_label_includes_gpio_ina_mux() -> None:
    s = anode_hw_label(1)
    assert "Anode 2" in s and "idx 1" in s
    assert "GPIO" in s and "INA" in s
    pin = int(cfg.PWM_GPIO_PINS[1])
    addr = f"0x{int(cfg.INA219_ADDRESSES[1]):02x}"
    assert str(pin) in s and addr in s
    mux = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
    if isinstance(mux, (tuple, list)) and len(mux) > 1:
        assert f"port {int(mux[1])}" in s
