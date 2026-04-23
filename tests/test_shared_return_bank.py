"""`SHARED_RETURN_PWM` bank: unified duty, aggregate I vs sum(target)."""

import config.settings as cfg
from control import (
    Controller,
    PWMBank,
    ChannelState,
    PATH_STRONG,
    pwm_ramp_step,
)


def test_pwm_ramp_step_ignores_per_channel_dicts_when_shared() -> None:
    cfg.SHARED_RETURN_PWM = True
    cfg.PWM_STEP_UP_REGULATE = 1.0
    prev = dict(cfg.CHANNEL_PWM_STEP_UP_REGULATE)
    cfg.CHANNEL_PWM_STEP_UP_REGULATE = {0: 9.0}
    try:
        s = pwm_ramp_step(0, regulating=True, increasing=True)
        assert s == 1.0
    finally:
        cfg.SHARED_RETURN_PWM = False
        cfg.CHANNEL_PWM_STEP_UP_REGULATE = prev


def test_pwmbank_set_duty_unified_matches_all_duties() -> None:
    b = PWMBank()
    b.set_duty_unified(12.0)
    for c in range(cfg.NUM_CHANNELS):
        assert b.duty(c) == 12.0


def test_controller_update_unifies_duty_in_bank_mode(monkeypatch) -> None:
    """classify_path forced STRONG + REGULATE on all so bank_ramp_path runs with rows."""

    def _strong(*args, **kwargs):
        return PATH_STRONG

    monkeypatch.setattr("control.classify_path", _strong)
    cfg.SHARED_RETURN_PWM = True
    r = {
        ch: {"ok": True, "current": 0.4, "bus_v": 4.5} for ch in range(cfg.NUM_CHANNELS)
    }
    try:
        ctrl = Controller()
        for ch in range(cfg.NUM_CHANNELS):
            ctrl._states[ch].status = ChannelState.REGULATE  # noqa: SLF001
        _, _ = ctrl.update(r)
        d = ctrl.duties()
        d0 = d[0]
        assert all(d[i] == d0 for i in range(cfg.NUM_CHANNELS))
    finally:
        cfg.SHARED_RETURN_PWM = False


def test_set_output_duty_pct_drives_unified() -> None:
    cfg.SHARED_RETURN_PWM = True
    try:
        c = Controller()
        c.set_output_duty_pct(2, 35.0)
        assert all(c.output_duty_pct(i) == 35.0 for i in range(cfg.NUM_CHANNELS))
    finally:
        cfg.SHARED_RETURN_PWM = False
        c.all_outputs_off()
