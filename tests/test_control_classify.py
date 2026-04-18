"""Channel path classification (DRY / WEAK_WET / CONDUCTIVE)."""

from __future__ import annotations

import time

import pytest

import config.settings as cfg
from control import ChannelState, Controller, classify_channel


def _ch() -> ChannelState:
    return ChannelState(0)


def test_classify_low_z_holds_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    ch = _ch()
    ch.status = ChannelState.WEAK_WET
    # 11.5 V / 0.02 A = 575 Ω < 800
    assert classify_channel(ch, 20.0, 11.5, cfg) == ChannelState.WEAK_WET


def test_low_z_from_dry_promotes_weak_wet_for_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below MIN_EFFECTIVE_OHMS from DRY → WEAK_WET so probe runs (not stuck at 0% duty)."""
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    ch = _ch()
    ch.status = ChannelState.DRY
    ch.dry_count = 9
    # Same geometry as test_classify_low_z_holds_previous: Z < MIN
    assert classify_channel(ch, 20.0, 11.5, cfg) == ChannelState.WEAK_WET
    assert ch.dry_count == 0
    assert ch.conductive_count == 0
    ch.status = ChannelState.WEAK_WET
    assert classify_channel(ch, 20.0, 11.5, cfg) == ChannelState.WEAK_WET


def test_dry_hysteresis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 3)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 5)

    ch = _ch()
    ch.status = ChannelState.WEAK_WET
    for _ in range(2):
        assert classify_channel(ch, 0.02, 11.5, cfg) == ChannelState.WEAK_WET
    assert classify_channel(ch, 0.02, 11.5, cfg) == ChannelState.DRY


def test_weak_wet_high_impedance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 2)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 2)

    ch = _ch()
    ch.status = ChannelState.DRY
    # 0.4 mA, 11.5 V → 28.75 kΩ > 12k, current < 0.5 mA
    assert classify_channel(ch, 0.4, 11.5, cfg) == ChannelState.WEAK_WET


def test_zero_current_is_weak_wet_not_dry(monkeypatch: pytest.MonkeyPatch) -> None:
    """At I noise floor, do not latch DRY — need WEAK_WET so probe can run in water."""
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 5)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 3)

    ch = _ch()
    ch.status = ChannelState.DRY
    for _ in range(10):
        assert classify_channel(ch, 0.0, 4.852, cfg) == ChannelState.WEAK_WET


def test_very_low_current_below_noise_floor_weak_wet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 3)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 2)

    ch = _ch()
    ch.status = ChannelState.DRY
    assert classify_channel(ch, 0.005, 4.852, cfg) == ChannelState.WEAK_WET


def test_conductive_requires_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 2)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 3)

    ch = _ch()
    ch.status = ChannelState.WEAK_WET
    # 2 mA @ 5 V → 2.5 kΩ, strong path
    assert classify_channel(ch, 2.0, 5.0, cfg) == ChannelState.WEAK_WET
    assert classify_channel(ch, 2.0, 5.0, cfg) == ChannelState.WEAK_WET
    assert classify_channel(ch, 2.0, 5.0, cfg) == ChannelState.CONDUCTIVE


def test_state_recheck_resets_hysteresis_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall-clock recheck clears dry_count / conductive_count so classify can move."""
    monkeypatch.setattr(cfg, "STATE_RECHECK_INTERVAL_S", 10.0)
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 99)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 3)

    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    ctrl = Controller()
    s0 = ctrl._states[0]
    s0.status = ChannelState.WEAK_WET
    s0.last_state_recheck_monotonic = 980.0
    s0.dry_count = 7
    s0.conductive_count = 4

    def _readings(ma: float) -> dict[int, dict]:
        return {
            i: {"ok": True, "current": ma, "bus_v": 5.0}
            for i in range(cfg.NUM_CHANNELS)
        }

    ctrl.update(_readings(0.02))
    assert s0.conductive_count == 0
    assert s0.dry_count == 1

    clock["t"] = 1005.0
    ctrl.update(_readings(0.02))
    assert s0.dry_count == 2

    clock["t"] = 1020.0
    ctrl.update(_readings(0.02))
    assert s0.dry_count == 1
    assert s0.conductive_count == 0
