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
    ch_status = {i: "OPEN" for i in range(cfg.NUM_CHANNELS)}

    log = DataLogger()
    snap = log.record(
        readings,
        False,
        [],
        duties,
        False,
        ch_status,
        sim_time="12:00",
        ref_raw_mv=200.0,
        ref_hw_ok=True,
        ref_hint="",
        ref_hw_message="sim",
        ref_baseline_set=False,
    )
    assert "channels" in snap and "total_power_w" in snap
    assert "ts" in snap and isinstance(snap["ts"], str)
    assert "ts_unix" in snap and isinstance(snap["ts_unix"], (int, float))
    log.maybe_flush(force=True)
    log.close()

    latest = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert latest["sim_time"] == "12:00"
    assert len(latest["channels"]) == cfg.NUM_CHANNELS
    assert latest["wet_channels"] == 0
    assert latest["ref_raw_mv"] == 200.0
    assert latest["ref_hw_ok"] is True
    assert "ref_hw_message" in latest
    assert "ref_baseline_set" in latest
    assert latest["ref_shift_mv"] is None
    assert latest["ref_status"] == "N/A"
    assert latest["ref_baseline_set"] is False
    assert latest["ref_hw_message"] == "sim"
    assert "total_power_w" in latest
    assert latest.get("system_alerts") == []
    ch0 = latest["channels"]["0"]
    assert ch0.get("reading_ok") is True
    assert "power_w" in ch0 and "z_delta_ohm" in ch0
    assert "coulombs_today_c" in ch0
    assert "energy_today_j" in ch0
    assert "surface_hint" in ch0
    assert "sigma_proxy_s" in ch0
    assert "fqi_smooth_s" in ch0
    assert "cross" in latest and "i_cv" in latest["cross"]
    assert ch0.get("sensor_error") in ("", None)
    assert latest["channels"]["0"].get("target_ma") == pytest.approx(
        float(cfg.TARGET_MA)
    )

    conn = sqlite3.connect(str(tmp_path / cfg.SQLITE_DB_NAME))
    try:
        n = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        assert n == 1
        row = conn.execute(
            "SELECT ch1_state, wet, ch1_impedance_ohm, ch1_cell_voltage_v FROM readings LIMIT 1"
        ).fetchone()
        assert row[0] == "OPEN"
        assert row[1] == 0
        assert row[2] is not None and float(row[2]) > 1000  # high Z when ~0.1 mA
        assert row[3] is not None
        row_pw = conn.execute(
            "SELECT total_power_w, ch1_power_w, ch1_z_delta_ohm, ch1_target_ma "
            "FROM readings LIMIT 1"
        ).fetchone()
        assert row_pw is not None
        assert row_pw[0] is not None and float(row_pw[0]) >= 0
        assert row_pw[1] is not None
        assert float(row_pw[3]) == pytest.approx(float(cfg.TARGET_MA))
        cr = conn.execute(
            "SELECT cross_i_cv, cross_z_cv FROM readings LIMIT 1"
        ).fetchone()
        assert cr is not None
    finally:
        conn.close()

    csv_files = list(tmp_path.glob(f"{cfg.LOG_BASE_NAME}_*.csv"))
    assert len(csv_files) == 1
    text = csv_files[0].read_text(encoding="utf-8")
    assert "ch1_state" in text
    assert "OPEN" in text


def test_sensor_error_and_system_alerts_when_read_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = _sample_readings()
    readings[0] = {"ok": False, "error": "INA219 NACK at 0x40"}
    duties = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
    ch_status = {i: "OPEN" for i in range(cfg.NUM_CHANNELS)}
    log = DataLogger()
    log.record(
        readings,
        False,
        ["CH2 OC"],
        duties,
        False,
        ch_status,
        ref_hw_ok=False,
        ref_hw_message="ADS1115 offline",
    )
    log.close()

    latest = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert latest["channels"]["0"]["sensor_error"] == "INA219 NACK at 0x40"
    assert latest["channels"]["1"].get("sensor_error") in ("", None)
    sa = latest["system_alerts"]
    assert "CH2 OC" in sa
    assert any("Anode 1 (idx 0) sensor:" in x and "NACK" in x for x in sa)
    assert any(x.startswith("Reference:") and "ADS1115" in x for x in sa)


def test_record_channel_targets_override_for_health_and_json(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)
    monkeypatch.setattr(cfg, "TARGET_MA", 0.5)
    from logger import DataLogger

    readings = {
        i: {"ok": True, "current": 2.0 if i == 0 else 0.1, "bus_v": 5.0}
        for i in range(cfg.NUM_CHANNELS)
    }
    duties = {i: 10.0 for i in range(cfg.NUM_CHANNELS)}
    st = {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)}
    log = DataLogger()
    log.record(
        readings,
        True,
        [],
        duties,
        False,
        st,
        channel_targets={0: 1.0, 1: 0.5, 2: 0.5, 3: 0.5},
    )
    log.close()
    latest = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert latest["channels"]["0"]["target_ma"] == pytest.approx(1.0)
    assert latest["channels"]["0"]["status"] == "HIGH"


