"""Shift-based FSM — docs/iccp-requirements.md §2.2 / §5 / §6.

Covers the new `state_v2` transitions driven by `Controller.advance_shift_fsm`:

* Probing → Polarizing on first driven current.
* Polarizing → Protected only after `shift_mv >= TARGET_SHIFT_MV` sustained
  for `T_POL_STABLE` — and exactly when every channel reaches Protected we
  assert `all_protected` system-wide (per §2.4).
* Polarizing window exceeds `T_POLARIZE_MAX` → `CANNOT_POLARIZE` retry bump,
  then a real latch after `POLARIZE_RETRY_MAX` exhausted retries.
* Protected → Polarizing slip on shift below `TARGET - HYST_PROT_EXIT_MV`.
* Reference invalid escalates every non-Off channel to `Fault` with a
  `REFERENCE_INVALID:<reason>` tag (see §6.1).
"""

from __future__ import annotations

from typing import Callable

import pytest

import config.settings as cfg
from control import (
    STATE_V2_FAULT,
    STATE_V2_OFF,
    STATE_V2_OVERPROTECTED,
    STATE_V2_POLARIZING,
    STATE_V2_PROBING,
    STATE_V2_PROTECTED,
    Controller,
)


def _spec_v2_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse spec timings so a unit test can step through the FSM quickly."""
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 300, raising=False)
    monkeypatch.setattr(cfg, "T_POL_STABLE", 1.0, raising=False)
    monkeypatch.setattr(cfg, "T_SYSTEM_STABLE", 0.0, raising=False)
    monkeypatch.setattr(cfg, "T_POLARIZE_MAX", 2.0, raising=False)
    monkeypatch.setattr(cfg, "POLARIZE_RETRY_MAX", 1, raising=False)
    monkeypatch.setattr(cfg, "POLARIZE_RETRY_INTERVAL_S", 2.0, raising=False)
    monkeypatch.setattr(cfg, "T_SLIP", 0.5, raising=False)
    monkeypatch.setattr(cfg, "HYST_PROT_EXIT_MV", 10.0, raising=False)


def _make_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[float], None]:
    """Return an ``advance(dt)`` helper that drives ``time.monotonic`` forward."""
    import control as control_mod

    now = {"t": 10_000.0}

    def _mono() -> float:
        return now["t"]

    monkeypatch.setattr(control_mod.time, "monotonic", _mono)

    def advance(dt: float) -> None:
        now["t"] += float(dt)

    return advance


def _all_ok(current_ma: float = 1.5) -> dict[int, dict]:
    return {
        i: {"ok": True, "current": current_ma, "bus_v": 5.0}
        for i in range(cfg.NUM_CHANNELS)
    }


def test_polarizing_to_protected_drives_all_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec_v2_timings(monkeypatch)
    advance = _make_clock(monkeypatch)
    ctrl = Controller()
    for ch in range(cfg.NUM_CHANNELS):
        ctrl._pwm.set_duty(ch, 30.0)

    # First tick: every channel has duty + current → Probing.
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=20.0, ref_valid=True)
    assert all(
        ctrl.channel_state_v2()[ch] == STATE_V2_PROBING
        for ch in range(cfg.NUM_CHANNELS)
    )

    # Second tick with a measurable shift → Polarizing.
    advance(0.1)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=60.0, ref_valid=True)
    assert all(
        ctrl.channel_state_v2()[ch] == STATE_V2_POLARIZING
        for ch in range(cfg.NUM_CHANNELS)
    )
    assert ctrl.all_protected() is False

    # Shift is above target, but not yet sustained for T_POL_STABLE.
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=120.0, ref_valid=True)
    assert all(
        ctrl.channel_state_v2()[ch] == STATE_V2_POLARIZING
        for ch in range(cfg.NUM_CHANNELS)
    )

    # After T_POL_STABLE the channels latch into Protected together.
    advance(cfg.T_POL_STABLE + 0.05)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=120.0, ref_valid=True)
    assert all(
        ctrl.channel_state_v2()[ch] == STATE_V2_PROTECTED
        for ch in range(cfg.NUM_CHANNELS)
    )
    assert ctrl.all_protected() is True
    assert ctrl.any_active() is True


def test_cannot_polarize_retry_then_latch_after_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CANNOT_POLARIZE bumps the retry counter and latches after POLARIZE_RETRY_MAX."""
    _spec_v2_timings(monkeypatch)
    advance = _make_clock(monkeypatch)
    ctrl = Controller()
    ch = 0
    ctrl._pwm.set_duty(ch, 30.0)

    # Enter Probing → Polarizing on channel 0, even with very low shift.
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=5.0, ref_valid=True)
    advance(0.1)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=5.0, ref_valid=True)
    assert ctrl.channel_state_v2()[ch] == STATE_V2_POLARIZING

    # First T_POLARIZE_MAX window expires → retry 1/1, Probing with backoff (§6.1 Q4).
    advance(cfg.T_POLARIZE_MAX + 0.05)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=5.0, ref_valid=True)
    assert ctrl.channel_state_v2()[ch] == STATE_V2_PROBING
    assert ctrl._states[ch].polarize_retry_count == 1
    assert ctrl._states[ch].polarize_backoff_until_mono is not None
    assert not ctrl._states[ch].fault_reason

    # After POLARIZE_RETRY_INTERVAL_S, channel can re-enter Polarizing; second window expires.
    advance(cfg.POLARIZE_RETRY_INTERVAL_S + 0.05)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=5.0, ref_valid=True)
    assert ctrl.channel_state_v2()[ch] == STATE_V2_POLARIZING
    advance(cfg.T_POLARIZE_MAX + 0.05)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=5.0, ref_valid=True)
    assert ctrl.channel_state_v2()[ch] == STATE_V2_FAULT
    assert ctrl._states[ch].fault_reason.startswith("CANNOT_POLARIZE")
    assert ctrl.all_protected() is False


