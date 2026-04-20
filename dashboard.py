#!/usr/bin/env python3
"""
CoilShield ICCP — web dashboard.

Run alongside main.py:
    python3 dashboard.py

Access from any device on the same network:
    http://<pi-ip>:8080

Reads from:
    logs/latest.json   — live data (atomic writes from main.py)
    logs/coilshield.db — history (SQLite WAL, written from main.py)

HTTP: /api/live (Cache-Control: no-store; adds feed_age_s, feed_stale_threshold_s), /api/diagnostic, /api/history, /api/stats, /api/daily, /api/sessions, /api/export, /api/export/csv

SQL column names for `readings` / `wet_sessions` / `daily_totals` MUST stay in sync with logger.py _init_schema.

Colors aligned with v77 coilshield-product-export.css .csp-exp — update both if brand shifts.

Install Flask if needed:
    pip install flask --break-system-packages
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, request, send_file

import config.settings as cfg

app = Flask(__name__)

DB_PATH = cfg.LOG_DIR / cfg.SQLITE_DB_NAME
LATEST_PATH = cfg.LOG_DIR / cfg.LATEST_JSON_NAME
DIAGNOSTIC_SNAPSHOT_PATH = cfg.LOG_DIR / getattr(
    cfg, "DIAGNOSTIC_SNAPSHOT_JSON", "diagnostic_snapshot.json"
)


def _sqlite_version_tuple() -> tuple[int, int, int]:
    raw = sqlite3.sqlite_version.split()[0]
    parts = [int(p) for p in raw.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def _warn_sqlite_lag_support() -> None:
    if _sqlite_version_tuple() >= (3, 25, 0):
        return
    print(
        f"[dashboard] WARNING: SQLite {sqlite3.sqlite_version} < 3.25; "
        "window functions (lag) in /api/stats may fail. Upgrade Pi OS / SQLite.",
        file=sys.stderr,
    )


def _safe_log_child(name: str) -> Path | None:
    """Resolve path under LOG_DIR for downloads."""
    base = cfg.LOG_DIR.resolve()
    path = (base / name).resolve()
    if base not in path.parents and path != base:
        return None
    return path


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _latest() -> dict:
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "no data yet — is main.py running?"}


def _live_envelope() -> dict:
    """Payload for /api/live: latest.json plus feed-health metadata for the UI."""
    data = _latest()
    try:
        st = LATEST_PATH.stat()
        data["feed_file_mtime_unix"] = round(st.st_mtime, 6)
        data["feed_age_s"] = round(time.time() - st.st_mtime, 3)
    except OSError:
        data["feed_file_mtime_unix"] = None
        data["feed_age_s"] = None
    data["sample_interval_s"] = float(cfg.SAMPLE_INTERVAL_S)
    # If main.py stops, latest.json stops updating; UI treats age above this as stale.
    data["feed_stale_threshold_s"] = max(3.0, 3.0 * float(cfg.SAMPLE_INTERVAL_S))
    data["target_ma"] = float(cfg.TARGET_MA)
    return data


@app.route("/api/live")
def api_live():
    resp = make_response(jsonify(_live_envelope()))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/diagnostic")
def api_diagnostic():
    """Last deep I2C snapshot from `touch logs/request_diag` while main is running."""
    try:
        raw = DIAGNOSTIC_SNAPSHOT_PATH.read_text(encoding="utf-8")
        return Response(raw, mimetype="application/json")
    except FileNotFoundError:
        return jsonify({"error": "no diagnostic_snapshot.json yet"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/history")
def api_history():
    minutes = int(request.args.get("minutes", 60))
    metric = request.args.get("metric", "ma").lower()
    if metric not in ("ma", "impedance"):
        metric = "ma"
    since = time.time() - minutes * 60

    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM readings WHERE ts_unix >= ? ORDER BY ts_unix ASC",
                (since,),
            ).fetchall()
    except Exception:
        return jsonify({"error": "database not ready"}), 503

    # Downsample to a bounded point count so the chart stays responsive, but keep
    # full resolution when there are few rows (avoids an empty-looking 1h graph).
    max_points = 1800
    n = len(rows)
    if n == 0:
        sampled = []
    else:
        step = max(1, (n + max_points - 1) // max_points)
        sampled = rows[::step]
    labels = [r["ts"][11:19] for r in sampled]
    channels = {str(i): [] for i in range(cfg.NUM_CHANNELS)}
    total: list[float | None] = []

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

    tgt = cfg.TARGET_MA if metric == "ma" else None
    resp = make_response(
        jsonify(
            {
                "labels": labels,
                "channels": channels,
                "total": total,
                "target": tgt,
                "metric": metric,
                "count": len(sampled),
                "minutes": minutes,
            }
        )
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/stats")
def api_stats():
    midnight = time.mktime(
        time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")
    )
    stats = []

    try:
        with _db() as conn:
            total_rows = conn.execute(
                "SELECT count(*) FROM readings WHERE ts_unix >= ?",
                (midnight,),
            ).fetchone()[0]

            env_row = conn.execute(
                """
                SELECT avg(ref_shift_mv) AS avg_ref, avg(temp_f) AS avg_temp
                FROM readings
                WHERE ts_unix >= ?
                """,
                (midnight,),
            ).fetchone()
            avg_ref_today = (
                round(env_row["avg_ref"], 2) if env_row["avg_ref"] is not None else None
            )
            avg_temp_today = (
                round(env_row["avg_temp"], 2) if env_row["avg_temp"] is not None else None
            )

            for i in range(cfg.NUM_CHANNELS):
                n = i + 1
                row = conn.execute(
                    f"""
                    SELECT
                        count(*) AS total_ticks,
                        sum(CASE WHEN ch{n}_state = 'PROTECTING' THEN 1 ELSE 0 END)
                            AS protecting_ticks,
                        avg(CASE WHEN ch{n}_state = 'PROTECTING' THEN ch{n}_ma END)
                            AS avg_ma,
                        avg(CASE WHEN ch{n}_state = 'PROTECTING' THEN ch{n}_bus_v END)
                            AS avg_v,
                        avg(CASE WHEN ch{n}_state = 'PROTECTING' THEN ch{n}_impedance_ohm END)
                            AS avg_z
                    FROM readings WHERE ts_unix >= ?
                    """,
                    (midnight,),
                ).fetchone()

                pticks = row["protecting_ticks"] or 0
                total = row["total_ticks"] or 1
                protecting_s = pticks * cfg.SAMPLE_INTERVAL_S
                pct = round(pticks / total * 100, 1) if total else 0
                avg_ma = round(row["avg_ma"] or 0, 3)
                avg_v = round(row["avg_v"] or 0, 2)
                avg_z = round(row["avg_z"] or 0, 1)

                transitions = conn.execute(
                    f"""
                    SELECT count(*) FROM (
                        SELECT ch{n}_state,
                               lag(ch{n}_state) OVER (ORDER BY ts_unix) AS prev
                        FROM readings WHERE ts_unix >= ?
                    )
                    WHERE ch{n}_state = 'PROTECTING' AND prev != 'PROTECTING'
                    """,
                    (midnight,),
                ).fetchone()[0]

                stats.append(
                    {
                        "ch": n,
                        "protecting_s": round(protecting_s, 0),
                        "protecting_pct": pct,
                        "avg_ma": avg_ma,
                        "avg_bus_v": avg_v,
                        "avg_impedance_ohm": avg_z,
                        "wet_cycles": transitions,
                        "ref_shift_mv": avg_ref_today,
                        "temp_f": avg_temp_today,
                    }
                )
    except Exception as e:
        return jsonify({"error": str(e)}), 503

    return jsonify(
        {
            "stats": stats,
            "total_ticks": total_rows,
            "sample_s": cfg.SAMPLE_INTERVAL_S,
            "target_ma": cfg.TARGET_MA,
        }
    )


@app.route("/api/sessions")
def api_sessions():
    """Recent wet (PROTECTING) episodes for export / Excel workflows."""
    try:
        hours = max(1, min(int(request.args.get("hours", 168)), 24 * 365))
        limit = max(1, min(int(request.args.get("limit", 2000)), 10_000))
    except ValueError:
        hours, limit = 168, 2000
    since = time.time() - hours * 3600
    try:
        with _db() as conn:
            rows = conn.execute(
                """
                SELECT id, channel, started_at, ended_at, duration_s, total_ma_s,
                       avg_ma, avg_impedance_ohm, peak_ma
                FROM wet_sessions
                WHERE started_at >= ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (since, limit),
            ).fetchall()
    except Exception as e:
        return jsonify({"error": str(e)}), 503
    sessions = []
    for row in rows:
        sessions.append({k: row[k] for k in row.keys()})
    return jsonify({"hours": hours, "limit": limit, "sessions": sessions})


