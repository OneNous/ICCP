"""REGULATE: probe floor before idle-hold, so 0 mA with I < target does not stick at 0%% duty."""

from __future__ import annotations

import pytest

import config.settings as cfg
import control
from control import ChannelState, Controller, duty_pct_cap_for_vcell


def _readings_all(
    current_ma: float, *, bus_v: float = 5.0, ok: bool = True
) -> dict[int, dict]:
    n = int(cfg.NUM_CHANNELS)
    r = {ch: {"ok": ok, "current": current_ma, "bus_v": bus_v} for ch in range(n)}
    return r


def test_regulate_uses_duty_probe_when_i_below_idle_and_below_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "TARGET_MA", 0.05)
    monkeypatch.setattr(cfg, "REGULATE_IDLE_OFF_BELOW_MA", 0.05)
    monkeypatch.setattr(cfg, "DUTY_PROBE", 0.1)

    ctrl = Controller()
    cap = duty_pct_cap_for_vcell(5.0, cfg)
    want_lo = min(float(cfg.DUTY_PROBE), cap)
    for ch in range(int(cfg.NUM_CHANNELS)):
        ctrl._states[ch].status = ChannelState.REGULATE
        ctrl._pwm.set_duty(ch, 0.0)

    ctrl.update(_readings_all(0.0, bus_v=5.0))
    assert ctrl._pwm.duty(0) == pytest.approx(want_lo)


def test_regulate_idle_off_still_cuts_output_when_satisfied_in_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """|I| below REGULATE_IDLE_OFF but I ≥ mA setpoint: hold 0% (open-path / noise guard)."""
    monkeypatch.setattr(cfg, "TARGET_MA", 0.03)
    monkeypatch.setattr(cfg, "REGULATE_IDLE_OFF_BELOW_MA", 0.05)
    monkeypatch.setattr(cfg, "DUTY_PROBE", 0.1)

    cap = duty_pct_cap_for_vcell(5.0, cfg)
    want_lo = min(float(cfg.DUTY_PROBE), cap)
    ctrl = Controller()
    for ch in range(int(cfg.NUM_CHANNELS)):
        ctrl._states[ch].status = ChannelState.REGULATE
    ctrl._pwm.set_duty(0, want_lo)

    # Measured I matches target but is still "noise" w.r.t. idle floor → force off.
    ctrl.update(_readings_all(0.03, bus_v=5.0))
    assert ctrl._pwm.duty(0) == pytest.approx(0.0)
