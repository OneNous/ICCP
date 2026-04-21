"""
Read-only SQLite helpers for live history / trends.

Shared by dashboard HTTP routes and the Textual TUI. Column names must stay
aligned with logger.py schema and dashboard expectations.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import config.settings as cfg

# Match dashboard.py caps for /api/history
HISTORY_MINUTES_DEFAULT = 60
HISTORY_MINUTES_MAX = 60 * 24 * 366
HISTORY_MAX_POINTS = 1800


def db_path() -> Path:
    return cfg.LOG_DIR / cfg.SQLITE_DB_NAME


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_readings_since(since_unix: float) -> list[sqlite3.Row]:
    """All readings rows with ts_unix >= since_unix, oldest first."""
    if not db_path().is_file():
        raise FileNotFoundError(str(db_path()))
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM readings WHERE ts_unix >= ? ORDER BY ts_unix ASC",
            (since_unix,),
        ).fetchall()


def downsample_readings(rows: list[sqlite3.Row], max_points: int = HISTORY_MAX_POINTS) -> list[sqlite3.Row]:
    n = len(rows)
    if n == 0:
        return []
    step = max(1, (n + max_points - 1) // max_points)
    return rows[::step]


def history_payload(
    minutes: int,
    *,
    metric: str = "ma",
) -> dict[str, Any]:
    """
    Build the same JSON-shaped dict as dashboard /api/history (without Flask).
    """
    minutes = max(1, min(int(minutes), HISTORY_MINUTES_MAX))
    metric = metric.lower()
    if metric not in ("ma", "impedance"):
        metric = "ma"
    since = time.time() - minutes * 60

    try:
        rows = fetch_readings_since(since)
    except Exception:
        return {"error": "database not ready", "labels": [], "channels": {}, "total": []}

    sampled = downsample_readings(rows, HISTORY_MAX_POINTS)
    labels = [r["ts"][11:19] for r in sampled]
    channels: dict[str, list[Any]] = {str(i): [] for i in range(cfg.NUM_CHANNELS)}
    total: list[float | None] = []
    avg_target_ma: list[float] = []
    fallback_tgt = float(cfg.TARGET_MA)

    for r in sampled:
        for i in range(cfg.NUM_CHANNELS):
            if metric == "impedance":
                key = f"ch{i + 1}_impedance_ohm"
                try:
                    val = r[key]
                except (KeyError, IndexError):
                    val = None
                channels[str(i)].append(val)
            else:
                channels[str(i)].append(r[f"ch{i + 1}_ma"])
        total.append(r["total_ma"])
        if metric == "ma":
            tvals: list[float] = []
            for i in range(cfg.NUM_CHANNELS):
                key = f"ch{i + 1}_target_ma"
                if key not in r:
                    continue
                raw_t = r[key]
                if raw_t is None or raw_t == "":
                    continue
                try:
                    tvals.append(float(raw_t))
                except (TypeError, ValueError):
                    continue
            avg_target_ma.append(
                round(sum(tvals) / len(tvals), 4) if tvals else fallback_tgt
            )

    tgt = cfg.TARGET_MA if metric == "ma" else None
    return {
        "labels": labels,
        "channels": channels,
        "total": total,
        "target": tgt,
        "avg_target_ma": avg_target_ma if metric == "ma" else [],
        "metric": metric,
        "count": len(sampled),
        "minutes": minutes,
    }


def trends_table_rows(minutes: int = 60, max_rows: int = 40) -> tuple[list[str], list[tuple[Any, ...]]]:
    """
    Downsampled rows for a DataTable: time + per-anode mA + total.
    Returns (column_keys_or_titles, list of tuples).
    """
    since = time.time() - minutes * 60
    try:
        rows = fetch_readings_since(since)
    except Exception:
        return [], []
    if not rows:
        return [], []
    step = max(1, len(rows) // max_rows)
    sampled = rows[::step]
    cols = ["Time"] + [f"Anode {i + 1} (idx {i})" for i in range(cfg.NUM_CHANNELS)] + ["ΣmA"]
    out: list[tuple[Any, ...]] = []
    for r in sampled:
        ts = str(r["ts"])[11:19] if r["ts"] else "—"
        ch_vals = tuple(round(float(r[f"ch{i+1}_ma"] or 0), 3) for i in range(cfg.NUM_CHANNELS))
        tot = round(float(r["total_ma"] or 0), 4)
        out.append((ts,) + ch_vals + (tot,))
    return cols, out