@app.route("/api/daily")
def api_daily():
    """Today's cumulative mA·s and wet seconds per channel (daily_totals)."""
    today = time.strftime("%Y-%m-%d")
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM daily_totals WHERE date = ?",
                (today,),
            ).fetchone()
    except Exception as e:
        return jsonify({"error": str(e)}), 503
    if not row:
        return jsonify({"date": today, "channels": {}})
    out: dict[str, dict[str, float]] = {}
    for i in range(cfg.NUM_CHANNELS):
        n = i + 1
        out[str(i)] = {
            "ma_s": float(row[f"ch{n}_ma_s"] or 0.0),
            "wet_s": float(row[f"ch{n}_wet_s"] or 0.0),
        }
    return jsonify({"date": today, "channels": out})


@app.route("/api/export")
def api_export():
    path = _safe_log_child(cfg.SQLITE_DB_NAME)
    if path is None or not path.is_file():
        return "No database found", 404
    return send_file(
        str(path),
        as_attachment=True,
        download_name="coilshield.db",
        mimetype="application/x-sqlite3",
    )


@app.route("/api/export/csv")
def api_export_csv():
    today = time.strftime("%Y-%m-%d")
    name = f"{cfg.LOG_BASE_NAME}_{today}.csv"
    path = _safe_log_child(name)
    if path is None or not path.is_file():
        return "No CSV for today yet", 404
    return send_file(
        str(path),
        as_attachment=True,
        download_name=name,
        mimetype="text/csv",
    )


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CoilShield ICCP</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  /* v77 .csp-exp tokens — light CoilShield surface */
  :root {
    --csp-bg: #e4edf1;
    --csp-surface: #ffffff;
    --csp-text: #2b2b2b;
    --csp-text-muted: #5c5c5c;
    --csp-border: rgba(43, 43, 43, 0.18);
    --csp-accent: #7dd3fc;
    --csp-btn-dark: #2b2b2b;
    --csp-btn-dark-text: #f5f5f5;
    --csp-radius: 0.4rem;
    --green: #15803d;
    --green-bg: #dcfce7;
    --amber: #b45309;
    --amber-bg: #fef3c7;
    --blue: #1d4ed8;
    --blue-bg: #dbeafe;
    --red: #b91c1c;
    --red-bg: #fee2e2;
    --gray-text: #475569;
    --gray-bg: #f1f5f9;
    --ch0: #0369a1;
    --ch1: #0f766e;
    --ch2: #c2410c;
    --ch3: #b91c1c;
    --ch4: #6d28d9;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif;
    background: var(--csp-bg);
    color: var(--csp-text);
    font-size: 14px;
  }

  header {
    background: var(--csp-btn-dark);
    color: var(--csp-btn-dark-text);
    padding: 14px 20px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }
  header h1 { font-size: 18px; font-weight: 600; letter-spacing: -0.02em; }
  .status-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #4ade80;
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
  .header-stats {
    margin-left: auto;
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    font-size: 13px;
    opacity: .92;
  }
  .hdr-ref-sub {
    font-size: 11px;
    opacity: 0.88;
    max-width: 28rem;
    line-height: 1.35;
  }
  .header-fault { color: #fca5a5; font-weight: 600; }

  main { max-width: 1200px; margin: 0 auto; padding: 20px; }

  .ch-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }
  .ch-card {
    background: var(--csp-surface);
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 14px;
  }
  .ch-card .ch-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #075985;
    margin-bottom: 6px;
  }
  .ch-card .ch-state {
    font-size: 12px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 999px;
    display: inline-block;
    margin-bottom: 10px;
  }
  .state-PROTECTING { background: var(--green-bg); color: var(--green); }
  .state-REGULATE { background: var(--amber-bg); color: var(--amber); }
  .state-OPEN { background: var(--gray-bg); color: var(--gray-text); }
  .state-DRY { background: var(--gray-bg); color: var(--gray-text); }
  .state-DORMANT { background: var(--gray-bg); color: var(--gray-text); }
  .state-PROBING { background: var(--amber-bg); color: var(--amber); }
  .state-FAULT { background: var(--red-bg); color: var(--red); }
  .state-UNKNOWN { background: var(--gray-bg); color: var(--gray-text); }
  .ch-ma { font-size: 26px; font-weight: 700; line-height: 1; margin-bottom: 4px; }
  .ch-ma small { font-size: 13px; font-weight: 400; color: var(--csp-text-muted); }
  .ch-meta { font-size: 12px; color: var(--csp-text-muted); line-height: 1.8; }

  .section {
    background: var(--csp-surface);
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 18px;
    margin-bottom: 24px;
  }
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 16px;
  }
  .section-title {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .time-btns { display: flex; gap: 6px; flex-wrap: wrap; }
  .time-btns button {
    background: var(--gray-bg);
    border: 1px solid var(--csp-border);
    color: var(--csp-text);
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
  }
  .time-btns button:hover { border-color: rgba(43,43,43,0.28); }
  .time-btns button:focus-visible {
    outline: 2px solid var(--csp-accent);
    outline-offset: 2px;
  }
  .time-btns button.active {
    background: var(--csp-btn-dark);
    color: var(--csp-btn-dark-text);
    border-color: var(--csp-btn-dark);
  }
  .chart-wrap { position: relative; height: 260px; }
  .export-links { display: flex; gap: 10px; }
  .export-links a {
    font-size: 12px;
    font-weight: 500;
    color: #0369a1;
    text-decoration: none;
  }
  .export-links a:hover { text-decoration: underline; }
  .export-links a:focus-visible {
    outline: 2px solid var(--csp-accent);
    outline-offset: 2px;
    border-radius: 2px;
  }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th {
    text-align: left;
    color: var(--csp-text-muted);
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 6px 10px;
    border-bottom: 2px solid var(--csp-border);
  }
  td { padding: 8px 10px; border-bottom: 1px solid var(--csp-border); }
  tr:last-child td { border-bottom: none; }
  .ch-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
  }
  .ok { color: var(--green); font-weight: 600; }
  .low { color: var(--amber); font-weight: 600; }
  .err { color: var(--red); font-weight: 600; }
  .dry { color: var(--csp-text-muted); font-weight: 500; }

  .sim-badge {
    background: #d97706;
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 999px;
  }
  .ux-banner {
    background: var(--amber-bg);
    color: var(--amber);
    padding: 10px 20px;
    font-size: 13px;
    border-bottom: 1px solid var(--csp-border);
  }
  .feed-error {
    background: var(--red-bg);
    color: var(--red);
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
    border-bottom: 1px solid var(--csp-border);
  }
  .system-alerts {
    background: var(--red-bg);
    color: var(--red);
    padding: 12px 20px;
    font-size: 13px;
    border-bottom: 1px solid var(--csp-border);
    border-left: 4px solid var(--red);
  }
  .system-alerts strong {
    display: block;
    margin-bottom: 8px;
    font-weight: 700;
  }
  .system-alerts ul {
    margin: 0;
    padding-left: 1.25rem;
    line-height: 1.45;
  }
  .ch-sensor-err {
    display: none;
    margin-top: 6px;
    font-size: 11px;
    line-height: 1.35;
    color: var(--red);
    font-weight: 500;
    word-break: break-word;
  }
  .subhdr {
    background: var(--csp-surface);
    border-bottom: 1px solid var(--csp-border);
    padding: 10px 20px;
    font-size: 12px;
    color: var(--csp-text-muted);
    display: flex;
    flex-wrap: wrap;
    gap: 10px 18px;
    align-items: baseline;
  }
  .subhdr strong { color: var(--csp-text); font-weight: 600; }
  .subhdr .stale { color: var(--red); font-weight: 700; }
  .ch-extra {
    font-size: 11px;
    line-height: 1.65;
    color: var(--csp-text-muted);
    margin-top: 10px;
    border-top: 1px solid var(--csp-border);
    padding-top: 10px;
  }
  .ch-extra .sensor-ok { color: var(--green); font-weight: 600; }
  .ch-extra .sensor-bad { color: var(--red); font-weight: 600; }
  .ch-extra .elec-zero {
    display: inline-block;
    margin-top: 4px;
    padding: 2px 8px;
    border-radius: 6px;
    background: var(--gray-bg);
    font-weight: 600;
    color: var(--gray-text);
  }
  .chart-note {
    font-size: 12px;
    color: var(--csp-text-muted);
    line-height: 1.45;
    margin: 0 0 14px;
    max-width: 52rem;
  }
