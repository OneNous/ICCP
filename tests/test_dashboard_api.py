"""Flask dashboard JSON routes against a temp DB populated by DataLogger."""

from __future__ import annotations

import json

import pytest

import config.settings as cfg


@pytest.fixture()
def log_and_dashboard_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = {
        i: {"ok": True, "current": 0.2 * (i + 1), "bus_v": 11.5}
        for i in range(cfg.NUM_CHANNELS)
    }
    duties = {i: 5.0 for i in range(cfg.NUM_CHANNELS)}

    log = DataLogger()
    log.record(
        readings,
        False,
        [],
        duties,
        False,
        {i: "DORMANT" for i in range(5)},
        ref_shift_mv=10.0,
        ref_status="OK",
        temp_f=72.5,
    )
    log.record(
        readings,
        True,
        [],
        duties,
        False,
        {i: "PROTECTING" for i in range(5)},
        ref_shift_mv=12.0,
        ref_status="OK",
        temp_f=73.0,
    )
    log.record(
        readings,
        False,
        [],
        duties,
        False,
        {i: "DORMANT" for i in range(5)},
        ref_shift_mv=11.0,
        ref_status="UNDER",
        temp_f=72.0,
    )
    log.close()

    import dashboard

    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_api_stats_includes_ref_and_temp(log_and_dashboard_client) -> None:
    c = log_and_dashboard_client
    r = c.get("/api/stats")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "stats" in data
    assert len(data["stats"]) == cfg.NUM_CHANNELS
    for s in data["stats"]:
        assert "ref_shift_mv" in s
        assert "temp_f" in s
        assert s["ref_shift_mv"] is not None
        assert s["temp_f"] is not None


def test_api_daily_and_sessions(log_and_dashboard_client) -> None:
    c = log_and_dashboard_client
    r = c.get("/api/daily")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["date"]
    assert "channels" in data

    r2 = c.get("/api/sessions?hours=24&limit=100")
    assert r2.status_code == 200
    sess = json.loads(r2.data)["sessions"]
    assert len(sess) == cfg.NUM_CHANNELS
    assert all("avg_impedance_ohm" in s for s in sess)
