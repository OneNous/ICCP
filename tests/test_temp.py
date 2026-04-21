"""DS18B20 / sim temperature."""

from __future__ import annotations

import pytest

import config.settings as cfg
import temp


def test_read_fahrenheit_sim_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(temp, "SIM_MODE", True)
    t = temp.read_fahrenheit()
    assert isinstance(t, float)
    assert 50.0 <= t <= 110.0


def test_in_operating_range_none_legacy_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "THERMAL_PAUSE_WHEN_SENSOR_MISSING", False)
    assert temp.in_operating_range(None) is True


def test_in_operating_range_none_fail_safe_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "THERMAL_PAUSE_WHEN_SENSOR_MISSING", True)
    assert temp.in_operating_range(None) is False