</style>
</head>
<body>

<header>
  <div class="status-dot" id="dot"></div>
  <h1>CoilShield ICCP</h1>
  <span id="sim-badge"></span>
  <span id="ts" style="font-size:13px; opacity:.85"></span>
  <div class="header-stats">
    <span id="hdr-supply"></span>
    <span id="hdr-total"></span>
    <span id="hdr-wet"></span>
    <span id="hdr-ref"></span>
    <span id="hdr-ref-hw" class="hdr-ref-sub"></span>
    <span id="hdr-temp"></span>
    <span id="hdr-fault" class="header-fault" style="display:none">⚠ FAULT LATCHED</span>
  </div>
</header>

<div id="feed-error" class="feed-error" role="alert" style="display:none"></div>
<div id="system-alerts" class="system-alerts" role="alert" style="display:none"></div>
<div class="subhdr" id="subhdr">
  <span id="hdr-feed"></span>
  <span id="hdr-cross"></span>
  <span id="hdr-anywet"></span>
  <span id="hdr-faults"></span>
  <span id="hdr-refhw"></span>
</div>

<div id="ux-banner" class="ux-banner" role="status" style="display:none"></div>

<main>
  <div class="ch-grid" id="ch-grid"></div>

  <div class="section">
    <div class="section-header">
      <span class="section-title" id="chart-section-title">Channel history</span>
      <div class="time-btns">
        <button type="button" onclick="setMetric('ma')" id="btn-metric-ma" class="active">mA</button>
        <button type="button" onclick="setMetric('impedance')" id="btn-metric-z">Ω</button>
      </div>
      <div class="time-btns">
        <button type="button" onclick="loadHistory(15)" id="btn-15">15m</button>
        <button type="button" onclick="loadHistory(60)" id="btn-60" class="active">1h</button>
        <button type="button" onclick="loadHistory(360)" id="btn-360">6h</button>
        <button type="button" onclick="loadHistory(1440)" id="btn-1440">24h</button>
      </div>
      <div class="export-links">
        <a href="/api/export/csv" download>↓ CSV</a>
        <a href="/api/export" download>↓ SQLite</a>
        <a href="/api/sessions?hours=720&amp;limit=5000" download="wet_sessions.json">↓ Wet sessions JSON</a>
      </div>
    </div>
    <p class="chart-note">The line trace is loaded from the database (auto downsampled). The same chart is pushed forward on each live poll (~400&nbsp;ms) from <code>latest.json</code>, so the right edge tracks the controller between DB refreshes. In mA mode, <strong>Total mA</strong> is the sum of all channels. Legend click-to-hide is disabled so traces stay visible.</p>
    <div class="chart-wrap"><canvas id="chart"></canvas></div>
  </div>

  <div class="section">
    <div class="section-header">
      <span class="section-title">Today's cumulative protection</span>
      <span style="font-size:12px;color:var(--csp-text-muted)">mA·s while PROTECTING; charge (C) = mA·s ÷ 1000</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Channel</th>
          <th>Wet time today</th>
          <th>mA·s (protecting)</th>
          <th>Charge (C)</th>
        </tr>
      </thead>
      <tbody id="daily-body"></tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-header">
      <span class="section-title">Statistics — today</span>
      <span id="stats-since" style="font-size:12px;color:var(--csp-text-muted)"></span>
      <span style="font-size:11px;color:var(--csp-text-muted)">† Ref Δ / temp: today’s average across all ticks (same value per row).</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Channel</th>
          <th>State</th>
          <th>Protecting today</th>
          <th>Avg mA</th>
          <th>Coverage</th>
          <th>Wet cycles</th>
          <th>Bus V avg</th>
          <th>Avg Z (Ω)</th>
          <th>Ref shift (mV)†</th>
          <th>Temp (°F)†</th>
        </tr>
      </thead>
      <tbody id="stats-body"></tbody>
    </table>
  </div>
