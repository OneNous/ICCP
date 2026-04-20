"""INA219 idle-benign: errno-5 style glitches while PWM off are not hard faults."""

from __future__ import annotations

import json

import pytest

import config.settings as cfg
from control import Controller
from sensors import ina219_read_failure_expected_idle


def test_ina219_read_failure_expected_idle_errno5() -> None:
    assert ina219_read_failure_expected_idle(
        ok=False,
        error="OSError: [Errno 5] Input/output error",
        duty_pct=0.0,
        fsm_state="OPEN",
        current_ma=0.0,
        bus_v=0.0,
    )
    assert not ina219_read_failure_expected_idle(
        ok=False,
        error="INA219 NACK at 0x40",
        duty_pct=0.0,
        fsm_state="OPEN",
        current_ma=0.0,
        bus_v=0.0,
    )
    assert not ina219_read_failure_expected_idle(
        ok=False,
        error="OSError: [Errno 5] Input/output error",
        duty_pct=0.0,
        fsm_state="REGULATE",
        current_ma=0.0,
        bus_v=0.0,
    )


def test_failsafe_suppressed_all_channels_idle_errno5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "INA219_FAILSAFE_ALL_OFF", True, raising=False)
    ctrl = Controller()
    bad = {
        "ok": False,
        "error": "OSError: [Errno 5] Input/output error",
        "current": 0.0,
        "bus_v": 0.0,
    }
    readings = {0: dict(bad), 1: dict(bad), 2: dict(bad), 3: dict(bad)}
    faults, latched = ctrl.update(readings)
    assert latched is False
    assert not any("READ ERROR" in f for f in faults)
    assert not any("forced OPEN" in f for f in faults)


def test_logger_idle_errno5_shows_off_not_sensor_alert(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = {
        i: {
            "ok": False,
            "error": "OSError: [Errno 5] Input/output error",
            "current": 0.0,
            "bus_v": 0.0,
        }
        for i in range(cfg.NUM_CHANNELS)
    }
    duties = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
    ch_status = {i: "OPEN" for i in range(cfg.NUM_CHANNELS)}
    log = DataLogger()
    log.record(readings, False, [], duties, False, ch_status)
    log.close()

    latest = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert latest["channels"]["0"]["status"] == "OFF"
    assert latest["channels"]["0"].get("sensor_error") in ("", None)
    assert latest["channels"]["0"]["reading_ok"] is True
    assert not any("CH1 sensor:" in x for x in latest.get("system_alerts", []))
