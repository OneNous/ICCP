"""DS18B20 / sim temperature."""

from __future__ import annotations

import pytest

import temp


def test_read_fahrenheit_sim_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(temp, "SIM_MODE", True)
    t = temp.read_fahrenheit()
    assert isinstance(t, float)
    assert 50.0 <= t <= 110.0