</main>

<script>
const CH_COLORS = ['var(--ch0)','var(--ch1)','var(--ch2)','var(--ch3)','var(--ch4)'];
const NUM_CH = __NUM_CH__;
let chart = null;
let activeMinutes = 60;
let chartMetric = 'ma';

const grid = document.getElementById('ch-grid');
for (let i = 0; i < NUM_CH; i++) {
  grid.innerHTML += `
    <div class="ch-card" id="card-${i}">
      <div class="ch-label">
        <span class="ch-dot" style="background:${CH_COLORS[i]}"></span>Channel ${i+1}
      </div>
      <div class="ch-state state-OPEN" id="state-${i}">OPEN</div>
      <div class="ch-ma" id="ma-${i}">— <small>mA</small></div>
      <div class="ch-meta">
        Duty: <span id="duty-${i}">—</span>%<br>
        Bus: <span id="busv-${i}">—</span> V<br>
        Z: <span id="z-${i}">—</span> Ω · Vcell: <span id="vcell-${i}">—</span> V<br>
        P: <span id="pow-${i}">—</span> W · E: <span id="enj-${i}">—</span> J<br>
        η: <span id="eff-${i}">—</span> mA/% · Status: <span id="status-${i}">—</span>
      </div>
      <div class="ch-extra">
        <span id="sens-line-${i}">Sensor: <span id="sens-${i}">—</span></span>
        <div class="ch-sensor-err" id="ch-err-${i}"></div>
        · Q today: <span id="coul-${i}">—</span> C<br>
        ΔZ: <span id="dz-${i}">—</span> Ω · Zσ: <span id="zstd-${i}">—</span> Ω<br>
        σ*: <span id="sigma-${i}">—</span> s · FQI: <span id="fqi-${i}">—</span> (raw <span id="fqir-${i}">—</span>)<br>
        dZ/dt: <span id="zrate-${i}">—</span> Ω/s · dV/dI: <span id="dvd-${i}">—</span> Ω<br>
        <span id="surf-${i}">—</span>
        <span id="zero-flag-${i}" style="display:none" class="elec-zero"></span>
      </div>
    </div>`;
}

