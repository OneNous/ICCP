"""Buffered CSV logging with time-based flush and size-based rotation."""

from __future__ import annotations

import csv
import shutil
import time
from pathlib import Path

import config.settings as cfg


class DataLogger:
    def __init__(self) -> None:
        cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._path = cfg.LOG_DIR / f"{cfg.LOG_BASE_NAME}.csv"
        self._rows: list[dict[str, object]] = []
        self._last_flush = time.monotonic()
        self._headers_written = self._path.exists() and self._path.stat().st_size > 0

    def record(
        self,
        readings: dict[int, dict],
        wet: bool,
        faults: list[str],
        duties: dict[int, float],
        fault_latched: bool,
    ) -> None:
        row: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "wet": int(wet),
            "fault_latched": int(fault_latched),
            "faults": ";".join(faults),
        }
        for i in range(cfg.NUM_CHANNELS):
            r = readings.get(i, {})
            row[f"ch{i + 1}_ok"] = int(bool(r.get("ok")))
            row[f"ch{i + 1}_ma"] = r.get("current", "")
            row[f"ch{i + 1}_bus_v"] = r.get("bus_v", "")
            row[f"ch{i + 1}_duty"] = duties.get(i, "")
        self._rows.append(row)

    def maybe_flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_flush) < cfg.LOG_INTERVAL_S:
            return
        self._flush()

    def _rotate_if_needed(self) -> None:
        if not self._path.exists() or self._path.stat().st_size < cfg.LOG_MAX_BYTES:
            return
        keep = cfg.LOG_ROTATION_KEEP
        files = [self._path] + [
            cfg.LOG_DIR / f"{cfg.LOG_BASE_NAME}.{i}.csv" for i in range(1, keep)
        ]
        tail = files[-1]
        if tail.exists():
            tail.unlink()
        for i in range(keep - 2, -1, -1):
            if files[i].exists():
                shutil.move(str(files[i]), str(files[i + 1]))
        self._headers_written = False

    def _flush(self) -> None:
        if not self._rows:
            self._last_flush = time.monotonic()
            return
        self._rotate_if_needed()
        fieldnames = list(self._rows[0].keys())
        write_header = not self._headers_written
        with self._path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                w.writeheader()
                self._headers_written = True
            w.writerows(self._rows)
        self._rows.clear()
        self._last_flush = time.monotonic()

    def close(self) -> None:
        self._flush(force=True)
