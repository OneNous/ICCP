"""DataLogger: SQLite, latest.json, CSV, fault dedupe."""

from __future__ import annotations

import json
import sqlite3

import pytest

import config.settings as cfg


def _sample_readings() -> dict[int, dict]:
    return {
        i: {"ok": True, "current": 0.1 * (i + 1), "bus_v": 11.5}
        for i in range(cfg.NUM_CHANNELS)
    }


def test_logger_writes_sqlite_latest_json_and_csv(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = _sample_readings()
    duties = {i: float(i * 5) for i in range(cfg.NUM_CHANNELS)}
    ch_status = {i: "DORMANT" for i in range(cfg.NUM_CHANNELS)}

    log = DataLogger()
    log.record(readings, False, [], duties, False, ch_status, sim_time="12:00")
    log.maybe_flush(force=True)
    log.close()

    latest = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert latest["sim_time"] == "12:00"
    assert len(latest["channels"]) == cfg.NUM_CHANNELS
    assert latest["wet_channels"] == 0

    conn = sqlite3.connect(str(tmp_path / cfg.SQLITE_DB_NAME))
    try:
        n = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        assert n == 1
        row = conn.execute(
            "SELECT ch1_state, wet, ch1_impedance_ohm, ch1_cell_voltage_v FROM readings LIMIT 1"
        ).fetchone()
        assert row[0] == "DORMANT"
        assert row[1] == 0
        assert row[2] is not None and float(row[2]) > 1000  # high Z when ~0.1 mA
        assert row[3] is not None
    finally:
        conn.close()

    csv_files = list(tmp_path.glob(f"{cfg.LOG_BASE_NAME}_*.csv"))
    assert len(csv_files) == 1
    text = csv_files[0].read_text(encoding="utf-8")
    assert "ch1_state" in text
    assert "DORMANT" in text


def test_fault_log_signature_dedupe(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = _sample_readings()
    duties = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
    ch_status = {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)}
    faults = ["CH1 OVERCURRENT: 3.0000 mA"]

    log = DataLogger()
    log.record(readings, True, faults, duties, True, ch_status)
    log.record(readings, True, faults, duties, True, ch_status)
    log.close()

    fault_text = (tmp_path / cfg.FAULT_LOG_NAME).read_text(encoding="utf-8")
    assert fault_text.count("FAULT") == 1


def test_wet_session_row_on_protecting_cycle(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = _sample_readings()
    duties = {i: 10.0 for i in range(cfg.NUM_CHANNELS)}

    log = DataLogger()
    log.record(readings, False, [], duties, False, {i: "DORMANT" for i in range(5)})
    log.record(readings, True, [], duties, False, {i: "PROTECTING" for i in range(5)})
    log.record(readings, False, [], duties, False, {i: "DORMANT" for i in range(5)})
    log.close()

    conn = sqlite3.connect(str(tmp_path / cfg.SQLITE_DB_NAME))
    try:
        n_sess = conn.execute(
            "SELECT COUNT(*) FROM wet_sessions WHERE ended_at IS NOT NULL"
        ).fetchone()[0]
        assert n_sess == cfg.NUM_CHANNELS
    finally:
        conn.close()
