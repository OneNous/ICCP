"""
CoilShield — data logger.

Four sinks per record() call:
  1. coilshield.db (SQLite WAL: readings + wet_sessions + daily_totals)
  2. latest.json (atomic replace every tick)
  3. iccp_faults.log (deduped fault signature + fsync)
  4. iccp_YYYY-MM-DD.csv (buffered CSV)

readings: per-tick telemetry including chN_impedance_ohm, chN_cell_voltage_v,
  chN_power_w (bus V × I as W), chN_z_delta_ohm (ΔZ vs prior tick), total_power_w.

cooling_cycles: one row per completed ICCP temperature band segment (same window as
  temp.in_operating_range): duration_s, avg_temp_f, chN_protect_s (PROTECTING dwell
  within that segment). Correlates wet dwell with coil cooling cycles.
  latest.json reference keys (every tick): ref_raw_mv, ref_shift_mv, ref_status, ref_hw_ok,
  ref_hw_message, ref_hint, ref_baseline_set (SQLite/CSV also carry raw/hw_ok/hint; hw_message and baseline_set are CSV + JSON).

wet_sessions: one row per PROTECTING episode (open until exit PROTECTING/FAULT).
  Session opens on any transition into PROTECTING.

daily_totals: per-calendar-day chN_ma_s (mA·s), chN_wet_s (seconds PROTECTING),
  chN_energy_j (∫ V·I dt in joules while readings are valid).

Per-channel derived telemetry (latest.json + CSV; see also readings.cross_*):
  σ_proxy = 1/Z, smoothed FQI ≈ EMA(I/V), z_std_ohm, z_rate_ohm_s, dV_dI_ohm,
  efficiency_ma_per_pct, surface_hint (DRY / FILM_FORMING / STABLE_WET / SATURATED),
  energy_today_j. System cross-channel: i_cv, z_cv (pstdev/mean when ≥2 channels OK).

CSV vs SQLite/latest.json:
  SQLite and latest.json are written every control tick (near real-time).
  CSV is buffered and flushed on LOG_INTERVAL_S and on fault-signature transitions (eventually consistent).

Dashboard SQL / column names MUST stay in sync with dashboard.py.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import statistics
import threading
import time
from collections import deque
from datetime import date, timedelta
from pathlib import Path

import config.settings as cfg


def _cell_impedance_ohm(bus_v: float, current_ma: float) -> float:
    """DC-ish ratio: V / I(A). High Ω when dry / negligible current."""
    i_a = max(
        current_ma / 1000.0,
        float(getattr(cfg, "Z_COMPUTE_I_A_MIN", 1e-6)),
    )
    return round(bus_v / i_a, 2)


def _cell_voltage_v(bus_v: float, duty_pct: float) -> float:
    """PWM-scaled estimate: bus × duty fraction."""
    return round(bus_v * (duty_pct / 100.0), 4)


def _dc_power_w(bus_v: float, current_ma: float) -> float:
    """Bus-side DC power estimate P = V × I (I in A)."""
    return round(float(bus_v) * (float(current_ma) / 1000.0), 6)


def _sigma_proxy_s(z_ohm: float) -> float:
    """Conductivity proxy σ ≈ 1/Z (Siemens). Z floored for numerical stability."""
    z = max(float(z_ohm), 1.0)
    return round(1.0 / z, 9)


def _conductance_i_over_v_s(ma_ma: float, bus_v: float) -> float:
    """I/V in Siemens (same dimensions as 1/Z for this lumped path)."""
    return (float(ma_ma) / 1000.0) / max(float(bus_v), 1e-6)


def _surface_hint(
    ma: float,
    z_ohm: float,
    z_std: float | None,
    cfg,
) -> str:
    """
    Emergent film / surface label for analytics (not the control FSM).
    Uses the same order-of-magnitude thresholds as wet-path tuning in cfg.
    """
    dry_ma = float(cfg.CHANNEL_DRY_MA)
    max_z = float(cfg.MAX_EFFECTIVE_OHMS)
    min_z = float(cfg.MIN_EFFECTIVE_OHMS)
    if ma < dry_ma * 0.99 or z_ohm >= max_z * 0.92:
        return "DRY"
    if z_ohm < max(min_z * 1.5, 2500.0) and ma > dry_ma * 4:
        return "SATURATED"
    noise = z_std if z_std is not None else 0.0
    rel_noise = noise / max(z_ohm, 1.0)
    if noise > 800.0 or rel_noise > 0.12:
        return "FILM_FORMING"
    if 2500.0 <= z_ohm <= max_z * 0.85 and ma >= float(cfg.CHANNEL_CONDUCTIVE_MA) * 0.5:
        return "STABLE_WET"
    return "FILM_FORMING"


def _coefficient_of_variation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = statistics.mean(values)
    if abs(m) < 1e-9:
        return None
    return round(statistics.pstdev(values) / abs(m), 6)


def _channel_health(
    ok: bool,
    state: str,
    ma: float,
) -> str:
    """UI / DB status column: OK, LOW, HIGH, ERR, DRY."""
    if not ok:
        return "ERR"
    if state in ("DRY", "DORMANT", "PROBING"):
        return "DRY"
    if state == "WEAK_WET":
        return "LOW"
    if ma < cfg.TARGET_MA * 0.7:
        return "LOW"
    if ma > cfg.TARGET_MA * 1.5:
        return "HIGH"
    return "OK"


def _atomic_write_same_dir(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
        raise


def _readings_column_names(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("PRAGMA table_info(readings)")
    return {row[1] for row in cur.fetchall()}


def _daily_totals_column_names(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("PRAGMA table_info(daily_totals)")
    return {row[1] for row in cur.fetchall()}


class DataLogger:
    def __init__(self) -> None:
        cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._db_path = cfg.LOG_DIR / cfg.SQLITE_DB_NAME
        self._latest_path = cfg.LOG_DIR / cfg.LATEST_JSON_NAME
        self._fault_log_path = cfg.LOG_DIR / cfg.FAULT_LOG_NAME

        self._csv_path = self._daily_csv_path()
        self._csv_rows: list[dict[str, object]] = []
        self._last_flush = time.monotonic()
        self._csv_headers_written = (
            self._csv_path.exists() and self._csv_path.stat().st_size > 0
        )

        self._fault_signature: tuple[str, ...] | None = None
        self._db_lock = threading.Lock()
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA temp_store=MEMORY")
        self._init_schema()
        self._purge_old_rows()
        self._insert_count = 0

        self._prev_fsm: dict[int, str] = {i: "DRY" for i in range(cfg.NUM_CHANNELS)}
        self._wet_active: dict[int, dict] = {}
        self._prev_z_ohm: dict[int, float | None] = {
            i: None for i in range(cfg.NUM_CHANNELS)
        }
        zwin = max(4, int(getattr(cfg, "Z_STATS_WINDOW", 16)))
        self._z_stat_window: dict[int, deque[float]] = {
            i: deque(maxlen=zwin) for i in range(cfg.NUM_CHANNELS)
        }
        self._fqi_ema: dict[int, float | None] = {
            i: None for i in range(cfg.NUM_CHANNELS)
        }
        self._prev_bus_v_elec: dict[int, float | None] = {
            i: None for i in range(cfg.NUM_CHANNELS)
        }
        self._prev_ma_elec: dict[int, float | None] = {
            i: None for i in range(cfg.NUM_CHANNELS)
        }
        self._prev_duty_elec: dict[int, float | None] = {
            i: None for i in range(cfg.NUM_CHANNELS)
        }

        self._cycle_active = False
        self._cycle_t0: float | None = None
        self._cycle_dwell: list[float] = [0.0] * cfg.NUM_CHANNELS
        self._cycle_temp_sum = 0.0
        self._cycle_temp_n = 0

        self._daily_date = time.strftime("%Y-%m-%d")
        self._daily_totals: dict[int, dict[str, float]] = {
            i: {"ma_s": 0.0, "wet_s": 0.0, "energy_j": 0.0}
            for i in range(cfg.NUM_CHANNELS)
        }
        self._load_today_daily_from_db()

    def _daily_csv_path(self) -> Path:
        return cfg.LOG_DIR / f"{cfg.LOG_BASE_NAME}_{time.strftime('%Y-%m-%d')}.csv"

    def _readings_ch_fragment(self) -> str:
        parts = []
        for i in range(1, cfg.NUM_CHANNELS + 1):
            parts.append(
                f"ch{i}_state TEXT, ch{i}_ma REAL, ch{i}_duty REAL, "
                f"ch{i}_bus_v REAL, ch{i}_status TEXT, "
                f"ch{i}_impedance_ohm REAL, ch{i}_cell_voltage_v REAL, "
                f"ch{i}_power_w REAL, ch{i}_z_delta_ohm REAL"
            )
        return ", ".join(parts)

    def _init_schema(self) -> None:
        ch_cols = self._readings_ch_fragment()
        with self._db_lock:
            self._db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS readings (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts              TEXT    NOT NULL,
                    ts_unix         REAL    NOT NULL,
                    wet             INTEGER,
                    fault_latched   INTEGER,
                    faults          TEXT,
                    {ch_cols},
                    total_ma        REAL,
                    supply_v_avg    REAL,
                    total_power_w   REAL,
                    cross_i_cv      REAL,
                    cross_z_cv      REAL,
                    ref_shift_mv    REAL,
                    ref_status      TEXT,
                    temp_f          REAL,
                    ref_raw_mv      REAL,
                    ref_hw_ok       INTEGER,
                    ref_hint        TEXT
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_ts_unix ON readings (ts_unix)"
            )

            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS wet_sessions (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel             INTEGER NOT NULL,
                    started_at          REAL NOT NULL,
                    ended_at            REAL,
                    duration_s          REAL,
                    total_ma_s          REAL,
                    avg_ma              REAL,
                    avg_impedance_ohm   REAL,
                    peak_ma             REAL
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_wet_sessions_started ON wet_sessions (started_at)"
            )

            prot = ", ".join(f"ch{i}_protect_s REAL" for i in range(1, cfg.NUM_CHANNELS + 1))
            self._db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS cooling_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    duration_s REAL NOT NULL,
                    avg_temp_f REAL,
                    {prot}
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_cooling_cycles_ended "
                "ON cooling_cycles (ended_at)"
            )

            ma_cols = ", ".join(f"ch{i}_ma_s REAL" for i in range(1, cfg.NUM_CHANNELS + 1))
            wet_cols = ", ".join(f"ch{i}_wet_s REAL" for i in range(1, cfg.NUM_CHANNELS + 1))
            ej_cols = ", ".join(f"ch{i}_energy_j REAL" for i in range(1, cfg.NUM_CHANNELS + 1))
            self._db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS daily_totals (
                    date TEXT PRIMARY KEY,
                    {ma_cols},
                    {wet_cols},
                    {ej_cols}
                )
                """
            )

            self._migrate_readings_columns()
            self._migrate_daily_totals_energy_columns()
            self._db.commit()

    def _migrate_readings_columns(self) -> None:
        cols = _readings_column_names(self._db)
        alters: list[str] = []
        for i in range(1, cfg.NUM_CHANNELS + 1):
            for name, decl in (
                (f"ch{i}_impedance_ohm", "REAL"),
                (f"ch{i}_cell_voltage_v", "REAL"),
                (f"ch{i}_power_w", "REAL"),
                (f"ch{i}_z_delta_ohm", "REAL"),
            ):
                if name not in cols:
                    alters.append(f"ALTER TABLE readings ADD COLUMN {name} {decl}")
        for name, decl in (
            ("total_power_w", "REAL"),
        ):
            if name not in cols:
                alters.append(f"ALTER TABLE readings ADD COLUMN {name} {decl}")
        for name, decl in (
            ("ref_shift_mv", "REAL"),
            ("ref_status", "TEXT"),
            ("temp_f", "REAL"),
            ("ref_raw_mv", "REAL"),
            ("ref_hw_ok", "INTEGER"),
            ("ref_hint", "TEXT"),
        ):
            if name not in cols:
                alters.append(f"ALTER TABLE readings ADD COLUMN {name} {decl}")
        for stmt in alters:
            self._db.execute(stmt)
        cross_cols = ("cross_i_cv", "cross_z_cv")
        for name in cross_cols:
            if name not in cols:
                self._db.execute(
                    f"ALTER TABLE readings ADD COLUMN {name} REAL"
                )

    def _migrate_daily_totals_energy_columns(self) -> None:
        cols = _daily_totals_column_names(self._db)
        for i in range(1, cfg.NUM_CHANNELS + 1):
            name = f"ch{i}_energy_j"
            if name not in cols:
                self._db.execute(f"ALTER TABLE daily_totals ADD COLUMN {name} REAL")

    def _purge_old_rows(self) -> None:
        cutoff = time.time() - cfg.TELEMETRY_RETENTION_DAYS * 86400
        cutoff_d = date.today() - timedelta(days=cfg.TELEMETRY_RETENTION_DAYS)
        cutoff_date_str = cutoff_d.isoformat()
        with self._db_lock:
            self._db.execute("DELETE FROM readings WHERE ts_unix < ?", (cutoff,))
            self._db.execute(
                "DELETE FROM wet_sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,),
            )
            self._db.execute(
                "DELETE FROM wet_sessions WHERE ended_at IS NULL AND started_at < ?",
                (cutoff,),
            )
            self._db.execute(
                "DELETE FROM daily_totals WHERE date < ?",
                (cutoff_date_str,),
            )
            self._db.execute(
                "DELETE FROM cooling_cycles WHERE ended_at < ?",
                (cutoff,),
            )
            self._db.commit()

    def _load_today_daily_from_db(self) -> None:
        today = self._daily_date
        q_m = ", ".join(f"ch{i}_ma_s" for i in range(1, cfg.NUM_CHANNELS + 1))
        q_w = ", ".join(f"ch{i}_wet_s" for i in range(1, cfg.NUM_CHANNELS + 1))
        q_e = ", ".join(f"ch{i}_energy_j" for i in range(1, cfg.NUM_CHANNELS + 1))
        with self._db_lock:
            row = self._db.execute(
                f"SELECT {q_m}, {q_w}, {q_e} FROM daily_totals WHERE date = ?",
                (today,),
            ).fetchone()
        if not row:
            return
        n = cfg.NUM_CHANNELS
        for i in range(n):
            self._daily_totals[i]["ma_s"] = float(row[i] or 0.0)
            self._daily_totals[i]["wet_s"] = float(row[i + n] or 0.0)
            self._daily_totals[i]["energy_j"] = float(row[i + 2 * n] or 0.0)

    def _roll_daily_calendar(self, ts_ymd: str) -> None:
        if ts_ymd == self._daily_date:
            return
        self._persist_daily_totals(self._daily_date)
        self._daily_date = ts_ymd
        for i in range(cfg.NUM_CHANNELS):
            self._daily_totals[i] = {"ma_s": 0.0, "wet_s": 0.0, "energy_j": 0.0}
        self._load_today_daily_from_db()

    def _persist_daily_totals(self, ymd: str) -> None:
        ma_parts = ", ".join(f"ch{i}_ma_s" for i in range(1, cfg.NUM_CHANNELS + 1))
        wet_parts = ", ".join(f"ch{i}_wet_s" for i in range(1, cfg.NUM_CHANNELS + 1))
        ej_parts = ", ".join(f"ch{i}_energy_j" for i in range(1, cfg.NUM_CHANNELS + 1))
        vals: list[object] = [ymd]
        for i in range(cfg.NUM_CHANNELS):
            vals.append(self._daily_totals[i]["ma_s"])
        for i in range(cfg.NUM_CHANNELS):
            vals.append(self._daily_totals[i]["wet_s"])
        for i in range(cfg.NUM_CHANNELS):
            vals.append(self._daily_totals[i]["energy_j"])
        placeholders = ", ".join(["?"] * len(vals))
        all_cols = "date, " + ma_parts + ", " + wet_parts + ", " + ej_parts
        col_list = (
            [f"ch{i}_ma_s" for i in range(1, cfg.NUM_CHANNELS + 1)]
            + [f"ch{i}_wet_s" for i in range(1, cfg.NUM_CHANNELS + 1)]
            + [f"ch{i}_energy_j" for i in range(1, cfg.NUM_CHANNELS + 1)]
        )
        updates = ", ".join(f"{c} = excluded.{c}" for c in col_list)
        sql = (
            f"INSERT INTO daily_totals ({all_cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}"
        )
        with self._db_lock:
            self._db.execute(sql, vals)
            self._db.commit()

    def record(
        self,
        readings: dict[int, dict],
        any_wet: bool,
        faults: list[str],
        duties: dict[int, float],
        fault_latched: bool,
        ch_status: dict[int, str] | None = None,
        sim_time: str | None = None,
        ref_shift_mv: float | None = None,
        ref_status: str | None = None,
        temp_f: float | None = None,
        ref_raw_mv: float | None = None,
        ref_hw_ok: bool | None = None,
        ref_hint: str | None = None,
        ref_hw_message: str | None = None,
        ref_baseline_set: bool | None = None,
    ) -> dict[str, object]:
        wet = any_wet
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        ts_unix = time.time()
        ts_ymd = ts[:10]

        self._roll_daily_calendar(ts_ymd)

        dt_s = cfg.SAMPLE_INTERVAL_S
        channels: dict[int, dict] = {}
        for i in range(cfg.NUM_CHANNELS):
            r = readings.get(i, {})
            ok = bool(r.get("ok"))
            if ok:
                try:
                    ma = round(float(r["current"]), 4)
                    bus_v = round(float(r["bus_v"]), 3)
                except (TypeError, ValueError):
                    ok = False
                    ma = 0.0
                    bus_v = 0.0
            else:
                ma = 0.0
                bus_v = 0.0
            duty = round(float(duties.get(i, 0.0)), 1)
            state = (ch_status or {}).get(i, "UNKNOWN")
            status = _channel_health(ok, state, ma) if ok else "ERR"
            z_ohm = _cell_impedance_ohm(bus_v, ma) if ok else 0.0
            v_cell = _cell_voltage_v(bus_v, duty) if ok else 0.0
            p_w = _dc_power_w(bus_v, ma) if ok else 0.0
            prev_z = self._prev_z_ohm[i]
            if ok:
                z_delta = (
                    round(z_ohm - prev_z, 2) if prev_z is not None else None
                )
                self._prev_z_ohm[i] = z_ohm
            else:
                z_delta = None

            z_std: float | None = None
            sigma_s: float | None = None
            fqi_raw: float | None = None
            fqi_smooth: float | None = None
            z_rate: float | None = None
            dvd_i_ohm: float | None = None
            eff_ma_pct: float | None = None
            if ok:
                self._z_stat_window[i].append(z_ohm)
                w = self._z_stat_window[i]
                if len(w) >= 2:
                    z_std = round(statistics.pstdev(w), 4)
                sigma_s = _sigma_proxy_s(z_ohm)
                fqi_raw = round(_conductance_i_over_v_s(ma, bus_v), 9)
                alpha = float(getattr(cfg, "FQI_EMA_ALPHA", 0.15))
                prev_ema = self._fqi_ema[i]
                fqi_smooth = round(
                    fqi_raw
                    if prev_ema is None
                    else (alpha * fqi_raw + (1.0 - alpha) * prev_ema),
                    9,
                )
                self._fqi_ema[i] = fqi_smooth
                if z_delta is not None:
                    z_rate = round(z_delta / dt_s, 4)
                pb = self._prev_bus_v_elec[i]
                pm = self._prev_ma_elec[i]
                pd = self._prev_duty_elec[i]
                if pb is not None and pm is not None:
                    di_a = (ma - pm) / 1000.0
                    if abs(di_a) >= 1e-7:
                        dvd_i_ohm = round((bus_v - pb) / di_a, 4)
                    if pd is not None and abs(duty - pd) >= 0.08:
                        eff_ma_pct = round((ma - pm) / (duty - pd), 6)
                self._prev_bus_v_elec[i] = bus_v
                self._prev_ma_elec[i] = ma
                self._prev_duty_elec[i] = duty

            surface = (
                _surface_hint(ma, z_ohm, z_std, cfg)
                if ok
                else "UNKNOWN"
            )

            channels[i] = {
                "state": state,
                "ma": ma,
                "duty": duty,
                "bus_v": bus_v,
                "status": status,
                "impedance_ohm": z_ohm,
                "cell_voltage_v": v_cell,
                "power_w": p_w,
                "z_delta_ohm": z_delta,
                "_reading_ok": ok,
                "z_std_ohm": z_std,
                "sigma_proxy_s": sigma_s,
                "fqi_raw_s": fqi_raw,
                "fqi_smooth_s": fqi_smooth,
                "z_rate_ohm_s": z_rate,
                "dV_dI_ohm": dvd_i_ohm,
                "efficiency_ma_per_pct": eff_ma_pct,
                "surface_hint": surface,
            }

        self._update_wet_sessions(ts_unix, channels, dt_s)
        self._accumulate_daily_totals(channels, dt_s)
        self._accumulate_daily_energy(channels, dt_s)

        for i in range(cfg.NUM_CHANNELS):
            channels[i]["coulombs_today_c"] = round(
                self._daily_totals[i]["ma_s"] / 1000.0, 6
            )
            channels[i]["energy_today_j"] = round(
                self._daily_totals[i]["energy_j"], 4
            )

        total_ma = round(sum(d["ma"] for d in channels.values()), 4)
        total_power_w = round(sum(d["power_w"] for d in channels.values()), 6)
        active_v = [d["bus_v"] for d in channels.values() if d["bus_v"] > 0]
        supply_v_avg = (
            round(sum(active_v) / len(active_v), 3) if active_v else 0.0
        )
        wet_channels = sum(
            1 for d in channels.values() if d["state"] == "PROTECTING"
        )

        mas_ok = [
            channels[i]["ma"]
            for i in range(cfg.NUM_CHANNELS)
            if channels[i].get("_reading_ok")
        ]
        zs_ok = [
            channels[i]["impedance_ohm"]
            for i in range(cfg.NUM_CHANNELS)
            if channels[i].get("_reading_ok") and channels[i]["impedance_ohm"] > 0
        ]
        cross_i_cv = _coefficient_of_variation(mas_ok)
        cross_z_cv = _coefficient_of_variation(zs_ok)

        self._write_db(
            ts,
            ts_unix,
            wet,
            fault_latched,
            faults,
            channels,
            total_ma,
            supply_v_avg,
            total_power_w,
            cross_i_cv,
            cross_z_cv,
            ref_shift_mv,
            ref_status,
            temp_f,
            ref_raw_mv,
            ref_hw_ok,
            ref_hint,
        )
        self._maybe_periodic_purge()

        def _ch_public(d: dict) -> dict:
            return {k: v for k, v in d.items() if not k.startswith("_")}

        public_channels: dict[str, dict] = {
            str(i): _ch_public(d) for i, d in channels.items()
        }

        payload: dict = {
            "ts": ts,
            "ts_unix": ts_unix,
            "wet": wet,
            "wet_channels": wet_channels,
            "fault_latched": fault_latched,
            "faults": list(faults),
            "channels": public_channels,
            "total_ma": total_ma,
            "supply_v_avg": supply_v_avg,
            "total_power_w": total_power_w,
            "cross": {
                "i_cv": cross_i_cv,
                "z_cv": cross_z_cv,
            },
        }
        if sim_time is not None:
            payload["sim_time"] = sim_time
        # Reference block: stable keys every tick for console parity / dashboard / scripts.
        payload["ref_raw_mv"] = ref_raw_mv
        payload["ref_shift_mv"] = ref_shift_mv
        payload["ref_status"] = ref_status or "N/A"
        payload["ref_hw_ok"] = bool(ref_hw_ok) if ref_hw_ok is not None else False
        payload["ref_hw_message"] = (ref_hw_message or "").strip()
        payload["ref_hint"] = (ref_hint or "").strip()
        payload["ref_baseline_set"] = (
            bool(ref_baseline_set) if ref_baseline_set is not None else False
        )
        payload["temp_f"] = temp_f
        _atomic_write_same_dir(self._latest_path, json.dumps(payload))

        pre_sig = self._fault_signature
        sig = tuple(faults)
        fault_log_written = False
        if faults and sig != self._fault_signature:
            self._append_fault_line(ts, wet, fault_latched, faults, readings)
            self._fault_signature = sig
            fault_log_written = True
        if not faults:
            self._fault_signature = None

        row: dict[str, object] = {
            "ts": ts,
            "wet": int(wet),
            "fault_latched": int(fault_latched),
            "faults": ";".join(faults),
        }
        for i in range(cfg.NUM_CHANNELS):
            ch = channels[i]
            n = i + 1
            row[f"ch{n}_state"] = ch["state"]
            row[f"ch{n}_ma"] = ch["ma"]
            row[f"ch{n}_duty"] = ch["duty"]
            row[f"ch{n}_bus_v"] = ch["bus_v"]
            row[f"ch{n}_status"] = ch["status"]
            row[f"ch{n}_impedance_ohm"] = ch["impedance_ohm"]
            row[f"ch{n}_cell_voltage_v"] = ch["cell_voltage_v"]
            row[f"ch{n}_power_w"] = ch["power_w"]
            row[f"ch{n}_z_delta_ohm"] = (
                ch["z_delta_ohm"] if ch["z_delta_ohm"] is not None else ""
            )
            row[f"ch{n}_coulombs_today_c"] = ch["coulombs_today_c"]
            row[f"ch{n}_energy_today_j"] = ch["energy_today_j"]
            row[f"ch{n}_z_std_ohm"] = ch["z_std_ohm"] if ch["z_std_ohm"] is not None else ""
            row[f"ch{n}_sigma_proxy_s"] = (
                ch["sigma_proxy_s"] if ch["sigma_proxy_s"] is not None else ""
            )
            row[f"ch{n}_fqi_raw_s"] = (
                ch["fqi_raw_s"] if ch["fqi_raw_s"] is not None else ""
            )
            row[f"ch{n}_fqi_smooth_s"] = (
                ch["fqi_smooth_s"] if ch["fqi_smooth_s"] is not None else ""
            )
            row[f"ch{n}_z_rate_ohm_s"] = (
                ch["z_rate_ohm_s"] if ch["z_rate_ohm_s"] is not None else ""
            )
            row[f"ch{n}_dV_dI_ohm"] = (
                ch["dV_dI_ohm"] if ch["dV_dI_ohm"] is not None else ""
            )
            row[f"ch{n}_efficiency_ma_per_pct"] = (
                ch["efficiency_ma_per_pct"]
                if ch["efficiency_ma_per_pct"] is not None
                else ""
            )
            row[f"ch{n}_surface_hint"] = ch["surface_hint"]
        row["total_ma"] = total_ma
        row["supply_v_avg"] = supply_v_avg
        row["total_power_w"] = total_power_w
        row["cross_i_cv"] = cross_i_cv if cross_i_cv is not None else ""
        row["cross_z_cv"] = cross_z_cv if cross_z_cv is not None else ""
        row["ref_shift_mv"] = ref_shift_mv if ref_shift_mv is not None else ""
        row["ref_status"] = ref_status or ""
        row["temp_f"] = temp_f if temp_f is not None else ""
        row["ref_raw_mv"] = ref_raw_mv if ref_raw_mv is not None else ""
        row["ref_hw_ok"] = (
            int(ref_hw_ok) if ref_hw_ok is not None else ""
        )
        row["ref_hint"] = ref_hint or ""
        row["ref_hw_message"] = (ref_hw_message or "").strip()
        row["ref_baseline_set"] = (
            int(bool(ref_baseline_set)) if ref_baseline_set is not None else 0
        )
        self._csv_rows.append(row)

        if fault_log_written or (bool(faults) and sig != pre_sig):
            self._flush_csv(sync=True)

        for i in range(cfg.NUM_CHANNELS):
            self._prev_fsm[i] = channels[i]["state"]

        return {
            "channels": public_channels,
            "total_power_w": total_power_w,
        }

    def _update_wet_sessions(
        self,
        ts_unix: float,
        channels: dict[int, dict],
        dt_s: float,
    ) -> None:
        """Close wet session before this tick's mA is applied if we left PROTECTING."""
        with self._db_lock:
            for ch in range(cfg.NUM_CHANNELS):
                prev = self._prev_fsm[ch]
                st = channels[ch]["state"]
                ma = channels[ch]["ma"]
                z = channels[ch]["impedance_ohm"]

                if prev == "PROTECTING" and st != "PROTECTING":
                    if ch not in self._wet_active:
                        continue
                    a = self._wet_active.pop(ch)
                    ended = ts_unix
                    dur = max(ended - a["t0"], 0.0)
                    avg_ma = (a["sum_ma_s"] / dur) if dur > 0 else 0.0
                    avg_z = (a["sum_z"] / a["n"]) if a["n"] else 0.0
                    self._db.execute(
                        """
                        UPDATE wet_sessions SET
                            ended_at = ?,
                            duration_s = ?,
                            total_ma_s = ?,
                            avg_ma = ?,
                            avg_impedance_ohm = ?,
                            peak_ma = ?
                        WHERE id = ?
                        """,
                        (
                            ended,
                            dur,
                            a["sum_ma_s"],
                            round(avg_ma, 4),
                            round(avg_z, 2),
                            round(a["peak_ma"], 4),
                            a["id"],
                        ),
                    )
                    continue

                if st == "PROTECTING" and prev != "PROTECTING":
                    cur = self._db.execute(
                        "INSERT INTO wet_sessions (channel, started_at) VALUES (?, ?)",
                        (ch + 1, ts_unix),
                    )
                    sid = cur.lastrowid
                    self._wet_active[ch] = {
                        "id": sid,
                        "t0": ts_unix,
                        "sum_ma_s": 0.0,
                        "sum_z": 0.0,
                        "n": 0,
                        "peak_ma": ma,
                    }

                if ch in self._wet_active:
                    a = self._wet_active[ch]
                    a["sum_ma_s"] += ma * dt_s
                    a["sum_z"] += z
                    a["n"] += 1
                    a["peak_ma"] = max(a["peak_ma"], ma)
            self._db.commit()

    def _accumulate_daily_totals(
        self,
        channels: dict[int, dict],
        dt_s: float,
    ) -> None:
        for i in range(cfg.NUM_CHANNELS):
            if channels[i]["state"] == "PROTECTING":
                self._daily_totals[i]["ma_s"] += channels[i]["ma"] * dt_s
                self._daily_totals[i]["wet_s"] += dt_s

    def _accumulate_daily_energy(
        self,
        channels: dict[int, dict],
        dt_s: float,
    ) -> None:
        """∫ V·I dt per channel (joules) while shunt reads are valid."""
        for i in range(cfg.NUM_CHANNELS):
            if channels[i].get("_reading_ok"):
                self._daily_totals[i]["energy_j"] += (
                    float(channels[i]["power_w"]) * dt_s
                )

    def _write_db(
        self,
        ts: str,
        ts_unix: float,
        wet: bool,
        fault_latched: bool,
        faults: list[str],
        channels: dict[int, dict],
        total_ma: float,
        supply_v_avg: float,
        total_power_w: float,
        cross_i_cv: float | None,
        cross_z_cv: float | None,
        ref_shift_mv: float | None,
        ref_status: str | None,
        temp_f: float | None,
        ref_raw_mv: float | None,
        ref_hw_ok: bool | None,
        ref_hint: str | None,
    ) -> None:
        ch_col_names = ", ".join(
            f"ch{i}_state, ch{i}_ma, ch{i}_duty, ch{i}_bus_v, ch{i}_status, "
            f"ch{i}_impedance_ohm, ch{i}_cell_voltage_v, "
            f"ch{i}_power_w, ch{i}_z_delta_ohm"
            for i in range(1, cfg.NUM_CHANNELS + 1)
        )
        ch_values: list[object] = []
        for i in range(cfg.NUM_CHANNELS):
            d = channels[i]
            zd = d["z_delta_ohm"]
            ch_values.extend(
                [
                    d["state"],
                    d["ma"],
                    d["duty"],
                    d["bus_v"],
                    d["status"],
                    d["impedance_ohm"],
                    d["cell_voltage_v"],
                    d["power_w"],
                    zd,
                ]
            )

        col_names = (
            f"ts,ts_unix,wet,fault_latched,faults,{ch_col_names},total_ma,"
            f"supply_v_avg,total_power_w,cross_i_cv,cross_z_cv,ref_shift_mv,ref_status,temp_f,"
            f"ref_raw_mv,ref_hw_ok,ref_hint"
        )
        n_ch_cols = 9
        n_params = 5 + cfg.NUM_CHANNELS * n_ch_cols + 3 + 2 + 3 + 3
        placeholders = ",".join(["?"] * n_params)
        ref_hw_sql = None if ref_hw_ok is None else (1 if ref_hw_ok else 0)
        with self._db_lock:
            self._db.execute(
                f"INSERT INTO readings ({col_names}) VALUES ({placeholders})",
                (
                    ts,
                    ts_unix,
                    int(wet),
                    int(fault_latched),
                    ";".join(faults),
                    *ch_values,
                    total_ma,
                    supply_v_avg,
                    total_power_w,
                    cross_i_cv,
                    cross_z_cv,
                    ref_shift_mv,
                    ref_status,
                    temp_f,
                    ref_raw_mv,
                    ref_hw_sql,
                    ref_hint,
                ),
            )
            self._db.commit()
        self._insert_count += 1

    def feed_cooling_cycle(
        self,
        *,
        in_band: bool,
        ts_unix: float,
        dt_s: float,
        ch_status: dict[int, str] | None,
        temp_f: float | None,
    ) -> None:
        """
        Track one ICCP temperature-band segment (same bounds as temp.in_operating_range).

        Call with in_band=True on each tick while the main loop is driving channels;
        call in_band=False when entering thermal pause (T outside band) so the open
        segment is closed and a cooling_cycles row is written.
        """
        if in_band:
            if not self._cycle_active:
                self._cycle_active = True
                self._cycle_t0 = ts_unix
                self._cycle_dwell = [0.0] * cfg.NUM_CHANNELS
                self._cycle_temp_sum = 0.0
                self._cycle_temp_n = 0
            if temp_f is not None:
                self._cycle_temp_sum += temp_f
                self._cycle_temp_n += 1
            if ch_status:
                for i in range(cfg.NUM_CHANNELS):
                    if ch_status.get(i) == "PROTECTING":
                        self._cycle_dwell[i] += dt_s
        elif self._cycle_active:
            self._finalize_cooling_cycle(ts_unix)
            self._cycle_active = False
            self._cycle_t0 = None

    def _finalize_cooling_cycle(self, ended_at: float) -> None:
        if self._cycle_t0 is None:
            return
        dur = max(ended_at - self._cycle_t0, 0.0)
        avg_tf: float | None = (
            round(self._cycle_temp_sum / self._cycle_temp_n, 2)
            if self._cycle_temp_n
            else None
        )
        prot_cols = ", ".join(f"ch{i}_protect_s" for i in range(1, cfg.NUM_CHANNELS + 1))
        placeholders = ", ".join(["?"] * (4 + cfg.NUM_CHANNELS))
        sql = (
            f"INSERT INTO cooling_cycles (started_at, ended_at, duration_s, "
            f"avg_temp_f, {prot_cols}) VALUES ({placeholders})"
        )
        vals: list[object] = [
            self._cycle_t0,
            ended_at,
            dur,
            avg_tf,
            *[round(self._cycle_dwell[i], 2) for i in range(cfg.NUM_CHANNELS)],
        ]
        with self._db_lock:
            self._db.execute(sql, vals)
            self._db.commit()

    def _maybe_periodic_purge(self) -> None:
        if self._insert_count % cfg.SQLITE_PURGE_EVERY_N_INSERTS != 0:
            return
        self._purge_old_rows()

    def _append_fault_line(
        self,
        ts: object,
        wet: bool,
        fault_latched: bool,
        faults: list[str],
        readings: dict[int, dict],
    ) -> None:
        ts_str = str(ts).replace("T", " ", 1)
        parts = [
            ts_str,
            "FAULT",
            f"latched={int(fault_latched)}",
            f"any_wet={int(wet)}",
            f"faults=[{'; '.join(faults)}]",
        ]
        for i in range(cfg.NUM_CHANNELS):
            r = readings.get(i, {})
            if r.get("ok"):
                try:
                    cur = float(r["current"])
                    parts.append(f"CH{i + 1}:{cur:.3f}mA")
                except (TypeError, ValueError):
                    parts.append(f"CH{i + 1}:ERR")
            else:
                parts.append(f"CH{i + 1}:ERR")
        line = "  ".join(parts)
        with self._fault_log_path.open("a", encoding="utf-8") as lf:
            lf.write(line + "\n")
            lf.flush()
            os.fsync(lf.fileno())

    def maybe_flush(self, force: bool = False) -> None:
        new_path = self._daily_csv_path()
        if new_path != self._csv_path:
            self._flush_csv()
            self._csv_path = new_path
            self._csv_headers_written = False
        if force or (time.monotonic() - self._last_flush) >= cfg.LOG_INTERVAL_S:
            self._flush_csv()

    def _flush_csv(self, sync: bool = False) -> None:
        if not self._csv_rows:
            self._last_flush = time.monotonic()
            return
        self._rotate_csv_if_needed()
        fieldnames = list(self._csv_rows[0].keys())
        with self._csv_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if not self._csv_headers_written:
                w.writeheader()
                self._csv_headers_written = True
            w.writerows(self._csv_rows)
            f.flush()
            if sync:
                os.fsync(f.fileno())
        self._csv_rows.clear()
        self._last_flush = time.monotonic()

    def _rotate_csv_if_needed(self) -> None:
        if not self._csv_path.exists() or self._csv_path.stat().st_size < cfg.LOG_MAX_BYTES:
            return
        keep = cfg.LOG_ROTATION_KEEP
        files = [self._csv_path] + [
            cfg.LOG_DIR / f"{cfg.LOG_BASE_NAME}.{i}.csv" for i in range(1, keep)
        ]
        if files[-1].exists():
            files[-1].unlink()
        for i in range(keep - 2, -1, -1):
            if files[i].exists():
                shutil.move(str(files[i]), str(files[i + 1]))
        self._csv_headers_written = False

    def close(self) -> None:
        ts_unix = time.time()
        if self._cycle_active:
            self._finalize_cooling_cycle(ts_unix)
            self._cycle_active = False
            self._cycle_t0 = None
        with self._db_lock:
            for _ch, a in list(self._wet_active.items()):
                ended = ts_unix
                dur = max(ended - a["t0"], 0.0)
                avg_ma = (a["sum_ma_s"] / dur) if dur > 0 else 0.0
                avg_z = (a["sum_z"] / a["n"]) if a["n"] else 0.0
                self._db.execute(
                    """
                    UPDATE wet_sessions SET
                        ended_at = ?,
                        duration_s = ?,
                        total_ma_s = ?,
                        avg_ma = ?,
                        avg_impedance_ohm = ?,
                        peak_ma = ?
                    WHERE id = ?
                    """,
                    (
                        ended,
                        dur,
                        a["sum_ma_s"],
                        round(avg_ma, 4),
                        round(avg_z, 2),
                        round(a["peak_ma"], 4),
                        a["id"],
                    ),
                )
            self._wet_active.clear()
            self._db.commit()

        self._persist_daily_totals(self._daily_date)
        new_path = self._daily_csv_path()
        if new_path != self._csv_path:
            self._flush_csv()
            self._csv_path = new_path
            self._csv_headers_written = False
        self._flush_csv(sync=True)
        with self._db_lock:
            try:
                self._db.close()
            except Exception:
                pass