const ctx = document.getElementById('chart').getContext('2d');
const TOTAL_DS = NUM_CH + 1;
const MAX_CHART_POINTS = 2400;

chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: 'Target', data: [], borderColor: '#94a3b8',
        borderDash: [5, 5], borderWidth: 1.5, pointRadius: 0,
        fill: false, tension: 0, hidden: false,
      },
      ...Array.from({length: NUM_CH}, (_, i) => ({
        label: `CH${i+1}`,
        data: [], borderColor: CH_COLORS[i],
        backgroundColor: 'transparent',
        borderWidth: 1.5, pointRadius: 0, tension: 0.2, fill: false, hidden: false,
      })),
      {
        label: 'Total mA',
        data: [],
        borderColor: '#475569',
        borderWidth: 1.8,
        pointRadius: 0,
        tension: 0.15,
        fill: false,
        hidden: false,
      },
    ]
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'top',
        labels: { boxWidth: 12, font: { size: 12 } },
        /* Avoid click-to-hide: an empty chart when all series are toggled off. */
        onClick: () => {},
      },
      tooltip: { callbacks: { label: ctx => {
        const u = chartMetric === 'impedance' ? 'Ω' : 'mA';
        const y = ctx.parsed.y;
        return ` ${ctx.dataset.label}: ${y != null ? y.toFixed(2) : '—'} ${u}`;
      }}}
    },
    scales: {
      x: { ticks: { maxTicksLimit: 12, font: { size: 11 }, color: '#5c5c5c' }, grid: { color: 'rgba(43,43,43,0.08)' } },
      y: {
        title: { display: true, text: 'mA', font: { size: 11 }, color: '#5c5c5c' },
        min: 0,
        grace: '8%',
        ticks: { font: { size: 11 }, color: '#5c5c5c' },
        grid: { color: 'rgba(43,43,43,0.08)' }
      }
    }
  }
});

function syncChartLiveTail(d) {
  if (!chart || !d.channels || d.error) return;
  const lab = (d.ts && d.ts.length >= 19) ? d.ts.slice(11, 19) : '';
  if (!lab) return;

  const tgtMa = (d.target_ma != null && d.target_ma !== '')
    ? Number(d.target_ma) : null;

  function yForCh(ch) {
    if (!ch) return null;
    if (chartMetric === 'impedance') {
      const z = ch.impedance_ohm;
      return (typeof z === 'number' && Number.isFinite(z)) ? z : null;
    }
    const ma = Number(ch.ma);
    return Number.isFinite(ma) ? ma : null;
  }

  function pushArrays(tgt, totMa) {
    chart.data.labels.push(lab);
    chart.data.datasets[0].data.push(chartMetric === 'ma' ? tgt : null);
    for (let i = 0; i < NUM_CH; i++) {
      chart.data.datasets[i + 1].data.push(yForCh(d.channels[String(i)]));
    }
    chart.data.datasets[TOTAL_DS].data.push(
      chartMetric === 'ma' && totMa != null && Number.isFinite(totMa) ? totMa : null
    );
  }

  function writeAt(idx, tgt, totMa) {
    chart.data.datasets[0].data[idx] = chartMetric === 'ma' ? tgt : null;
    for (let i = 0; i < NUM_CH; i++) {
      chart.data.datasets[i + 1].data[idx] = yForCh(d.channels[String(i)]);
    }
    chart.data.datasets[TOTAL_DS].data[idx] =
      chartMetric === 'ma' && totMa != null && Number.isFinite(totMa) ? totMa : null;
  }

  const n = chart.data.labels.length;
  const totMa = Number(d.total_ma);
  const tgt = chartMetric === 'ma' ? tgtMa : null;

  if (n > 0 && chart.data.labels[n - 1] === lab) {
    writeAt(n - 1, tgt, totMa);
  } else {
    pushArrays(tgt, totMa);
    while (chart.data.labels.length > MAX_CHART_POINTS) {
      chart.data.labels.shift();
      for (let di = 0; di < chart.data.datasets.length; di++) {
        chart.data.datasets[di].data.shift();
      }
    }
  }
  chart.update('none');
}

function fmtOpt(x, digits, suffix = '') {
  if (x == null || x === '') return '—';
  const n = Number(x);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(digits) + suffix;
}

