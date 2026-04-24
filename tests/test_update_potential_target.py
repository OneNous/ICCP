"""Outer loop update_potential_target — interaction with Overprotected FSM."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import STATE_V2_OFF, STATE_V2_OVERPROTECTED, Controller


def test_update_potential_target_skips_when_any_overprotected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