def test_recovery_touch_latest_merges_alert(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)
    from logger import DataLogger

    log = DataLogger()
    log.recovery_touch_latest("first banner")
    t0 = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))[
        "ts_unix"
    ]
    log.recovery_touch_latest("second banner", ValueError("x"))
    log.close()
    latest = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert "first banner" in latest["system_alerts"]
    assert any("second banner" in x for x in latest["system_alerts"])
    assert "tick_writer_error" in latest
    assert latest["ts_unix"] >= t0
    assert "ts" in latest and len(str(latest["ts"])) >= 10


def test_recovery_clears_stale_channel_numerics(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a good record(), recovery must not leave old mA/reading_ok as if current."""
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)
    from logger import DataLogger

    readings = {
        i: {"ok": True, "current": 2.0, "bus_v": 5.0} for i in range(cfg.NUM_CHANNELS)
    }
    duties = {i: 10.0 for i in range(cfg.NUM_CHANNELS)}
    st = {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)}
    log = DataLogger()
    log.record(readings, True, [], duties, False, st, channel_targets={0: 1.0, 1: 0.5, 2: 0.5, 3: 0.5})
    good = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    good_ts = good["ts"]
    good_tsu = good["ts_unix"]
    assert good["total_ma"] > 0.1
    log.recovery_touch_latest("write failed in tick")
    log.close()
    latest = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert latest.get("telemetry_incomplete") is True
    assert latest.get("last_valid_channel_snapshot_ts") == good_ts
    assert float(latest.get("last_valid_channel_snapshot_ts_unix", 0.0)) == float(good_tsu)
    assert latest.get("total_ma") == 0.0
    for i in range(cfg.NUM_CHANNELS):
        ch = latest["channels"][str(i)]
        assert ch.get("ma") == 0.0
        assert ch.get("reading_ok") is False
    # Second recovery: last_valid should still be the good record, not a recovery-time ts
    log2 = DataLogger()
    log2.recovery_touch_latest("failed again")
    log2.close()
    again = json.loads((tmp_path / cfg.LATEST_JSON_NAME).read_text(encoding="utf-8"))
    assert again.get("last_valid_channel_snapshot_ts") == good_ts
    assert any("failed again" in str(x) for x in (again.get("system_alerts") or []))


def test_cooling_cycle_row_on_band_exit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    log = DataLogger()
    t0 = 1_000_000.0
    st = {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)}
    log.feed_cooling_cycle(
        in_band=True, ts_unix=t0, dt_s=0.5, ch_status=st, temp_f=72.0
    )
    log.feed_cooling_cycle(
        in_band=True, ts_unix=t0 + 0.5, dt_s=0.5, ch_status=st, temp_f=71.0
    )
    log.feed_cooling_cycle(
        in_band=False, ts_unix=t0 + 2.0, dt_s=0.5, ch_status=st, temp_f=85.0
    )
    log.close()

    conn = sqlite3.connect(str(tmp_path / cfg.SQLITE_DB_NAME))
    try:
        n = conn.execute("SELECT COUNT(*) FROM cooling_cycles").fetchone()[0]
        assert n == 1
        row = conn.execute(
            "SELECT duration_s, ch1_protect_s, avg_temp_f FROM cooling_cycles LIMIT 1"
        ).fetchone()
        assert row[0] == pytest.approx(2.0, rel=0, abs=0.05)
        assert float(row[1]) == pytest.approx(1.0, rel=0, abs=0.05)
        assert row[2] is not None
    finally:
        conn.close()


def test_fault_log_signature_dedupe(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = _sample_readings()
    duties = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
    ch_status = {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)}
    from channel_labels import anode_hw_label

    faults = [f"{anode_hw_label(0)} OVERCURRENT: 3.0000 mA"]

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
    log.record(
        readings, False, [], duties, False,
        {i: "OPEN" for i in range(cfg.NUM_CHANNELS)},
        ref_raw_mv=1.0, ref_hw_ok=True,
    )
    log.record(
        readings, True, [], duties, False,
        {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)},
        ref_raw_mv=1.0, ref_hw_ok=True,
    )
    log.record(
        readings, False, [], duties, False,
        {i: "OPEN" for i in range(cfg.NUM_CHANNELS)},
        ref_raw_mv=1.0, ref_hw_ok=True,
    )
    log.close()

    conn = sqlite3.connect(str(tmp_path / cfg.SQLITE_DB_NAME))
    try:
        n_sess = conn.execute(
            "SELECT COUNT(*) FROM wet_sessions WHERE ended_at IS NOT NULL"
        ).fetchone()[0]
        assert n_sess == cfg.NUM_CHANNELS
    finally:
        conn.close()
