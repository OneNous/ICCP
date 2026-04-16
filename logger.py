"""Buffered CSV logging: daily files, time-based flush, fault log, fault-time fsync."""

from __future__ import annotations

import csv
import os
import time
from collections import defaultdict
from pathlib import Path

import config.settings as cfg


def _channel_status(r: dict) -> str:
    """Per-channel protection / health: OK, LOW (undervolt), HIGH (overvolt/overcurrent), ERR."""
    if not r.get("ok"):
        return "ERR"
    try:
        cur = float(r["current"])
        bus_v = float(r["bus_v"])
    except (TypeError, ValueError):
        return "ERR"
    if bus_v < cfg.MIN_BUS_V:
        return "LOW"
    if bus_v > cfg.MAX_BUS_V or cur > cfg.MAX_MA:
        return "HIGH"
    return "OK"


def _supply_v_avg(readings: dict[int, dict]) -> object:
    volts: list[float] = []
    for i in range(cfg.NUM_CHANNELS):
        r = readings.get(i, {})
        if not r.get("ok"):
            continue
        try:
            volts.append(float(r["bus_v"]))
        except (TypeError, ValueError):
            continue
    if not volts:
        return ""
    return round(sum(volts) / len(volts), 4)


def _total_ma(readings: dict[int, dict]) -> object:
    total = 0.0
    for i in range(cfg.NUM_CHANNELS):
        r = readings.get(i, {})
        if not r.get("ok"):
            continue
        try:
            total += float(r["current"])
        except (TypeError, ValueError):
            continue
    return round(total, 6)


class DataLogger:
    def __init__(self) -> None:
        cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._rows: list[dict[str, object]] = []
        self._last_flush = time.monotonic()
        self._fault_signature: tuple[str, ...] | None = None
        self._fault_log_path = cfg.LOG_DIR / cfg.FAULT_LOG_NAME

    @staticmethod
    def _csv_path_for_date(ymd: str) -> Path:
        return cfg.LOG_DIR / f"{cfg.LOG_BASE_NAME}_{ymd}.csv"

    def record(
        self,
        readings: dict[int, dict],
        any_wet: bool,
        faults: list[str],
        duties: dict[int, float],
        fault_latched: bool,
        ch_status: dict[int, str] | None = None,
    ) -> None:
        pre_sig = self._fault_signature

        row: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "any_wet": int(any_wet),
            "fault_latched": int(fault_latched),
            "faults": ";".join(faults),
        }
        for i in range(cfg.NUM_CHANNELS):
            r = readings.get(i, {})
            row[f"ch{i + 1}_status"] = _channel_status(r)
            row[f"ch{i + 1}_ok"] = int(bool(r.get("ok")))
            row[f"ch{i + 1}_ma"] = r.get("current", "")
            row[f"ch{i + 1}_bus_v"] = r.get("bus_v", "")
            row[f"ch{i + 1}_duty"] = duties.get(i, "")
            row[f"ch{i + 1}_fsm"] = (ch_status or {}).get(i, "")
        row["supply_v_avg"] = _supply_v_avg(readings)
        row["total_ma"] = _total_ma(readings)
        self._rows.append(row)

        sig = tuple(faults)
        fault_log_written = False
        if faults and sig != self._fault_signature:
            self._append_fault_line(row["ts"], any_wet, fault_latched, faults, readings)
            self._fault_signature = sig
            fault_log_written = True
        if not faults:
            self._fault_signature = None

        if fault_log_written or (bool(faults) and sig != pre_sig):
            self._flush(sync=True)

    def _append_fault_line(
        self,
        ts: object,
        any_wet: bool,
        fault_latched: bool,
        faults: list[str],
        readings: dict[int, dict],
    ) -> None:
        ts_str = str(ts).replace("T", " ", 1)
        parts = [
            ts_str,
            "FAULT",
            f"latched={int(fault_latched)}",
            f"any_wet={int(any_wet)}",
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
        now = time.monotonic()
        if not force and (now - self._last_flush) < cfg.LOG_INTERVAL_S:
            return
        self._flush()

    def _flush(self, sync: bool = False) -> None:
        if not self._rows:
            self._last_flush = time.monotonic()
            return

        by_date: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in self._rows:
            ts = row.get("ts", "")
            ymd = str(ts)[:10] if len(str(ts)) >= 10 else time.strftime("%Y-%m-%d")
            by_date[ymd].append(row)

        for ymd, rows in sorted(by_date.items()):
            path = self._csv_path_for_date(ymd)
            fieldnames = list(rows[0].keys())
            write_header = not (path.exists() and path.stat().st_size > 0)
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                if write_header:
                    w.writeheader()
                w.writerows(rows)
                f.flush()
                if sync:
                    os.fsync(f.fileno())

        self._rows.clear()
        self._last_flush = time.monotonic()

    def close(self) -> None:
        self._flush(sync=True)
