"""Bus-scoped INA219 fail-safe — docs/iccp-requirements.md §4.3 (Decision Q8).

Non-bus read errors must stay per-channel so a single flaky INA219 cannot force
every siblings' PWM to 0%. Bus-level errno-5 / errno-121 failures across
``INA219_FAILSAFE_MIN_BUS_CHANNELS`` channels escalate to the aggregate
`all_off` path that zeros the entire bus and appends the "forced OPEN" banner.
"""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import Controller, _bus_level_read_failure


def test_bus_level_classifier_recognizes_errno_5_and_121() -> None:
    assert _bus_level_read_failure({"ok": False, "errno": 5}) is True
    assert _bus_level_read_failure({"ok": False, "errno": 121}) is True
    # String-only is enough when the kernel bubbled up the usual OSError repr.
    assert _bus_level_read_failure(
        {"ok": False, "error": "OSError: [Errno 5] Input/output error"}
    ) is True
    assert _bus_level_read_failure(
        {"ok": False, "error": "OSError: [Errno 121] Remote I/O error"}
    ) is True
    # Non-bus-scoped NACKs / decode errors stay per-channel.
    assert _bus_level_read_failure({"ok": False, "errno": 6}) is False
    assert _bus_level_read_failure({"ok": False, "error": "decode mismatch"}) is False
    assert _bus_level_read_failure({"ok": True, "current": 0.1}) is False


def test_single_non_bus_error_does_not_force_siblings_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One channel with a non-errno-5 error must not zero the other three."""
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_ALL_OFF", True, raising=False)
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_MIN_BUS_CHANNELS", 2, raising=False)
    ctrl = Controller()
    ok = {"ok": True, "current": 0.4, "bus_v": 5.0}
    readings = {
        0: ok,
        1: ok,
        2: {"ok": False, "error": "decode mismatch", "bus_v": 0.0, "shunt_mv": 0.0},
        3: ok,
    }
    faults, _ = ctrl.update(readings)
    # Per-channel fault is reported, but the aggregate banner must not appear.
    assert any("READ ERROR" in f and "Anode 3" in f for f in faults)
    assert not any("forced OPEN" in f for f in faults)
    assert ctrl.duties()[2] == 0.0


def test_single_bus_error_below_threshold_stays_per_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the default threshold of 2, a single errno-5 is still per-channel."""
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_ALL_OFF", True, raising=False)
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_MIN_BUS_CHANNELS", 2, raising=False)
    ctrl = Controller()
    ok = {"ok": True, "current": 0.4, "bus_v": 5.0}
    readings = {
        0: ok,
        1: ok,
        2: {"ok": False, "error": "OSError: [Errno 5] I/O", "bus_v": 0.0, "shunt_mv": 0.0},
        3: ok,
    }
    faults, _ = ctrl.update(readings)
    assert not any("forced OPEN" in f for f in faults)


def test_bus_errors_above_threshold_trigger_all_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_ALL_OFF", True, raising=False)
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_MIN_BUS_CHANNELS", 2, raising=False)
    ctrl = Controller()
    bus_err = {
        "ok": False,
        "error": "OSError: [Errno 5] Input/output error",
        "bus_v": 0.0,
        "shunt_mv": 0.0,
    }
    readings = {
        0: {"ok": True, "current": 0.4, "bus_v": 5.0},
        1: {"ok": True, "current": 0.4, "bus_v": 5.0},
        2: bus_err,
        3: bus_err,
    }
    faults, _ = ctrl.update(readings)
    assert any("forced OPEN" in f for f in faults)
    for ch in range(cfg.NUM_CHANNELS):
        assert ctrl.duties()[ch] == 0.0
