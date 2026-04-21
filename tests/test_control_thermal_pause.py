"""Thermal pause: PWM held off while read errors and FAULT recovery still run."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import Controller


def test_thermal_pause_reports_read_errors() -> None:
    ctrl = Controller()
    ctrl.set_thermal_pause(True)
    readings: dict[int, dict] = {
        i: {"ok": True, "current": 0.5, "bus_v": 5.0} for i in range(cfg.NUM_CHANNELS)
    }
    readings[0] = {"ok": False, "error": "I2C NACK"}
    faults, latched = ctrl.update(readings)
    assert any("READ ERROR" in f and "NACK" in f and "Anode 1" in f and "idx 0" in f for f in faults)
    assert latched is False
    assert all(ctrl.duties().get(i, -1) == 0.0 for i in range(cfg.NUM_CHANNELS))


def test_thermal_pause_cleared_resumes_normal_update() -> None:
    ctrl = Controller()
    ctrl.set_thermal_pause(True)
    ok = {i: {"ok": True, "current": 0.5, "bus_v": 5.0} for i in range(cfg.NUM_CHANNELS)}
    ctrl.update(ok)
    ctrl.set_thermal_pause(False)
    faults, latched = ctrl.update(ok)
    assert isinstance(faults, list)
    assert latched is False
