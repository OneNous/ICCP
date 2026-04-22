"""STEP 1b mux-aware I2C downstream probe (TCA9548A)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import config.settings as cfg
import hw_probe
import pytest


def test_mux_downstream_returns_none_when_no_mux_in_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "I2C_MUX_ADDRESS", None)
    assert hw_probe.mux_downstream_i2c_probe(1) is None


def test_mux_downstream_pings_ina_per_port_and_ads() -> None:
    smbus2 = pytest.importorskip("smbus2")
    bus = MagicMock()
    bus.read_byte = MagicMock(return_value=0)
    with patch.object(smbus2, "SMBus", return_value=bus) as mock_smbus:
        r = hw_probe.mux_downstream_i2c_probe(1)
    mock_smbus.assert_called()
    assert r is not None
    assert r.error is None
    n = min(len(cfg.INA219_ADDRESSES), len(cfg.I2C_MUX_CHANNELS_INA219 or ()))
    assert len(r.ina_rows) == n
    assert r.ads_checked is True
    assert r.ads_ok is True
    for _ch, _tca, ina, ok in r.ina_rows:
        assert ok is True
    bus.write_byte.assert_any_call(0x70, 0)
    bus.close.assert_called()
