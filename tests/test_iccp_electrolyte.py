"""iccp_electrolyte helpers — impedance, LSB, feedforward, health."""

from __future__ import annotations

import pytest

import config.settings as cfg
from iccp_electrolyte import (
    anode_activity_score,
    cell_impedance_ohm,
    effective_target_ma_floor,
    ina219_nominal_current_lsb_ma,
    predict_duty_feedforward,
    system_health_composite,
)


def test_cell_impedance_ohm() -> None:
    z = cell_impedance_ohm(5.0, 0.5)
    assert z == pytest.approx(10000.0, rel=1e-3)


def test_ina219_lsb_scales_with_shunt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "INA219_SHUNT_OHMS", 0.1, raising=False)
    monkeypatch.setattr(cfg, "INA219_SHUNT_LSB_V", 1e-5, raising=False)
    a = ina219_nominal_current_lsb_ma()
    assert a == pytest.approx(0.1, rel=1e-6)
    monkeypatch.setattr(cfg, "INA219_SHUNT_OHMS", 1.0, raising=False)
    b = ina219_nominal_current_lsb_ma()
    assert b == pytest.approx(0.01, rel=1e-6)


def test_effective_target_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cfg, "INA219_ENFORCE_CURRENT_LSB_FLOOR", True, raising=False
    )
    monkeypatch.setattr(cfg, "TARGET_MA_FLOOR", 0.0, raising=False)
    monkeypatch.setattr(cfg, "INA219_SHUNT_OHMS", 0.1, raising=False)
    assert effective_target_ma_floor() >= ina219_nominal_current_lsb_ma()
    monkeypatch.setattr(cfg, "TARGET_MA_FLOOR", 0.05, raising=False)
    assert effective_target_ma_floor() >= 0.05
    monkeypatch.setattr(
        cfg, "INA219_ENFORCE_CURRENT_LSB_FLOOR", False, raising=False
    )
    monkeypatch.setattr(cfg, "TARGET_MA_FLOOR", 0.0, raising=False)
    assert effective_target_ma_floor() == 0.0


def test_predict_duty_feedforward_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "PWM_MIN_DUTY", 0.0, raising=False)
    d = predict_duty_feedforward(0.5, 5.0, 10_000.0)
    assert 0.0 <= d <= 100.0


def test_anode_and_health() -> None:
    assert anode_activity_score(40.0, 50.0) < anode_activity_score(50.0, 50.0)
    h = system_health_composite(0.8, 0.9, 0.7)
    assert 0.0 < h < 1.0