function paintChannelUnknown(i) {
  const stateEl = document.getElementById(`state-${i}`);
  stateEl.textContent = 'UNKNOWN';
  stateEl.className = 'ch-state state-UNKNOWN';
  document.getElementById(`ma-${i}`).innerHTML = '— <small>mA</small>';
  document.getElementById(`duty-${i}`).textContent = '—';
  document.getElementById(`busv-${i}`).textContent = '—';
  document.getElementById(`z-${i}`).textContent = '—';
  document.getElementById(`vcell-${i}`).textContent = '—';
  const st = document.getElementById(`status-${i}`);
  st.textContent = '—';
  st.className = '';
  document.getElementById(`pow-${i}`).textContent = '—';
  document.getElementById(`enj-${i}`).textContent = '—';
  document.getElementById(`eff-${i}`).textContent = '—';
  document.getElementById(`sens-${i}`).textContent = '—';
  document.getElementById(`sens-${i}`).className = '';
  document.getElementById(`coul-${i}`).textContent = '—';
  document.getElementById(`dz-${i}`).textContent = '—';
  document.getElementById(`zstd-${i}`).textContent = '—';
  document.getElementById(`sigma-${i}`).textContent = '—';
  document.getElementById(`fqi-${i}`).textContent = '—';
  document.getElementById(`fqir-${i}`).textContent = '—';
  document.getElementById(`zrate-${i}`).textContent = '—';
  document.getElementById(`dvd-${i}`).textContent = '—';
  document.getElementById(`surf-${i}`).textContent = '—';
  const zf = document.getElementById(`zero-flag-${i}`);
  zf.style.display = 'none';
  zf.textContent = '';
  const cerr = document.getElementById(`ch-err-${i}`);
  if (cerr) {
    cerr.style.display = 'none';
    cerr.textContent = '';
  }
}

