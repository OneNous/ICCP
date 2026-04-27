"""FEEDFORWARD_MAX_DUTY_JUMP_PCT limits a single step from current duty."""

from __future__ import annotations

import pytest

import config.settings as cfg
import iccp_electrolyte
from control import ChannelState, Controller, duty_pct_cap_for_vcell
from iccp_electrolyte import cell_impedance_ohm


def _readings(current_ma: float, *, bus_v: float = 5.0) -> dict[int, dict]:
    n = int(cfg.NUM_CHANNELS)
    return {ch: {"ok": True, "current": current_ma, "bus_v": bus_v} for ch in range(n)}


def test_regulate_feedforward_duty_capped_by_jump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "FEEDFORWARD_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "FEEDBACK_KP", 0.0, raising=False)
    monkeypatch.setattr(cfg, "FEEDFORWARD_MAX_DUTY_JUMP_PCT", 10.0, raising=False)
    monkeypatch.setattr(cfg, "DUTY_PROBE", 0.1, raising=False)
    monkeypatch.setattr(cfg, "REGULATE_IDLE_OFF_BELOW_MA", 0.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA", 1.0, raising=False)
    monkeypatch.setattr(
        iccp_electrolyte,
        "predict_duty_feedforward",
        lambda *a, **k: 50.0,
    )
    z_val = float(cell_impedance_ohm(5.0, 2.5))
    cap = duty_pct_cap_for_vcell(5.0, cfg)
    want_lo = min(float(cfg.DUTY_PROBE), cap)
    assert want_lo < 5.0

    ctrl = Controller()
    for ch in range(int(cfg.NUM_CHANNELS)):
        st = ctrl._states[ch]
        st.status = ChannelState.REGULATE
        st._feedforward_done = False
        st._z_window.clear()
        st._z_window.extend([z_val, z_val, z_val])
        ctrl._pwm.set_duty(ch, 0.0)

    ctrl.update(_readings(2.5, bus_v=5.0))
    assert ctrl._pwm.duty(0) == pytest.approx(10.0, abs=0.02)


def test_regulate_feedforward_respects_zero_jump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 = do not cap vs current duty; feedforward value (mocked) applies."""
    monkeypatch.setattr(cfg, "FEEDFORWARD_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "FEEDBACK_KP", 0.0, raising=False)
    monkeypatch.setattr(cfg, "FEEDFORWARD_MAX_DUTY_JUMP_PCT", 0.0, raising=False)
    monkeypatch.setattr(cfg, "DUTY_PROBE", 0.1, raising=False)
    monkeypatch.setattr(cfg, "REGULATE_IDLE_OFF_BELOW_MA", 0.0, raising=False)
    monkeypatch.setattr(cfg, "TARGET_MA", 1.0, raising=False)
    monkeypatch.setattr(
        iccp_electrolyte,
        "predict_duty_feedforward",
        lambda *a, **k: 50.0,
    )
    z_val = float(cell_impedance_ohm(5.0, 2.5))

    ctrl = Controller()
    for ch in range(int(cfg.NUM_CHANNELS)):
        st = ctrl._states[ch]
        st.status = ChannelState.REGULATE
        st._feedforward_done = False
        st._z_window.clear()
        st._z_window.extend([z_val, z_val, z_val])
        ctrl._pwm.set_duty(ch, 0.0)

    ctrl.update(_readings(2.5, bus_v=5.0))
    assert ctrl._pwm.duty(0) == pytest.approx(50.0, abs=0.02)
