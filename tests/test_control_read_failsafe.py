"""INA219_FAILSAFE_ALL_OFF: any read error forces every non-FAULT channel to 0% PWM."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import Controller


def test_any_read_error_forces_all_channels_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_ALL_OFF", True, raising=False)
    ctrl = Controller()
    readings: dict[int, dict] = {
        0: {"ok": True, "current": 1.4, "bus_v": 4.9},
        1: {"ok": True, "current": 1.4, "bus_v": 4.9},
        2: {"ok": False, "error": "OSError: [Errno 5] Input/output error", "bus_v": 0.0, "shunt_mv": 0.0},
        3: {"ok": False, "error": "OSError: [Errno 5] Input/output error", "bus_v": 0.0, "shunt_mv": 0.0},
    }
    faults, latched = ctrl.update(readings)
    assert latched is False
    assert all(ctrl.duties().get(i, -1) == 0.0 for i in range(cfg.NUM_CHANNELS))
    assert any("CH3 READ ERROR" in f for f in faults)
    assert any("forced OPEN" in f and "CH 3" in f for f in faults)


def test_failsafe_disabled_skips_aggregate_hold_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_ALL_OFF", False, raising=False)
    ctrl = Controller()
    ok = {"ok": True, "current": 0.4, "bus_v": 5.0}
    bad = {"ok": False, "error": "EIO", "bus_v": 0.0, "shunt_mv": 0.0}
    readings = {0: ok, 1: ok, 2: bad, 3: ok}
    faults, _ = ctrl.update(readings)
    assert any("CH3 READ ERROR" in f for f in faults)
    assert not any("forced OPEN" in f for f in faults)
    assert ctrl.duties()[2] == 0.0
