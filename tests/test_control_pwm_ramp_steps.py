"""Per-state / direction PWM ramp steps (PWM_STEP_*_REGULATE / _PROTECTING)."""

from __future__ import annotations

import pytest

import config.settings as cfg
import control
from control import PATH_STRONG, ChannelState, Controller


@pytest.fixture
def strong_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control, "classify_path", lambda *a, **k: PATH_STRONG)


def _readings_ok(
    *,
    bus_v: float = 5.0,
    current_ma: float = 0.1,
) -> dict[int, dict]:
    return {
        ch: {"ok": True, "current": current_ma, "bus_v": bus_v}
        for ch in range(cfg.NUM_CHANNELS)
    }


def test_regulate_ramp_up_uses_pwm_step_up_regulate(
    monkeypatch: pytest.MonkeyPatch, strong_path: None
) -> None:
    monkeypatch.setattr(cfg, "PWM_STEP", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_REGULATE", 2.5)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "TARGET_MA", 0.5)

    ctrl = Controller()
    ctrl._pwm.set_duty(0, 12.0)
    ctrl._states[0].status = ChannelState.REGULATE

    ctrl.update(_readings_ok(current_ma=0.1))
    assert ctrl._pwm.duty(0) == pytest.approx(14.5)


def test_regulate_ramp_down_uses_pwm_step_down_regulate(
    monkeypatch: pytest.MonkeyPatch, strong_path: None
) -> None:
    monkeypatch.setattr(cfg, "PWM_STEP", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_REGULATE", 1.25)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "TARGET_MA", 0.5)

    ctrl = Controller()
    ctrl._pwm.set_duty(0, 22.0)
    ctrl._states[0].status = ChannelState.REGULATE

    ctrl.update(_readings_ok(current_ma=2.0))
    assert ctrl._pwm.duty(0) == pytest.approx(20.75)


def test_regulate_clamp_above_hi_uses_pwm_step_down_regulate(
    monkeypatch: pytest.MonkeyPatch, strong_path: None
) -> None:
    monkeypatch.setattr(cfg, "PWM_STEP", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_REGULATE", 3.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "TARGET_MA", 0.5)
    # Keep Vcell cap below PWM_MAX so hi is a mid-range value (4.8 V cap + 80% max
    # makes hi==80 and expected step-down 85 is unachievable — duty clamps at 80).
    monkeypatch.setattr(cfg, "VCELL_HARD_MAX_V", 3.0)

    ctrl = Controller()
    hi = min(float(cfg.PWM_MAX_DUTY), control.duty_pct_cap_for_vcell(5.0, cfg))
    ctrl._pwm.set_duty(0, hi + 8.0)
    ctrl._states[0].status = ChannelState.REGULATE

    ctrl.update(_readings_ok(current_ma=0.1))
    assert ctrl._pwm.duty(0) == pytest.approx(hi + 8.0 - 3.0)


def test_protecting_ramp_up_uses_pwm_step_up_protecting(
    monkeypatch: pytest.MonkeyPatch, strong_path: None
) -> None:
    monkeypatch.setattr(cfg, "PWM_STEP", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_PROTECTING", 0.4)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "TARGET_MA", 0.5)

    ctrl = Controller()
    ctrl._pwm.set_duty(0, 10.0)
    ctrl._states[0].status = ChannelState.PROTECTING

    ctrl.update(_readings_ok(current_ma=0.1))
    assert ctrl._pwm.duty(0) == pytest.approx(10.4)


def test_protecting_ramp_down_uses_pwm_step_down_protecting(
    monkeypatch: pytest.MonkeyPatch, strong_path: None
) -> None:
    monkeypatch.setattr(cfg, "PWM_STEP", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_PROTECTING", 0.6)
    monkeypatch.setattr(cfg, "TARGET_MA", 0.5)

    ctrl = Controller()
    ctrl._pwm.set_duty(0, 15.0)
    ctrl._states[0].status = ChannelState.PROTECTING

    ctrl.update(_readings_ok(current_ma=2.0))
    assert ctrl._pwm.duty(0) == pytest.approx(14.4)


def test_pwm_ramp_step_getattr_falls_back_to_pwm_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "PWM_STEP", 7.0)
    for name in (
        "PWM_STEP_UP_REGULATE",
        "PWM_STEP_DOWN_REGULATE",
        "PWM_STEP_UP_PROTECTING",
        "PWM_STEP_DOWN_PROTECTING",
    ):
        monkeypatch.delattr(cfg, name, raising=False)
    assert control.pwm_ramp_step(0, regulating=True, increasing=True) == 7.0
    assert control.pwm_ramp_step(0, regulating=True, increasing=False) == 7.0
    assert control.pwm_ramp_step(0, regulating=False, increasing=True) == 7.0
    assert control.pwm_ramp_step(0, regulating=False, increasing=False) == 7.0


def test_channel_pwm_step_up_regulate_overrides_global_per_anode(
    monkeypatch: pytest.MonkeyPatch, strong_path: None
) -> None:
    monkeypatch.setattr(cfg, "PWM_STEP", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_REGULATE", 0.5)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_REGULATE", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_UP_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "PWM_STEP_DOWN_PROTECTING", 99.0)
    monkeypatch.setattr(cfg, "CHANNEL_PWM_STEP_UP_REGULATE", {0: 3.0})
    monkeypatch.setattr(cfg, "TARGET_MA", 0.5)

    ctrl = Controller()
    for ch in (0, 1):
        ctrl._pwm.set_duty(ch, 12.0)
        ctrl._states[ch].status = ChannelState.REGULATE

    ctrl.update(_readings_ok(current_ma=0.1))
    assert ctrl._pwm.duty(0) == pytest.approx(15.0)
    assert ctrl._pwm.duty(1) == pytest.approx(12.5)