async function fetchLive() {
  try {
    const d = await fetch('/api/live', { cache: 'no-store' }).then(r => r.json());
    const errEl = document.getElementById('feed-error');
    if (d.error) {
      errEl.style.display = '';
      errEl.textContent = d.error;
      window._dashLastLive = null;
      document.getElementById('hdr-feed').textContent = '';
      document.getElementById('hdr-cross').textContent = '';
      document.getElementById('hdr-anywet').textContent = '';
      document.getElementById('hdr-faults').textContent = '';
      document.getElementById('hdr-refhw').textContent = '';
      document.getElementById('dot').style.background = '#94a3b8';
      const sal0 = document.getElementById('system-alerts');
      sal0.style.display = 'none';
      sal0.innerHTML = '';
      for (let i = 0; i < NUM_CH; i++) paintChannelUnknown(i);
      return;
    }
    errEl.style.display = 'none';
    errEl.textContent = '';
    window._dashLastLive = d;

    const age = d.feed_age_s;
    const thr = d.feed_stale_threshold_s ?? 3;
    const stale = typeof age === 'number' && age > thr;
    const feedSpan = document.getElementById('hdr-feed');
    if (typeof age === 'number') {
      feedSpan.innerHTML = '<strong>Feed</strong> · last write to latest.json <strong>' + age.toFixed(2) + 's</strong> ago' +
        (stale ? ' <span class="stale">· STALE — controller may be stopped</span>' : '');
    } else {
      feedSpan.textContent = 'Feed: —';
    }
    const dot = document.getElementById('dot');
    dot.style.background = stale ? '#fbbf24' : (d.fault_latched ? '#f87171' : '#4ade80');

    const cr = d.cross || {};
    const icv = cr.i_cv, zcv = cr.z_cv;
    document.getElementById('hdr-cross').textContent =
      (icv != null && icv !== '') || (zcv != null && zcv !== '')
        ? `Cross-channel · I_CV ${fmtOpt(icv, 4)} · Z_CV ${fmtOpt(zcv, 4)}`
        : 'Cross-channel · —';
    document.getElementById('hdr-anywet').textContent =
      'Any anode wet (sense): ' + (d.wet ? 'yes' : 'no');
    const fl = Array.isArray(d.faults) ? d.faults : [];
    document.getElementById('hdr-faults').textContent =
      fl.length ? ('Active faults: ' + fl.join(' · ')) : 'Active faults: none';
    document.getElementById('hdr-refhw').textContent =
      d.ref_hw_ok === true ? 'Ref HW: OK' : d.ref_hw_ok === false ? 'Ref HW: problem' : 'Ref HW: —';

    const tsExtra = (d.ts_unix != null && d.ts_unix !== '')
      ? ' · tick unix ' + d.ts_unix : '';
    document.getElementById('ts').textContent = (d.ts || '—') + tsExtra;
    const sup = d.supply_v_avg;
    document.getElementById('hdr-supply').textContent =
      (sup != null && sup !== '') ? `Supply: ${fmtOpt(sup, 3)}V avg (channels with bus>0)` : 'Supply: —';
    document.getElementById('hdr-total').textContent =
      `Total: ${fmtOpt(d.total_ma, 4)}mA · ${(d.total_power_w != null && d.total_power_w !== '') ? Number(d.total_power_w).toFixed(3) + 'W' : '—'}`;
    document.getElementById('hdr-wet').textContent =
      `PROTECTING count: ${d.wet_channels}/__NUM_CH__`;
    const raw = (d.ref_raw_mv != null && d.ref_raw_mv !== '')
      ? `${Number(d.ref_raw_mv).toFixed(1)} mV` : '—';
    const sh = (d.ref_shift_mv != null && d.ref_shift_mv !== '')
      ? `${Number(d.ref_shift_mv).toFixed(1)} mV` : '—';
    const bd = d.ref_status || '—';
    document.getElementById('hdr-ref').textContent =
      `Ref ${raw} · shift ${sh} · band ${bd}`;
    const hwEl = document.getElementById('hdr-ref-hw');
    const hw = d.ref_hw_message || '';
    const bl = d.ref_baseline_set ? 'baseline: yes' : 'baseline: no';
    hwEl.textContent = hw ? `${hw} · ${bl}` : bl;
    const ban = document.getElementById('ux-banner');
    if (d.ref_hint) {
      ban.style.display = '';
      ban.textContent = d.ref_hint;
    } else {
      ban.style.display = 'none';
      ban.textContent = '';
    }
    document.getElementById('hdr-temp').textContent =
      d.temp_f != null && d.temp_f !== '' ? `${d.temp_f}°F` : '';
    const faultEl = document.getElementById('hdr-fault');
    faultEl.style.display = d.fault_latched ? '' : 'none';

    const sal = document.getElementById('system-alerts');
    sal.innerHTML = '';
    const alerts = Array.isArray(d.system_alerts) ? d.system_alerts.filter(Boolean) : [];
    if (alerts.length) {
      sal.style.display = '';
      const t = document.createElement('strong');
      t.textContent = 'Component alerts';
      sal.appendChild(t);
      const ul = document.createElement('ul');
      alerts.forEach((a) => {
        const li = document.createElement('li');
        li.textContent = a;
        ul.appendChild(li);
      });
      sal.appendChild(ul);
    } else {
      sal.style.display = 'none';
    }

    const badge = document.getElementById('sim-badge');
    if (d.sim_time) {
      badge.className = 'sim-badge';
      badge.textContent = `SIM ${d.sim_time}`;
    } else {
      badge.className = '';
      badge.textContent = '';
    }

    for (let i = 0; i < NUM_CH; i++) {
      const ch = (d.channels && d.channels[String(i)]) ? d.channels[String(i)] : {};
      let readingOk = ch.reading_ok;
      if (readingOk === undefined) readingOk = ch.status !== 'ERR';

      const stateEl = document.getElementById(`state-${i}`);
      const stName = ch.state || 'UNKNOWN';
      stateEl.textContent = stName;
      stateEl.className = 'ch-state state-' + stName;
      const maNum = Number(ch.ma);
      const maDisp = Number.isFinite(maNum) ? maNum : 0;
      document.getElementById(`ma-${i}`).innerHTML =
        `${maDisp.toFixed(3)} <small>mA</small>`;
      document.getElementById(`duty-${i}`).textContent = fmtOpt(ch.duty, 1);
      const busV = Number(ch.bus_v);
      document.getElementById(`busv-${i}`).textContent = fmtOpt(ch.bus_v, 3);
      const z = ch.impedance_ohm;
      document.getElementById(`z-${i}`).textContent =
        (typeof z === 'number' && Number.isFinite(z)) ? z.toFixed(0) : '—';
      const vc = ch.cell_voltage_v;
      document.getElementById(`vcell-${i}`).textContent =
        (typeof vc === 'number' && Number.isFinite(vc)) ? vc.toFixed(3) : '—';
      const st = document.getElementById(`status-${i}`);
      const stt = ch.status || '—';
      st.textContent = stt;
      st.className = stt === 'OK' ? 'ok'
        : stt === 'ERR' ? 'err'
        : (stt === 'DRY' || stt === 'OPEN') ? 'dry' : 'low';
      const pw = ch.power_w;
      document.getElementById(`pow-${i}`).textContent =
        (typeof pw === 'number' && Number.isFinite(pw)) ? pw.toFixed(4) : '—';
      const ej = ch.energy_today_j;
      document.getElementById(`enj-${i}`).textContent =
        (typeof ej === 'number' && Number.isFinite(ej)) ? ej.toFixed(2) : '—';
      const ef = ch.efficiency_ma_per_pct;
      document.getElementById(`eff-${i}`).textContent =
        (typeof ef === 'number' && Number.isFinite(ef)) ? ef.toFixed(3) : '—';

      const sens = document.getElementById(`sens-${i}`);
      sens.textContent = readingOk ? 'OK' : 'NO READ';
      sens.className = readingOk ? 'sensor-ok' : 'sensor-bad';
      const se = (ch.sensor_error && String(ch.sensor_error).trim()) || '';
      const cerr = document.getElementById(`ch-err-${i}`);
      if (cerr) {
        if (se) {
          cerr.style.display = 'block';
          cerr.textContent = se;
        } else {
          cerr.style.display = 'none';
          cerr.textContent = '';
        }
      }
      document.getElementById(`coul-${i}`).textContent = fmtOpt(ch.coulombs_today_c, 6);
      document.getElementById(`dz-${i}`).textContent = fmtOpt(ch.z_delta_ohm, 2);
      document.getElementById(`zstd-${i}`).textContent = fmtOpt(ch.z_std_ohm, 3);
      document.getElementById(`sigma-${i}`).textContent = fmtOpt(ch.sigma_proxy_s, 2);
      document.getElementById(`fqi-${i}`).textContent = fmtOpt(ch.fqi_smooth_s, 6);
      document.getElementById(`fqir-${i}`).textContent = fmtOpt(ch.fqi_raw_s, 6);
      document.getElementById(`zrate-${i}`).textContent = fmtOpt(ch.z_rate_ohm_s, 4);
      document.getElementById(`dvd-${i}`).textContent = fmtOpt(ch.dV_dI_ohm, 2);
      const hint = (ch.surface_hint && String(ch.surface_hint).trim()) || '—';
      document.getElementById(`surf-${i}`).textContent = hint;

      const zf = document.getElementById(`zero-flag-${i}`);
      const busOk = Number.isFinite(busV);
      if (readingOk && Math.abs(maDisp) < 0.001 && busOk && Math.abs(busV) < 0.05) {
        zf.style.display = 'inline-block';
        zf.textContent = 'Live: 0 mA and ~0 V bus (path open / supply off / INA219 idle)';
      } else if (readingOk && Math.abs(maDisp) < 0.001) {
        zf.style.display = 'inline-block';
        zf.textContent = 'Live: 0 mA (sensor OK — check wet state and bus voltage)';
      } else {
        zf.style.display = 'none';
        zf.textContent = '';
      }
    }
    syncChartLiveTail(d);
  } catch (e) {
    const errEl = document.getElementById('feed-error');
    errEl.style.display = '';
    errEl.textContent = 'Network error loading /api/live';
    window._dashLastLive = null;
    document.getElementById('dot').style.background = '#94a3b8';
    const salE = document.getElementById('system-alerts');
    salE.style.display = 'none';
    salE.innerHTML = '';
  }
}