def test_reference_invalid_forces_fault_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec_v2_timings(monkeypatch)
    _make_clock(monkeypatch)
    ctrl = Controller()
    for ch in range(cfg.NUM_CHANNELS):
        ctrl._pwm.set_duty(ch, 30.0)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=50.0, ref_valid=True)
    # Now reference goes invalid → every non-Off channel should flip to Fault.
    ctrl.advance_shift_fsm(
        _all_ok(), shift_mv=None, ref_valid=False, ref_valid_reason="read_failed_x3"
    )
    for ch in range(cfg.NUM_CHANNELS):
        assert ctrl.channel_state_v2()[ch] == STATE_V2_FAULT
        assert ctrl._states[ch].fault_reason == "REFERENCE_INVALID:read_failed_x3"


def test_protected_slip_back_to_polarizing(monkeypatch: pytest.MonkeyPatch) -> None:
    _spec_v2_timings(monkeypatch)
    advance = _make_clock(monkeypatch)
    ctrl = Controller()
    ch = 0
    ctrl._pwm.set_duty(ch, 30.0)

    # Walk into Protected on channel 0. Each tick only advances one state edge,
    # so we need: Probing → Polarizing (tick 2), arm `shift_above_target_since`
    # (tick 3), then latch Protected after T_POL_STABLE (tick 4).
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=110.0, ref_valid=True)
    advance(0.1)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=110.0, ref_valid=True)
    advance(0.1)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=110.0, ref_valid=True)
    advance(cfg.T_POL_STABLE + 0.05)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=110.0, ref_valid=True)
    assert ctrl.channel_state_v2()[ch] == STATE_V2_PROTECTED

    # Shift drops below TARGET − HYST_PROT_EXIT_MV sustained for T_SLIP → Polarizing.
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=60.0, ref_valid=True)
    advance(cfg.T_SLIP + 0.05)
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=60.0, ref_valid=True)
    assert ctrl.channel_state_v2()[ch] == STATE_V2_POLARIZING


def test_all_protected_ignores_off_dry_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2.2: enabled channels still `Off` do not block system-level all_protected."""
    monkeypatch.setattr(cfg, "T_SYSTEM_STABLE", 0.0, raising=False)
    ctrl = Controller()
    for ch in range(cfg.NUM_CHANNELS):
        if ch == 0:
            ctrl._states[ch].state_v2 = STATE_V2_OFF
        else:
            ctrl._states[ch].state_v2 = STATE_V2_PROTECTED
    assert ctrl.all_protected() is True


def test_overprotected_ramp_uses_per_channel_pwm_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "SHARED_RETURN_PWM", False, raising=False)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200.0, raising=False)
    monkeypatch.setattr(cfg, "HYST_OVER_FAULT_MV", 50.0, raising=False)
    monkeypatch.setattr(cfg, "T_OVER_FAULT", 3600.0, raising=False)
    monkeypatch.setattr(cfg, "PWM_STEP", 0.1, raising=False)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_PROTECTING", 0.1, raising=False)
    monkeypatch.setattr(cfg, "CHANNEL_PWM_STEP_DOWN_PROTECTING", {1: 4.0}, raising=False)

    ctrl = Controller()
    ch = 1
    ctrl._states[ch].state_v2 = STATE_V2_OVERPROTECTED
    ctrl._pwm.set_duty(ch, 50.0)
    # shift below over_max+HYST so we do not latch OVERPROTECTION fault
    ctrl.advance_shift_fsm(_all_ok(), shift_mv=249.0, ref_valid=True)
    assert ctrl._pwm.duty(ch) == pytest.approx(46.0)


def test_t_in_polarizing_s_not_polarizing_is_zero() -> None:
    ctrl = Controller()
    assert ctrl.t_in_polarizing_s(0) == 0.0
    ctrl._states[0].state_v2 = STATE_V2_POLARIZING
    ctrl._states[0].polarizing_since = None
    assert ctrl.t_in_polarizing_s(0) == 0.0


def test_t_in_polarizing_s_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    advance = _make_clock(monkeypatch)
    import control as control_mod

    ctrl = Controller()
    t0 = control_mod.time.monotonic()
    ctrl._states[0].state_v2 = STATE_V2_POLARIZING
    ctrl._states[0].polarizing_since = t0
    assert ctrl.t_in_polarizing_s(0) == pytest.approx(0.0)
    advance(1.25)
    assert ctrl.t_in_polarizing_s(0) == pytest.approx(1.25)
