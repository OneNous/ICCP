"""
CoilShield — data logger.

Four sinks per record() call:
  1. coilshield.db (SQLite WAL, every tick; periodic + startup retention purge)
  2. latest.json (atomic replace every tick — live dashboard feed)
  3. iccp_faults.log (deduped by fault signature + fsync; same semantics as legacy)
  4. iccp_YYYY-MM-DD.csv (buffered, flushed every LOG_INTERVAL_S; sync flush on fault transitions)

Dashboard SQL and _init_schema column names MUST stay in sync with dashboard.py.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

import config.settings as cfg


def _channel_health(
    ok: bool,
    state: str,
    ma: float,
) -> str:
    """UI / DB status column: OK, LOW, HIGH, ERR, DRY."""
    if not ok:
        return "ERR"
    if state in ("DORMANT", "PROBING"):
        return "DRY"
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

    def _daily_csv_path(self) -> Path:
        return cfg.LOG_DIR / f"{cfg.LOG_BASE_NAME}_{time.strftime('%Y-%m-%d')}.csv"

    def _init_schema(self) -> None:
        """Must match dashboard.py queries (readings columns, chN_* names)."""
        ch_cols = ", ".join(
            f"ch{i}_state TEXT, ch{i}_ma REAL, ch{i}_duty REAL, "
            f"ch{i}_bus_v REAL, ch{i}_status TEXT"
            for i in range(1, cfg.NUM_CHANNELS + 1)
        )
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
                    supply_v_avg    REAL
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_ts_unix ON readings (ts_unix)"
            )
            self._db.commit()

    def _purge_old_rows(self) -> None:
        cutoff = time.time() - cfg.TELEMETRY_RETENTION_DAYS * 86400
        with self._db_lock:
            self._db.execute("DELETE FROM readings WHERE ts_unix < ?", (cutoff,))
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
    ) -> None:
        wet = any_wet
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        ts_unix = time.time()

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

            channels[i] = {
                "state": state,
                "ma": ma,
                "duty": duty,
                "bus_v": bus_v,
                "status": status,
            }

        total_ma = round(sum(d["ma"] for d in channels.values()), 4)
        active_v = [d["bus_v"] for d in channels.values() if d["bus_v"] > 0]
        supply_v_avg = (
            round(sum(active_v) / len(active_v), 3) if active_v else 0.0
        )
        wet_channels = sum(
            1 for d in channels.values() if d["state"] == "PROTECTING"
        )

        self._write_db(
            ts,
            ts_unix,
            wet,
            fault_latched,
            faults,
            channels,
            total_ma,
            supply_v_avg,
        )
        self._maybe_periodic_purge()

        payload: dict = {
            "ts": ts,
            "ts_unix": ts_unix,
            "wet": wet,
            "wet_channels": wet_channels,
            "fault_latched": fault_latched,
            "faults": list(faults),
            "channels": {str(i): d for i, d in channels.items()},
            "total_ma": total_ma,
            "supply_v_avg": supply_v_avg,
        }
        if sim_time is not None:
            payload["sim_time"] = sim_time
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
        row["total_ma"] = total_ma
        row["supply_v_avg"] = supply_v_avg
        self._csv_rows.append(row)

        if fault_log_written or (bool(faults) and sig != pre_sig):
            self._flush_csv(sync=True)

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
    ) -> None:
        ch_col_names = ", ".join(
            f"ch{i}_state, ch{i}_ma, ch{i}_duty, ch{i}_bus_v, ch{i}_status"
            for i in range(1, cfg.NUM_CHANNELS + 1)
        )
        ch_values: list[object] = []
        for i in range(cfg.NUM_CHANNELS):
            d = channels[i]
            ch_values.extend(
                [d["state"], d["ma"], d["duty"], d["bus_v"], d["status"]]
            )

        col_names = (
            f"ts,ts_unix,wet,fault_latched,faults,{ch_col_names},total_ma,supply_v_avg"
        )
        placeholders = ",".join(
            ["?"] * (5 + cfg.NUM_CHANNELS * 5 + 2)
        )
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
                ),
            )
            self._db.commit()
        self._insert_count += 1

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
