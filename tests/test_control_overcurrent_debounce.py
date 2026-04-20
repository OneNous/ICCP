"""OVERCURRENT latch debounce (OVERCURRENT_LATCH_TICKS)."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import ChannelState, Controller


def _all_channels(current_ch0: float, current_other: float = 0.5) -> dict[int, dict]:
    return {
        i: {
            "ok": True,
            "current": current_ch0 if i == 0 else current_other,
            "bus_v": 5.0,
        }
        for i in range(cfg.NUM_CHANNELS)
    }


def test_overcurrent_requires_consecutive_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "OVERCURRENT_LATCH_TICKS", 3)
    monkeypatch.setattr(cfg, "MAX_MA", 5.0)
    monkeypatch.setattr(cfg, "CHANNEL_MAX_MA", {})
    ctrl = Controller()
    hi = _all_channels(8.0)
    assert ctrl.update(hi)[1] is False
    assert ctrl.update(hi)[1] is False
    assert ctrl.update(hi)[1] is True
    assert ctrl._states[0].status == ChannelState.FAULT


def test_overcurrent_streak_resets_when_current_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "OVERCURRENT_LATCH_TICKS", 3)
    monkeypatch.setattr(cfg, "MAX_MA", 5.0)
    monkeypatch.setattr(cfg, "CHANNEL_MAX_MA", {})
    ctrl = Controller()
    hi = _all_channels(8.0)
    ok = _all_channels(0.5)
    ctrl.update(hi)
    ctrl.update(hi)
    ctrl.update(ok)
    ctrl.update(hi)
    assert ctrl._states[0].status != ChannelState.FAULT


def test_overcurrent_latch_ticks_one_matches_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "OVERCURRENT_LATCH_TICKS", 1)
    monkeypatch.setattr(cfg, "MAX_MA", 5.0)
    monkeypatch.setattr(cfg, "CHANNEL_MAX_MA", {})
    ctrl = Controller()
    hi = _all_channels(8.0)
    assert ctrl.update(hi)[1] is True
    assert ctrl._states[0].status == ChannelState.FAULT


def test_read_error_fault_includes_bus_and_shunt_snapshot() -> None:
    ctrl = Controller()
    r = {
        i: {"ok": True, "current": 0.1, "bus_v": 5.0} for i in range(cfg.NUM_CHANNELS)
    }
    r[0] = {"ok": False, "error": "I2C fail", "bus_v": 1.25, "shunt_mv": 0.5}
    faults, _ = ctrl.update(r)
    assert any("READ ERROR" in f for f in faults)
    assert any("last bus_v=1.25" in f for f in faults)
    assert any("shunt_mv=0.5" in f for f in faults)
