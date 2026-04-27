"""SQLite readings batching (reduces SD write amplification on Pi)."""

from __future__ import annotations

import sqlite3

import pytest

import config.settings as cfg
from logger import DataLogger


def _count_readings(db_path) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return int(con.execute("SELECT COUNT(*) FROM readings").fetchone()[0])
    finally:
        con.close()


def _minimal_record(log: DataLogger) -> None:
    readings = {
        i: {
            "ok": True,
            "current": 0.1,
            "bus_v": 5.0,
            "shunt_mv": 0.01,
            "shunt_v": 0.0,
            "v_shunt_uv": 0.0,
        }
        for i in range(cfg.NUM_CHANNELS)
    }
    duties = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
    ch = {i: "REGULATE" for i in range(cfg.NUM_CHANNELS)}
    log.record(readings, False, [], duties, False, ch)


def test_readings_batched_by_row_count(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_FLUSH_INTERVAL_S", 3600.0)
    monkeypatch.setattr(cfg, "SQLITE_FLUSH_MAX_ROWS", 3)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 10_000_000)

    log = DataLogger()
    for _ in range(2):
        _minimal_record(log)
    assert _count_readings(tmp_path / cfg.SQLITE_DB_NAME) == 0
    _minimal_record(log)
    assert _count_readings(tmp_path / cfg.SQLITE_DB_NAME) == 3
    log.close()


def test_readings_unbatched_when_flags_zero(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_FLUSH_INTERVAL_S", 0.0)
    monkeypatch.setattr(cfg, "SQLITE_FLUSH_MAX_ROWS", 0)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 10_000_000)
    log = DataLogger()
    _minimal_record(log)
    log.close()
    assert _count_readings(tmp_path / cfg.SQLITE_DB_NAME) == 1