function setMetric(m) {
  chartMetric = m;
  document.getElementById('btn-metric-ma').className = m === 'ma' ? 'active' : '';
  document.getElementById('btn-metric-z').className = m === 'impedance' ? 'active' : '';
  document.getElementById('chart-section-title').textContent =
    m === 'impedance' ? 'Channel history (impedance Ω)' : 'Channel history (current mA)';
  chart.options.scales.y.title.text = m === 'impedance' ? 'Ω' : 'mA';
  if (m === 'impedance') {
    delete chart.options.scales.y.min;
    chart.options.scales.y.grace = '10%';
  } else {
    chart.options.scales.y.min = 0;
    chart.options.scales.y.grace = '8%';
  }
  loadHistory(activeMinutes);
}

async function loadHistory(minutes) {
  activeMinutes = minutes;
  ['15','60','360','1440'].forEach(m => {
    const b = document.getElementById(`btn-${m}`);
    if (b) b.className = String(m) === String(minutes) ? 'active' : '';
  });
  try {
    const d = await fetch(`/api/history?minutes=${minutes}&metric=${chartMetric}`, { cache: 'no-store' }).then(r => r.json());
    if (d.error) return;
    chart.data.labels = d.labels;
    const tgt = d.target;
    chart.data.datasets[0].hidden = tgt == null;
    chart.data.datasets[0].data = d.labels.map(() => tgt);
    for (let i = 0; i < NUM_CH; i++) {
      chart.data.datasets[i + 1].data = d.channels[String(i)] || [];
    }
    const totalArr = d.total || [];
    if (chartMetric === 'ma') {
      chart.data.datasets[TOTAL_DS].hidden = false;
      chart.data.datasets[TOTAL_DS].data = d.labels.map((_, j) => {
        const v = totalArr[j];
        return (v != null && v !== '') ? Number(v) : null;
      });
    } else {
      chart.data.datasets[TOTAL_DS].hidden = true;
      chart.data.datasets[TOTAL_DS].data = d.labels.map(() => null);
    }
    chart.update('none');
    if (window._dashLastLive) syncChartLiveTail(window._dashLastLive);
  } catch (e) {}
}

function fmtSecs(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

async function fetchStats() {
  try {
    const d = await fetch('/api/stats').then(r => r.json());
    if (d.error) return;
    const tbody = document.getElementById('stats-body');
    const totalH = (d.total_ticks * d.sample_s / 3600).toFixed(1);
    document.getElementById('stats-since').textContent =
      `${totalH}h of data collected today`;
    tbody.innerHTML = d.stats.map(s => `
      <tr>
        <td><span class="ch-dot" style="background:${CH_COLORS[s.ch-1]}"></span>CH${s.ch}</td>
        <td id="stat-state-${s.ch}">—</td>
        <td>${fmtSecs(s.protecting_s)}</td>
        <td>${s.avg_ma.toFixed(3)} mA</td>
        <td>${s.protecting_pct}%</td>
        <td>${s.wet_cycles}</td>
        <td>${s.avg_bus_v.toFixed(2)} V</td>
        <td>${Number(s.avg_impedance_ohm ?? 0).toFixed(0)}</td>
        <td>${s.ref_shift_mv != null ? Number(s.ref_shift_mv).toFixed(1) : '—'}</td>
        <td>${s.temp_f != null ? Number(s.temp_f).toFixed(1) : '—'}</td>
      </tr>
    `).join('');
  } catch (e) {}
}

async function fetchDaily() {
  try {
    const d = await fetch('/api/daily').then(r => r.json());
    if (d.error) return;
    const tbody = document.getElementById('daily-body');
    if (!d.channels || Object.keys(d.channels).length === 0) {
      tbody.innerHTML = '<tr><td colspan="4">No daily totals yet (controller running?)</td></tr>';
      return;
    }
    tbody.innerHTML = Array.from({length: NUM_CH}, (_, i) => i).map(i => {
      const c = d.channels[String(i)] || { ma_s: 0, wet_s: 0 };
      const q = (c.ma_s || 0) / 1000;
      return `<tr>
        <td><span class="ch-dot" style="background:${CH_COLORS[i]}"></span>CH${i+1}</td>
        <td>${fmtSecs(c.wet_s || 0)}</td>
        <td>${(c.ma_s || 0).toFixed(0)}</td>
        <td>${q.toFixed(4)}</td>
      </tr>`;
    }).join('');
  } catch (e) {}
}

setInterval(fetchLive, 400);
setInterval(() => loadHistory(activeMinutes), 2000);
setInterval(fetchStats, 5000);
setInterval(fetchDaily, 15000);
fetchLive();
loadHistory(60);
fetchStats();
fetchDaily();
</script>
</body>
</html>
"""

DASHBOARD_HTML = DASHBOARD_HTML.replace("__NUM_CH__", str(cfg.NUM_CHANNELS))


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


def main() -> None:
    p = argparse.ArgumentParser(description="CoilShield web dashboard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    _warn_sqlite_lag_support()
    print(f"CoilShield dashboard: http://127.0.0.1:{args.port} (bind {args.host}:{args.port})")
    print(f"Reading from: {cfg.LOG_DIR}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
