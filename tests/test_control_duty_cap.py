"""Vcell-derived max duty cap (duty_pct_cap_for_vcell)."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import _quantize_duty_for_gpio, duty_pct_cap_for_vcell


def test_duty_cap_disabled_when_vmax_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "VCELL_HARD_MAX_V", 0.0)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 80.0)
    assert duty_pct_cap_for_vcell(4.85, cfg) == 80.0


def test_duty_cap_disabled_when_vmax_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "VCELL_HARD_MAX_V", -1.0)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 75.0)
    assert duty_pct_cap_for_vcell(4.85, cfg) == 75.0


def test_duty_cap_uses_formula_when_below_pwm_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "VCELL_HARD_MAX_V", 1.6)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 99.0)
    assert duty_pct_cap_for_vcell(4.85, cfg) == pytest.approx(100.0 * 1.6 / 4.85)


def test_duty_cap_clamped_by_pwm_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "VCELL_HARD_MAX_V", 1.6)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 20.0)
    assert duty_pct_cap_for_vcell(4.85, cfg) == 20.0


def test_duty_cap_bus_near_zero_returns_pwm_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "VCELL_HARD_MAX_V", 1.6)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 80.0)
    assert duty_pct_cap_for_vcell(1e-9, cfg) == 80.0


def test_quantize_duty_respects_pwm_duty_quantum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "PWM_DUTY_QUANTUM", 0.1)
    assert _quantize_duty_for_gpio(1.14) == pytest.approx(1.1)
    assert _quantize_duty_for_gpio(0.0) == 0.0
    monkeypatch.setattr(cfg, "PWM_DUTY_QUANTUM", 0.0)
    assert _quantize_duty_for_gpio(3.33) == pytest.approx(3.33)
