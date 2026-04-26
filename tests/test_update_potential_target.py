"""Outer loop update_potential_target — interaction with Overprotected FSM."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import STATE_V2_OFF, STATE_V2_OVERPROTECTED, Controller


def _no_outer_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "OUTER_LOOP_POTENTIAL_MIN_S", 0.0, raising=False)


def test_update_potential_target_skips_when_any_overprotected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_outer_throttle(monkeypatch)
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100.0, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA_STEP", 0.02, raising=False)
    monkeypatch.setattr(cfg, "MAX_MA", 2.0, raising=False)
    start_target = 0.5
    cfg.TARGET_MA = start_target
    ctrl = Controller()
    ctrl._states[0].state_v2 = STATE_V2_OVERPROTECTED
    # Low shift would nudge target up
    ctrl.update_potential_target(10.0)
    assert cfg.TARGET_MA == start_target
    # High shift would nudge down — also skipped
    cfg.TARGET_MA = start_target
    ctrl.update_potential_target(250.0)
    assert cfg.TARGET_MA == start_target


def test_update_potential_target_nudges_when_not_overprotected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_outer_throttle(monkeypatch)
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100.0, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA_STEP", 0.02, raising=False)
    monkeypatch.setattr(cfg, "MAX_MA", 2.0, raising=False)
    cfg.TARGET_MA = 0.5
    ctrl = Controller()
    for s in ctrl._states:
        s.state_v2 = STATE_V2_OFF
    ctrl.update_potential_target(10.0)
    assert cfg.TARGET_MA == pytest.approx(0.52)
    assert ctrl.any_overprotected() is False


def test_update_potential_target_trims_in_ok_band_toward_center(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_outer_throttle(monkeypatch)
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100.0, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA_STEP", 0.02, raising=False)
    monkeypatch.setattr(cfg, "MAX_MA", 2.0, raising=False)
    monkeypatch.setattr(cfg, "OUTER_LOOP_TRIM_TO_SHIFT_CENTER", True, raising=False)
    monkeypatch.setattr(cfg, "OUTER_LOOP_SHIFT_TRIM_TOL_MV", 3.0, raising=False)
    cfg.TARGET_MA = 0.5
    ctrl = Controller()
    for s in ctrl._states:
        s.state_v2 = STATE_V2_OFF
    # Shift above center + tol → reduce setpoint
    ctrl.update_potential_target(118.0)
    assert cfg.TARGET_MA == pytest.approx(0.48)
    cfg.TARGET_MA = 0.5
    # Shift below center − tol → increase setpoint
    ctrl.update_potential_target(90.0)
    assert cfg.TARGET_MA == pytest.approx(0.52)


def test_update_potential_target_dead_band_when_trim_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between center and over-max, no trim, no change. OVER max still nudges down."""
    _no_outer_throttle(monkeypatch)
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100.0, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA_STEP", 0.02, raising=False)
    monkeypatch.setattr(cfg, "MAX_MA", 2.0, raising=False)
    monkeypatch.setattr(cfg, "OUTER_LOOP_TRIM_TO_SHIFT_CENTER", False, raising=False)
    cfg.TARGET_MA = 0.5
    ctrl = Controller()
    for s in ctrl._states:
        s.state_v2 = STATE_V2_OFF
    ctrl.update_potential_target(118.0)
    assert cfg.TARGET_MA == pytest.approx(0.5)


def test_update_potential_target_uses_under_not_80pct_when_trim_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If shift is UNDER (below mV target) but above the old 0.8*center rule, we still nudge up."""
    _no_outer_throttle(monkeypatch)
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100.0, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA_STEP", 0.02, raising=False)
    monkeypatch.setattr(cfg, "MAX_MA", 2.0, raising=False)
    monkeypatch.setattr(cfg, "OUTER_LOOP_TRIM_TO_SHIFT_CENTER", False, raising=False)
    cfg.TARGET_MA = 0.5
    ctrl = Controller()
    for s in ctrl._states:
        s.state_v2 = STATE_V2_OFF
    ctrl.update_potential_target(85.0)
    assert cfg.TARGET_MA == pytest.approx(0.52)


def test_update_potential_target_respects_out_loop_min_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second nudge in the same throttling window is skipped unless force=True."""
    monkeypatch.setattr(cfg, "OUTER_LOOP_POTENTIAL_MIN_S", 3600.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100.0, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA_STEP", 0.02, raising=False)
    monkeypatch.setattr(cfg, "MAX_MA", 2.0, raising=False)
    cfg.TARGET_MA = 0.5
    ctrl = Controller()
    for s in ctrl._states:
        s.state_v2 = STATE_V2_OFF
    ctrl.update_potential_target(10.0)
    assert cfg.TARGET_MA == pytest.approx(0.52)
    ctrl.update_potential_target(10.0)
    assert cfg.TARGET_MA == pytest.approx(0.52)
    ctrl.update_potential_target(10.0, force=True)
    assert cfg.TARGET_MA == pytest.approx(0.54)
