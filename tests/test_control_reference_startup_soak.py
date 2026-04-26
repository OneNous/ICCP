"""Reference startup soak: Controller keeps 0% PWM (same path as thermal pause)."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import ChannelState, Controller


def _readings_all(current_ma: float, *, bus_v: float = 5.0, ok: bool = True) -> dict[int, dict]:
    n = int(cfg.NUM_CHANNELS)
    return {ch: {"ok": ok, "current": current_ma, "bus_v": bus_v} for ch in range(n)}


def test_reference_startup_soak_holds_zero_duty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Soak must not allow channels that would otherwise regulate to apply duty."""
    ctrl = Controller()
    for ch in range(int(cfg.NUM_CHANNELS)):
        ctrl._states[ch].status = ChannelState.REGULATE
        ctrl._pwm.set_duty(ch, 40.0)
    ctrl.set_reference_startup_soak(True)
    try:
        ctrl.update(_readings_all(0.0))
        for ch in range(int(cfg.NUM_CHANNELS)):
            assert ctrl._pwm.duty(ch) == 0.0
    finally:
        ctrl.set_reference_startup_soak(False)
