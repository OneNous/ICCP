"""Path classification (PATH_OPEN / PATH_WEAK / PATH_STRONG) and FSM helpers."""

from __future__ import annotations

import time

import pytest

import config.settings as cfg
from control import (
    PATH_OPEN,
    PATH_STRONG,
    PATH_WEAK,
    ChannelState,
    Controller,
    classify_path,
)


def _ch() -> ChannelState:
    return ChannelState(0)


def test_classify_low_z_holds_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    ch = _ch()
    ch.status = ChannelState.REGULATE
    ch._last_path_class = PATH_WEAK
    # 11.5 V / 0.02 A = 575 Ω < 800
    assert classify_path(ch, 20.0, 11.5, cfg) == PATH_WEAK


def test_low_z_from_open_promotes_weak_for_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below MIN_EFFECTIVE_OHMS from OPEN → PATH_WEAK so probe runs (not stuck at 0% duty)."""
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    ch = _ch()
    ch.status = ChannelState.OPEN
    assert classify_path(ch, 20.0, 11.5, cfg) == PATH_WEAK


def test_low_z_from_regulate_holds_last_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    ch = _ch()
    ch.status = ChannelState.REGULATE
    ch._last_path_class = PATH_STRONG
    assert classify_path(ch, 20.0, 11.5, cfg) == PATH_STRONG


def test_dry_hysteresis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 3)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 5)

    ch = _ch()
    ch.status = ChannelState.REGULATE
    ch._last_path_class = PATH_WEAK
    for _ in range(2):
        assert classify_path(ch, 0.02, 11.5, cfg) == PATH_WEAK
    assert classify_path(ch, 0.02, 11.5, cfg) == PATH_OPEN


def test_dry_resets_conductive_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 2)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 2)

    ch = _ch()
    ch.status = ChannelState.OPEN
    assert classify_path(ch, 0.4, 11.5, cfg) == PATH_WEAK


def test_zero_current_never_latches_open_without_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At I noise floor, do not latch OPEN — need weak path so probe can run in water."""
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 5)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 3)

    ch = _ch()
    ch.status = ChannelState.OPEN
    for _ in range(6):
        assert classify_path(ch, 0.0, 4.852, cfg) == PATH_WEAK


def test_very_small_current_never_latches_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 3)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 2)

    ch = _ch()
    ch.status = ChannelState.OPEN
    assert classify_path(ch, 0.005, 4.852, cfg) == PATH_WEAK


def test_conductive_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 2)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 3)

    ch = _ch()
    ch.status = ChannelState.REGULATE
    ch._last_path_class = PATH_WEAK
    assert classify_path(ch, 2.0, 5.0, cfg) == PATH_WEAK
    assert classify_path(ch, 2.0, 5.0, cfg) == PATH_WEAK
    assert classify_path(ch, 2.0, 5.0, cfg) == PATH_STRONG


def test_channel_target_ma_matches_cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "TARGET_MA", 1.25)
    monkeypatch.setattr(cfg, "CHANNEL_TARGET_MA", {1: 1.9})
    ctrl = Controller()
    assert ctrl.channel_target_ma(0) == 1.25
    assert ctrl.channel_target_ma(1) == 1.9


def test_state_recheck_resets_hysteresis_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall-clock recheck clears dry_count / conductive_count."""
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
    s0.status = ChannelState.REGULATE
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
