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
        {i: "OPEN" for i in range(cfg.NUM_CHANNELS)},
        ref_shift_mv=10.0,
        ref_status="OK",
        temp_f=72.5,
        ref_raw_mv=200.0,
        ref_hw_ok=True,
        ref_hint="",
        ref_hw_message="sim",
        ref_baseline_set=True,
    )
    log.record(
        readings,
        True,
        [],
        duties,
        False,
        {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)},
        ref_shift_mv=12.0,
        ref_status="OK",
        temp_f=73.0,
        ref_raw_mv=205.0,
        ref_hw_ok=True,
        ref_hint="",
        ref_hw_message="sim",
        ref_baseline_set=True,
    )
    log.record(
        readings,
        False,
        [],
        duties,
        False,
        {i: "OPEN" for i in range(cfg.NUM_CHANNELS)},
        ref_shift_mv=11.0,
        ref_status="UNDER",
        temp_f=72.0,
        ref_raw_mv=203.0,
        ref_hw_ok=True,
        ref_hint="",
        ref_hw_message="sim",
        ref_baseline_set=True,
    )
    log.close()

    import dashboard

    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_api_history_includes_avg_target_ma(log_and_dashboard_client) -> None:
    c = log_and_dashboard_client
    r = c.get("/api/history?minutes=60&metric=ma")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert "avg_target_ma" in data
    assert len(data["avg_target_ma"]) == len(data["labels"])
    assert all(isinstance(x, (int, float)) for x in data["avg_target_ma"])


def test_api_history_invalid_minutes_defaults(log_and_dashboard_client) -> None:
    import dashboard

    c = log_and_dashboard_client
    r = c.get("/api/history?minutes=notanumber&metric=ma")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["minutes"] == dashboard._HISTORY_MINUTES_DEFAULT


def test_api_history_minutes_clamped_and_non_negative(
    log_and_dashboard_client,
) -> None:
    import dashboard

    c = log_and_dashboard_client
    r = c.get(f"/api/history?minutes={10**9}&metric=ma")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data["minutes"] == dashboard._HISTORY_MINUTES_MAX

    r2 = c.get("/api/history?minutes=-500&metric=ma")
    assert r2.status_code == 200
    data2 = json.loads(r2.data)
    assert data2["minutes"] == 1


def test_api_live_includes_feed_envelope(log_and_dashboard_client) -> None:
    c = log_and_dashboard_client
    r = c.get("/api/live")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data.get("error") is None
    assert "feed_age_s" in data
    assert "feed_stale_threshold_s" in data
    assert "json_payload_age_s" in data
    assert "feed_stale_reasons" in data and isinstance(data["feed_stale_reasons"], list)
    assert data.get("feed_ok") is True
    assert data.get("feed_trust_channel_metrics") is True
    assert "telemetry_seq" in data
    assert isinstance(data.get("writer_pid"), int)
    assert data.get("telemetry_incomplete") in (None, False)
    assert "sample_interval_s" in data
    assert r.headers.get("Cache-Control") == "no-store"
    assert data["channels"]["0"].get("reading_ok") is True
    assert "target_ma" in data
    assert isinstance(data["target_ma"], (int, float))
    assert data.get("target_ma_avg_live") is not None
    assert isinstance(data["target_ma_avg_live"], (int, float))
    assert "target_ma" in data["channels"]["0"]
    assert isinstance(data.get("system_alerts"), list)
    tp = data.get("telemetry_paths")
    assert isinstance(tp, dict)
    assert "latest_json" in tp and "log_dir" in tp and "log_dir_source" in tp
    assert tp["latest_json"].endswith("latest.json")


def test_api_live_telemetry_incomplete_untrusted(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """recovery_touch_latest keeps mtime fresh but feed_trust is false when incomplete."""
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    from logger import DataLogger

    log = DataLogger()
    readings = {i: {"ok": True, "current": 0.1, "bus_v": 11.5} for i in range(cfg.NUM_CHANNELS)}
    log.record(
        readings,
        False,
        [],
        {i: 5.0 for i in range(cfg.NUM_CHANNELS)},
        False,
        {i: "OPEN" for i in range(cfg.NUM_CHANNELS)},
    )
    log.recovery_touch_latest("simulated tick failure", exc=RuntimeError("x"))
    log.close()

    import dashboard

    # ``dashboard`` may already be imported in this process with a different LATEST_PATH.
    monkeypatch.setattr(dashboard, "LATEST_PATH", tmp_path / cfg.LATEST_JSON_NAME)
    dashboard.app.config["TESTING"] = True
    c = dashboard.app.test_client()
    r = c.get("/api/live")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data.get("telemetry_incomplete") is True
    assert data.get("feed_trust_channel_metrics") is False
    assert "telemetry_incomplete" in (data.get("feed_stale_reasons") or [])
    assert "telemetry_seq" not in data or data.get("telemetry_seq") is None


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


def test_api_diagnostic_404(log_and_dashboard_client) -> None:
    c = log_and_dashboard_client
    r = c.get("/api/diagnostic")
    assert r.status_code == 404


def test_api_diagnostic_ok(log_and_dashboard_client, tmp_path, monkeypatch) -> None:
    import dashboard

    p = tmp_path / "snap.json"
    p.write_text('{"ok": true, "probe": 1}', encoding="utf-8")
    monkeypatch.setattr(dashboard, "DIAGNOSTIC_SNAPSHOT_PATH", p)
    c = log_and_dashboard_client
    r = c.get("/api/diagnostic")
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data.get("probe") == 1


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


def test_static_geist_font_served(log_and_dashboard_client) -> None:
    c = log_and_dashboard_client
    r = c.get("/static/fonts/Geist-Variable.woff2")
    assert r.status_code == 200
    assert r.mimetype in ("font/woff2", "application/font-woff2", "application/octet-stream")
    assert len(r.data) > 10_000
