#!/usr/bin/env python3
"""
CoilShield ICCP — web dashboard.

Run alongside the controller (``iccp start``):
    iccp dashboard

Access from any device on the same network:
    http://<pi-ip>:8080

Reads from the same ``config.settings`` as the controller. Telemetry directory:

- **Environment:** ``COILSHIELD_LOG_DIR`` or ``ICCP_LOG_DIR`` (absolute path recommended).
- **CLI (before config import):** ``iccp dashboard --log-dir /abs/path/to/logs --host 0.0.0.0``

If ``COILSHIELD_LOG_DIR`` / ``ICCP_LOG_DIR`` are **unset**, the dashboard (Linux) tries to
inherit the same directory as a running ``iccp start`` process (via ``/proc``). You can still
override with ``--log-dir`` or env. If the feed is stale while the controller runs, set
``--log-dir`` explicitly to the path the controller uses.

Direct execution (``python3 dashboard.py``) is not supported — it prints a redirect and
exits. The module stays importable so ``iccp dashboard`` can drive it.

**UI:** Dark theme tracks v77 *marketing* ``global.css`` (``#05070a`` background, sky accent).
Vendored **Geist** variable fonts are served from ``/static/fonts/`` (``static/`` next to
this file) for offline / Pi use — no font CDN. HTML/CSS lives in this module; avoid duplicating
feed-health copy between Overview and Health (diagnostics are under a ``<details>`` block).

HTTP: ``/api/meta`` (controller layout + package version), ``/api/live`` (Cache-Control: no-store;
feed_age_s, json_payload_age_s, feed_stale_threshold_s, feed_trust_channel_metrics,
feed_stale_reasons, target_ma_avg_live), ``/api/diagnostic``, ``/api/history`` (avg_target_ma for mA
charts), ``/api/stats``, ``/api/daily``, ``/api/sessions``, ``/api/export``, ``/api/export/csv``.
**CORS:** all ``/api/*`` responses include permissive headers so Electron / Vite dev servers can
``fetch`` a tunneled ``http://127.0.0.1:<port>/api/...`` from another origin. See
``docs/desktop-app-integration.md``.

SQL column names for `readings` / `wet_sessions` / `daily_totals` MUST stay in sync with logger.py _init_schema.

Install Flask if needed:
    pip install flask --break-system-packages
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


def _apply_dashboard_argv_log_dir() -> None:
    """Set COILSHIELD_LOG_DIR from argv before ``import config.settings`` (LOG_DIR is fixed at import)."""
    from config.argv_log_dir import (
        apply_coilshield_log_dir_from_argv,
        apply_coilshield_log_dir_from_running_controller_if_unset,
    )

    apply_coilshield_log_dir_from_argv(sys.argv[1:])
    apply_coilshield_log_dir_from_running_controller_if_unset()


_apply_dashboard_argv_log_dir()

from config.argv_channels import apply_coilshield_active_channels_from_argv

if apply_coilshield_active_channels_from_argv(sys.argv[1:]) == 2:
    raise SystemExit(2)

from flask import Flask, Response, jsonify, make_response, request, send_file

import config.settings as cfg
from telemetry_queries import (
    HISTORY_MINUTES_DEFAULT as _HISTORY_MINUTES_DEFAULT,
    HISTORY_MINUTES_MAX as _HISTORY_MINUTES_MAX,
    history_payload as _telemetry_history_payload,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT
app = Flask(
    __name__,
    static_folder=str(_DASHBOARD_DIR / "static"),
    static_url_path="/static",
)


@app.before_request
def _dashboard_api_cors_preflight() -> Response | None:
    """Allow browser/Electron renderers on another origin to call read-only ``/api/*`` GET APIs."""
    if request.method != "OPTIONS":
        return None
    if not str(request.path).startswith("/api/"):
        return None
    r = make_response("", 204)
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Max-Age"] = "86400"
    return r


@app.after_request
def _dashboard_api_cors_headers(resp: Response) -> Response:
    if str(request.path).startswith("/api/"):
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault("Access-Control-Allow-Methods", "GET, OPTIONS")
    return resp


# /api/history: cap window so bad params cannot load unbounded rows into memory.
_HISTORY_MINUTES_DEFAULT = _HISTORY_MINUTES_DEFAULT
_HISTORY_MINUTES_MAX = _HISTORY_MINUTES_MAX

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
        return {"error": "no data yet — is the controller (iccp start) running?"}


def _package_version() -> str | None:
    try:
        return importlib.metadata.version("coilshield-iccp")
    except importlib.metadata.PackageNotFoundError:
        return None


@app.route("/api/meta")
def api_meta():
    """Build and channel layout for external desktop clients (no ``latest.json`` required)."""
    paths = cfg.resolved_telemetry_paths()
    body = {
        "package": "coilshield-iccp",
        "package_version": _package_version(),
        "num_channels": int(cfg.NUM_CHANNELS),
        "target_ma": float(cfg.TARGET_MA),
        "max_ma": float(cfg.MAX_MA),
        "sample_interval_s": float(cfg.SAMPLE_INTERVAL_S),
        "pwm_frequency_hz": int(getattr(cfg, "PWM_FREQUENCY_HZ", 0) or 0),
        "sim_mode": os.environ.get("COILSHIELD_SIM", "0").strip() == "1",
        "log_dir": paths.get("log_dir"),
        "latest_json": paths.get("latest_json"),
        "sqlite_db": paths.get("sqlite_db"),
    }
    resp = make_response(jsonify(body))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _live_envelope() -> dict:
    """Payload for /api/live: latest.json plus feed-health metadata for the UI.

    ``feed_trust_channel_metrics`` is True only when the file mtime is fresh, the
    JSON ``ts_unix`` is not older than the same threshold, and
    ``telemetry_incomplete`` is false (full ``record()`` wrote channel data).
    """
    data = _latest()
    now = time.time()
    thr = float(cfg.latest_feed_stale_threshold_s())
    try:
        st = LATEST_PATH.stat()
        data["feed_file_mtime_unix"] = round(st.st_mtime, 6)
        data["feed_age_s"] = round(now - st.st_mtime, 3)
    except OSError:
        data["feed_file_mtime_unix"] = None
        data["feed_age_s"] = None
    data["sample_interval_s"] = float(cfg.SAMPLE_INTERVAL_S)
    data["feed_stale_threshold_s"] = thr
    tsu = data.get("ts_unix")
    json_payload_age_s: float | None = None
    if isinstance(tsu, (int, float)) and not isinstance(tsu, bool):
        try:
            json_payload_age_s = max(0.0, now - float(tsu))
        except (TypeError, ValueError, OverflowError):
            pass
    data["json_payload_age_s"] = (
        round(json_payload_age_s, 3) if json_payload_age_s is not None else None
    )
    incomplete = bool(data.get("telemetry_incomplete"))
    file_age = data.get("feed_age_s")
    file_stale = isinstance(file_age, (int, float)) and float(file_age) > thr
    json_stale = (
        json_payload_age_s is not None
        and json_payload_age_s > thr * 1.01
    )
    reasons: list[str] = []
    if file_stale:
        reasons.append("file_mtime")
    if json_stale:
        reasons.append("json_ts")
    if incomplete:
        reasons.append("telemetry_incomplete")
    data["feed_stale_reasons"] = reasons
    data["feed_trust_channel_metrics"] = len(reasons) == 0
    data["feed_ok"] = len(reasons) == 0
    data["target_ma"] = float(cfg.TARGET_MA)
    # Mean of per-channel effective targets from the snapshot (matches controller setpoints).
    tgts: list[float] = []
    chs = data.get("channels")
    if isinstance(chs, dict):
        for i in range(cfg.NUM_CHANNELS):
            c = chs.get(str(i))
            if not isinstance(c, dict):
                continue
            raw = c.get("target_ma")
            if raw is None or raw == "":
                continue
            try:
                tgts.append(float(raw))
            except (TypeError, ValueError):
                continue
    data["target_ma_avg_live"] = (
        round(sum(tgts) / len(tgts), 4) if tgts else None
    )
    data["telemetry_paths"] = cfg.resolved_telemetry_paths()
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
    try:
        minutes = int(request.args.get("minutes", _HISTORY_MINUTES_DEFAULT))
    except (TypeError, ValueError):
        minutes = _HISTORY_MINUTES_DEFAULT
    minutes = max(1, min(minutes, _HISTORY_MINUTES_MAX))
    metric = request.args.get("metric", "ma").lower()
    if metric not in ("ma", "impedance"):
        metric = "ma"

    try:
        body = _telemetry_history_payload(minutes, metric=metric)
    except Exception:
        return jsonify({"error": "database not ready"}), 503
    if body.get("error"):
        return jsonify({"error": body["error"]}), 503

    resp = make_response(jsonify(body))
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
  /* v77 main-site dark; Geist in /static/fonts (vend from npm `geist` 1.3.1) */
  @font-face {
    font-family: "Geist";
    src: url("/static/fonts/Geist-Variable.woff2") format("woff2");
    font-weight: 100 900;
    font-style: normal;
    font-display: swap;
  }
  @font-face {
    font-family: "Geist Mono";
    src: url("/static/fonts/GeistMono-Variable.woff2") format("woff2");
    font-weight: 100 900;
    font-style: normal;
    font-display: swap;
  }
  :root {
    --csp-bg: #05070a;
    --csp-surface: #0c0f16;
    --csp-surface-elevated: #111827;
    --csp-surface-strong: #1e293b;
    --csp-text: #e4e4e7;
    --csp-text-muted: #a1a1aa;
    --csp-text-subtle: #71717a;
    --csp-border: rgba(255, 255, 255, 0.1);
    --csp-border-strong: rgba(255, 255, 255, 0.16);
    --csp-accent: #7dd3fc;
    --csp-accent-strong: #38bdf8;
    --csp-link: #38bdf8;
    --csp-btn-dark: #0f172a;
    --csp-btn-dark-text: #f1f5f9;
    --csp-radius: 0.4rem;
    --green: #4ade80;
    --green-bg: rgba(34, 197, 94, 0.12);
    --amber: #fbbf24;
    --amber-bg: rgba(234, 179, 8, 0.12);
    --blue: #60a5fa;
    --blue-bg: rgba(59, 130, 246, 0.12);
    --red: #f87171;
    --red-bg: rgba(248, 113, 113, 0.12);
    --gray-text: #94a3b8;
    --gray-bg: #1e293b;
    --ch0: #38bdf8;
    --ch1: #34d399;
    --ch2: #fbbf24;
    --ch3: #f87171;
    --ch4: #a78bfa;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { color-scheme: dark; }
  body {
    font-family: "Geist", system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif;
    background: var(--csp-bg);
    color: var(--csp-text);
    font-size: 14px;
  }
  code, .telemetry-paths-line code { font-family: "Geist Mono", ui-monospace, monospace; }

  header {
    background: var(--csp-surface-strong);
    color: var(--csp-text);
    border-bottom: 1px solid var(--csp-border);
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
  main {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
    min-width: 0;
  }

  .skip-link {
    position: absolute;
    left: -9999px;
    top: auto;
    width: 1px;
    height: 1px;
    overflow: hidden;
  }
  .skip-link:focus {
    position: fixed;
    left: 12px;
    top: 12px;
    width: auto;
    height: auto;
    padding: 8px 14px;
    background: var(--csp-btn-dark);
    color: var(--csp-btn-dark-text);
    z-index: 10000;
    border-radius: 6px;
  }

  .dash-nav {
    position: sticky;
    top: 0;
    z-index: 50;
    background: var(--csp-surface);
    border-bottom: 1px solid var(--csp-border);
    padding: 8px 20px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    align-items: center;
    box-shadow: 0 1px 0 rgba(0,0,0,0.35);
  }
  .dash-nav a {
    font-size: 12px;
    font-weight: 600;
    color: var(--csp-link);
    text-decoration: none;
    padding: 4px 2px;
    border-radius: 4px;
  }
  .dash-nav a:hover { text-decoration: underline; color: var(--csp-accent); }
  .dash-nav a:focus-visible {
    outline: 2px solid var(--csp-accent);
    outline-offset: 2px;
  }

  .status-pill {
    font-size: 12px;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 999px;
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.25);
  }
  .header-ts { font-size: 13px; opacity: 0.88; margin-left: auto; }

  .kpi-section .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 14px;
    margin-top: 4px;
  }
  .kpi-tile {
    background: var(--csp-surface-elevated);
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.25);
  }
  .kpi-tile h3 {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--csp-text-muted);
    margin-bottom: 8px;
  }
  .kpi-tile .kpi-value {
    font-size: 1.65rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    line-height: 1.15;
  }
  .kpi-tile .kpi-caption {
    font-size: 11px;
    color: var(--csp-text-muted);
    margin-top: 6px;
    line-height: 1.35;
  }

  .feed-status-wrap {
    margin-top: 16px;
    background: var(--csp-surface-elevated);
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 0;
    box-shadow: 0 1px 2px rgba(0,0,0,0.2);
    overflow: hidden;
  }
  .feed-status-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px 16px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--csp-border);
  }
  .feed-line {
    font-size: 13px;
    color: var(--csp-text-muted);
    flex: 1;
    min-width: 0;
    line-height: 1.4;
  }
  .feed-diag-details {
    padding: 0;
  }
  .feed-diag-details > summary {
    cursor: pointer;
    list-style: none;
    font-size: 12px;
    font-weight: 600;
    padding: 10px 16px;
    color: var(--csp-accent-strong);
    user-select: none;
  }
  .feed-diag-details > summary::-webkit-details-marker { display: none; }
  .feed-diag-details > summary::before { content: "▸ "; opacity: 0.7; }
  .feed-diag-details[open] > summary::before { content: "▾ "; }
  .feed-diag-body {
    padding: 0 16px 16px;
    border-top: 1px solid var(--csp-border);
  }
  .feed-contract-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 16px 20px;
    align-items: flex-start;
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 12px 0 0;
    box-shadow: none;
  }
  .telemetry-diag {
    font-size: 12px;
    color: var(--csp-text-muted);
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed var(--csp-border);
  }
  .telemetry-diag p { margin: 6px 0; }
  .feed-pill {
    font-size: 13px;
    font-weight: 700;
    padding: 8px 16px;
    border-radius: 999px;
    white-space: nowrap;
    align-self: center;
  }
  .feed-pill.ok { background: var(--green-bg); color: var(--green); border: 1px solid rgba(21, 128, 61, 0.25); }
  .feed-pill.degraded { background: var(--amber-bg); color: var(--amber); border: 1px solid rgba(180, 83, 9, 0.3); }
  .feed-pill.stale { background: var(--red-bg); color: var(--red); border: 1px solid rgba(185, 28, 28, 0.25); }
  .feed-metrics {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px 20px;
    font-size: 12px;
    flex: 1;
    min-width: 0;
  }
  .feed-metrics p { line-height: 1.4; margin: 0; }
  .feed-metrics .fm-k {
    color: var(--csp-text-muted);
    display: block;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
  }
  .feed-metrics .fm-v {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    word-break: break-word;
    color: var(--csp-text);
  }
  .feed-tick-err {
    font-size: 12px;
    color: var(--red);
    font-weight: 500;
    line-height: 1.35;
  }

  .health-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-top: 8px;
  }
  .health-card {
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 12px 14px;
    background: var(--csp-surface-elevated);
  }
  .health-card h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--csp-accent-strong);
    margin-bottom: 6px;
  }
  .health-card p {
    font-size: 13px;
    color: var(--csp-text);
    line-height: 1.45;
  }
  .health-card.telemetry-paths-card {
    grid-column: 1 / -1;
  }
  .telemetry-paths-line code {
    font-size: 12px;
    word-break: break-all;
    color: var(--csp-text);
  }

  .alerts-section .alert-block {
    margin-top: 10px;
    padding: 12px 14px;
    border-radius: var(--csp-radius);
    font-size: 13px;
    line-height: 1.45;
  }
  .alerts-section .alert-block:first-of-type { margin-top: 0; }
  .alert-fault {
    background: var(--red-bg);
    color: var(--red);
    border: 1px solid rgba(185, 28, 28, 0.25);
    font-weight: 600;
  }
  .alert-feed {
    background: var(--red-bg);
    color: var(--red);
    border: 1px solid rgba(185, 28, 28, 0.25);
    font-weight: 600;
  }
  .alert-stale {
    background: var(--amber-bg);
    color: var(--amber);
    border: 1px solid rgba(180, 83, 9, 0.28);
    font-weight: 500;
  }
  .alert-stale code { font-size: 12px; word-break: break-all; }
  .alert-incomplete {
    background: rgba(234, 88, 12, 0.15);
    color: #fdba74;
    border: 1px solid rgba(234, 88, 12, 0.35);
    font-weight: 600;
  }
  .alert-none {
    color: var(--csp-text-muted);
    font-size: 13px;
    padding: 8px 0;
  }

  /* Definition rows: use .dl-k / .dl-v (not dt/dd inside div wrappers — invalid dl
     markup is repaired by browsers and breaks grid/flex in channel cards). */
  .ref-dl, .ch-dl {
    display: grid;
    gap: 0;
    margin: 0;
    min-width: 0;
  }
  /* Two minmax(0,…) columns: "auto" on values let long numbers set min-content width
     and blew grid tracks so channel cards drew on top of each other. */
  .ref-dl .dl-row, .ch-dl .dl-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 8px 10px;
    padding: 6px 0;
    border-bottom: 1px solid var(--csp-border);
    font-size: 13px;
    align-items: start;
    min-width: 0;
  }
  .ref-dl .dl-row:last-child, .ch-dl .dl-row:last-child { border-bottom: none; }
  .ch-dl .dl-k, .ref-dl .dl-k {
    color: var(--csp-text-muted);
    font-weight: 500;
    min-width: 0;
    overflow-wrap: break-word;
  }
  .ch-dl .dl-v, .ref-dl .dl-v {
    margin: 0;
    text-align: right;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    min-width: 0;
    overflow-wrap: anywhere;
    word-break: break-word;
    hyphens: manual;
  }
  .ch-ma-row .dl-k { font-weight: 700; color: var(--csp-text); }
  .ch-ma-row .dl-v .ch-ma { font-size: 1.75rem; }
  .ch-dl .dl-row.dl-row-stack {
    grid-template-columns: minmax(0, 1fr);
  }
  .ch-dl .dl-row.dl-row-stack .dl-v {
    text-align: left;
    font-weight: 500;
    margin-top: 4px;
  }
  .ch-dl .dl-row.dl-row-banner {
    grid-template-columns: minmax(0, 1fr);
  }
  .ch-dl .dl-row.dl-row-banner .dl-v {
    grid-column: 1;
    text-align: left;
  }

  .ch-adv {
    margin-top: 12px;
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 0 10px 8px;
    background: var(--csp-bg);
  }
  .ch-adv summary {
    cursor: pointer;
    font-weight: 600;
    font-size: 12px;
    padding: 10px 4px;
    color: var(--csp-accent-strong);
  }
  .ch-adv summary:focus-visible { outline: 2px solid var(--csp-accent); border-radius: 4px; }
  .ch-adv .ch-dl { padding: 4px 4px 0; }

  .ref-hint {
    margin-top: 12px;
    padding: 10px 12px;
    background: var(--amber-bg);
    border-radius: var(--csp-radius);
    font-size: 12px;
    line-height: 1.45;
    border-left: 3px solid var(--amber);
  }

  .site-footer {
    max-width: 1400px;
    margin: 0 auto;
    padding: 16px 20px 32px;
    font-size: 12px;
    color: var(--csp-text-muted);
    border-top: 1px solid var(--csp-border);
  }
  .site-footer code { font-size: 11px; }

  table.data-table td.num,
  table.data-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tbody.striped tr:nth-child(even) { background: rgba(255, 255, 255, 0.04); }
  .muted { font-size: 11px; font-weight: 400; color: var(--csp-text-muted); }

  .alerts-section .system-alerts {
    margin-top: 0;
    border-radius: var(--csp-radius);
    border-bottom: none;
  }

  /* N columns from --ch-cols (NUM_CHANNELS). MUST use minmax(0,1fr): plain 1fr uses an
     implicit min-content minimum and wide inner grids make tracks overlap visually. */
  .ch-grid {
    display: grid;
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
    grid-template-columns: repeat(var(--ch-cols, 4), minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 24px;
    align-items: start;
  }
  @media (max-width: 1180px) {
    .ch-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @media (max-width: 560px) {
    .ch-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .ch-card {
    background: var(--csp-surface-elevated);
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 14px;
    min-width: 0;
    max-width: 100%;
    width: 100%;
    box-sizing: border-box;
    overflow-x: hidden;
    overflow-y: visible;
    position: relative;
    isolation: isolate;
  }
  .ch-card .ch-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--csp-accent-strong);
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
  .state-OFF { background: var(--gray-bg); color: var(--gray-text); }
  .state-DRY { background: var(--gray-bg); color: var(--gray-text); }
  .state-DORMANT { background: var(--gray-bg); color: var(--gray-text); }
  .state-PROBING { background: var(--amber-bg); color: var(--amber); }
  .state-FAULT { background: var(--red-bg); color: var(--red); }
  .state-UNKNOWN { background: var(--gray-bg); color: var(--gray-text); }
  .ch-ma {
    font-size: clamp(1.1rem, 2.8vw + 0.6rem, 1.65rem);
    font-weight: 700;
    line-height: 1.15;
    margin-bottom: 4px;
  }
  .ch-ma small { font-size: 13px; font-weight: 400; color: var(--csp-text-muted); }
  .ch-ma-idle {
    color: var(--csp-accent);
    letter-spacing: 0.02em;
  }
  .ch-ma-sub { font-size: 12px; font-weight: 500; color: var(--csp-text-muted); }
  .ch-meta { margin-top: 4px; }

  .section {
    background: var(--csp-surface);
    border: 1px solid var(--csp-border);
    border-radius: var(--csp-radius);
    padding: 18px;
    margin-bottom: 24px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    min-width: 0;
  }
  .section-sub {
    font-size: 12px;
    color: var(--csp-text-muted);
    margin: -8px 0 16px;
    max-width: 40rem;
    line-height: 1.5;
  }
  .history-sub {
    font-size: 13px;
    font-weight: 600;
    margin: 20px 0 10px;
    color: var(--csp-text);
    padding-bottom: 6px;
    border-bottom: 1px solid var(--csp-border);
  }
  .history-sub:first-of-type { margin-top: 0; }
  .protection-stack > .section { margin-bottom: 16px; }
  .protection-stack > .section:last-child { margin-bottom: 0; }
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
  .section h2.section-title {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.02em;
    margin: 0;
  }
  .time-btns { display: flex; gap: 6px; flex-wrap: wrap; }
  .time-btns button {
    background: var(--csp-surface-elevated);
    border: 1px solid var(--csp-border);
    color: var(--csp-text);
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
  }
  .time-btns button:hover { border-color: var(--csp-border-strong); }
  .time-btns button:focus-visible {
    outline: 2px solid var(--csp-accent);
    outline-offset: 2px;
  }
  .time-btns button.active {
    background: var(--csp-accent);
    color: #0a0a0a;
    border-color: var(--csp-accent);
  }
  .chart-wrap { position: relative; height: 280px; }
  @media (min-width: 900px) {
    .chart-wrap { height: 360px; }
  }
  .export-links { display: flex; gap: 10px; }
  .export-links a {
    font-size: 12px;
    font-weight: 500;
    color: var(--csp-link);
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
    background: var(--csp-surface-elevated);
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
  .high { color: #fb923c; font-weight: 600; }
  .err { color: var(--red); font-weight: 600; }
  .off { color: var(--gray-text); font-weight: 600; }
  .dry { color: var(--csp-text-muted); font-weight: 500; }

  .sim-badge {
    background: rgba(217, 119, 6, 0.35);
    color: #fdba74;
    border: 1px solid rgba(251, 191, 36, 0.4);
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 999px;
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
  .stale { color: var(--red); font-weight: 700; }
  .ch-extra .sensor-ok { color: var(--green); font-weight: 600; }
  .ch-extra .sensor-bad { color: var(--red); font-weight: 600; }
  .ch-extra .sensor-off { color: var(--gray-text); font-weight: 600; }
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

<a class="skip-link" href="#main">Skip to content</a>

<header>
  <div class="status-dot" id="dot" title="Controller / feed health"></div>
  <h1>CoilShield ICCP</h1>
  <span id="status-pill" class="status-pill">—</span>
  <span id="sim-badge"></span>
  <span id="ts" class="header-ts"></span>
</header>

<nav class="dash-nav" aria-label="Page sections">
  <a href="#overview">Overview</a>
  <a href="#alerts">Alerts</a>
  <a href="#protection">Protection</a>
  <a href="#health">Health</a>
  <a href="#trends">Trends</a>
  <a href="#history">History</a>
</nav>

<main id="main">
  <section id="overview" class="section kpi-section">
    <div class="section-header">
      <h2 class="section-title">Overview</h2>
    </div>
    <div class="kpi-grid">
      <article class="kpi-tile">
        <h3>Total output</h3>
        <p class="kpi-value" id="kpi-total-ma">—</p>
        <p class="kpi-caption" id="kpi-total-cap">Sum of all channel currents</p>
      </article>
      <article class="kpi-tile">
        <h3>Total power</h3>
        <p class="kpi-value" id="kpi-total-pw">—</p>
        <p class="kpi-caption" id="kpi-total-pw-cap">Σ V×I (control proxy)</p>
      </article>
      <article class="kpi-tile">
        <h3>PROTECTING</h3>
        <p class="kpi-value" id="kpi-wet-ch">—</p>
        <p class="kpi-caption">Channels in PROTECTING / __NUM_CH__</p>
      </article>
      <article class="kpi-tile">
        <h3>Supply (avg)</h3>
        <p class="kpi-value" id="kpi-supply">—</p>
        <p class="kpi-caption">Bus V, channels with bus &gt; 0</p>
      </article>
      <article class="kpi-tile">
        <h3>Temperature</h3>
        <p class="kpi-value" id="kpi-temp">—</p>
        <p class="kpi-caption">Controller sensor</p>
      </article>
      <article class="kpi-tile">
        <h3>Ref polarization</h3>
        <p class="kpi-value" id="kpi-ref-short">—</p>
        <p class="kpi-caption">Shift · band</p>
      </article>
    </div>
    <div class="feed-status-wrap" id="feed-block">
      <div class="feed-status-row">
        <div class="feed-pill stale" id="feed-trust-pill" role="status">—</div>
        <p class="feed-line" id="feed-line-summary">—</p>
      </div>
      <details class="feed-diag-details" id="feed-diag-details">
        <summary>Advanced feed diagnostics &amp; paths</summary>
        <div class="feed-diag-body">
          <p class="section-sub" style="margin-top:0">Trusted = full <code>record()</code>, fresh file mtime and JSON <code>ts_unix</code> within
          <code>feed_stale_threshold_s</code> (<code>config.settings</code>).
          <strong>Degraded</strong> = <code>telemetry_incomplete</code> (e.g. tick error).</p>
          <div class="feed-contract-bar" id="feed-contract-bar">
            <div class="feed-metrics">
              <p><span class="fm-k">Threshold (s)</span><span class="fm-v" id="fc-thr">—</span></p>
              <p><span class="fm-k">Mtime age (s)</span><span class="fm-v" id="fc-file">—</span></p>
              <p><span class="fm-k">JSON ts age (s)</span><span class="fm-v" id="fc-json">—</span></p>
              <p><span class="fm-k">telemetry_seq</span><span class="fm-v" id="fc-seq">—</span></p>
              <p><span class="fm-k">writer_pid</span><span class="fm-v" id="fc-pid">—</span></p>
              <p><span class="fm-k">Reasons (if not trusted)</span><span class="fm-v" id="fc-reasons">—</span></p>
              <p><span class="fm-k">Last good channel snapshot</span><span class="fm-v" id="fc-last-good">—</span></p>
              <p style="grid-column:1/-1" id="fc-tick-wrap" hidden><span class="fm-k">tick_writer_error</span><span class="feed-tick-err" id="fc-tick-err"></span></p>
            </div>
          </div>
          <div class="telemetry-diag">
            <p class="telemetry-paths-line">live: <code id="health-latest-path">—</code></p>
            <p class="telemetry-paths-line">database: <code id="health-sqlite-path">—</code></p>
            <p id="health-telemetry-meta">—</p>
          </div>
        </div>
      </details>
    </div>
  </section>

  <section id="alerts" class="section alerts-section" aria-live="polite">
    <div class="section-header">
      <h2 class="section-title">Alerts &amp; notices</h2>
    </div>
    <div id="alert-fault" class="alert-block alert-fault" style="display:none"></div>
    <div id="alert-incomplete" class="alert-block alert-incomplete" style="display:none"></div>
    <div id="alert-feed" class="alert-block alert-feed" style="display:none"></div>
    <div id="alert-stale" class="alert-block alert-stale" style="display:none"></div>
    <div id="alert-system" class="system-alerts" style="display:none"></div>
    <p id="alert-none" class="alert-none">No active alerts.</p>
  </section>

  <section id="protection" class="section">
    <div class="section-header">
      <h2 class="section-title">Protection</h2>
    </div>
    <p class="section-sub">Reference electrode and per-channel output (live <code>latest.json</code>).</p>
    <div class="protection-stack">
      <div>
        <h3 class="history-sub" style="border:none;padding:0;margin:0 0 8px">Reference electrode</h3>
        <div class="ref-dl" role="list">
          <div class="dl-row"><span class="dl-k" title="ref_raw_mv — ADS1115: single-ended vs differential is in ref_ads_sense / ref_hw_message.">Raw reading</span><span class="dl-v" id="ref-raw">—</span></div>
          <div class="dl-row"><span class="dl-k" title="mV vs commissioned baseline; null until baseline exists.">Polarization shift</span><span class="dl-v" id="ref-shift">—</span></div>
          <div class="dl-row"><span class="dl-k" title="Classification band for shift vs expected range.">Shift band</span><span class="dl-v" id="ref-band">—</span></div>
          <div class="dl-row"><span class="dl-k" title="Whether a commissioning baseline has been stored.">Baseline</span><span class="dl-v" id="ref-baseline">—</span></div>
          <div class="dl-row"><span class="dl-k" title="Reference ADC / wiring status from firmware.">Hardware</span><span class="dl-v" id="ref-hwmsg">—</span></div>
          <div class="dl-row"><span class="dl-k" title="0..1 composite vs commissioning baselines (galvanic, Z, depol).">System health</span><span class="dl-v" id="ref-health">—</span></div>
        </div>
        <p id="ref-hint-callout" class="ref-hint" style="display:none"></p>
      </div>
      <div style="margin-top:8px">
        <h3 class="history-sub" style="border:none;padding:0;margin:0 0 8px">Channels</h3>
        <div class="ch-grid" id="ch-grid" style="--ch-cols: __NUM_CH__"></div>
      </div>
    </div>
  </section>

  <section id="health" class="section">
    <div class="section-header">
      <h2 class="section-title">System health</h2>
    </div>
    <p class="section-sub" style="margin-top:-4px">Cross-checks and faults. File paths and feed maths are under <strong>Overview → Advanced feed diagnostics</strong>.</p>
    <div class="health-grid">
      <article class="health-card">
        <h3>Cross-channel balance</h3>
        <p id="health-cross"></p>
      </article>
      <article class="health-card">
        <h3>Any channel PROTECTING</h3>
        <p id="health-anywet"></p>
      </article>
      <article class="health-card health-accuracy-card" style="grid-column:1/-1">
        <details>
          <summary><strong>Feed &amp; accuracy</strong> — how this UI relates to hardware</summary>
          <ul style="margin:8px 0 0 18px;font-size:13px;line-height:1.45;color:var(--csp-text-muted)">
            <li><strong>Same files as the controller:</strong> match <code>COILSHIELD_LOG_DIR</code> / <code>ICCP_LOG_DIR</code> (or <code>iccp dashboard --log-dir</code>) with <code>iccp start</code>. Paths are in Overview → Advanced.</li>
            <li><strong>Live vs stale vs degraded:</strong> <code>/api/live</code> sets <code>feed_trust_channel_metrics</code> from file mtime age, JSON <code>ts_unix</code> age, and <code>telemetry_incomplete</code> — not from mtime alone. Stopped controller → mtime and ts age grow past threshold. A failed <code>record()</code> triggers <code>recovery_touch_latest</code>: fresh mtime and ts but <code>telemetry_incomplete: true</code> and no <code>telemetry_seq</code> (channel mA is placeholders).</li>
            <li><strong>Proxies (not lab potentials):</strong> cell voltage ≈ bus×duty%; impedance ≈ bus/I; power ≈ bus×I — see README and <code>docs/iccp-vs-coilshield.md</code>.</li>
            <li><strong>PROTECTING vs “wet current”:</strong> the overview flag is true when any channel FSM is PROTECTING, not merely shunt current above a wet threshold.</li>
            <li><strong>Targets:</strong> each channel card shows the effective mA setpoint for that tick; KPI “settings default” uses module <code>TARGET_MA</code> (outer loop may move the live setpoint).</li>
          </ul>
        </details>
      </article>
      <article class="health-card">
        <h3>Active faults</h3>
        <p id="health-faults"></p>
      </article>
      <article class="health-card">
        <h3>Reference hardware</h3>
        <p id="health-refhw"></p>
      </article>
    </div>
  </section>

  <div class="section" id="trends">
    <div class="section-header">
      <span class="section-title" id="chart-section-title">Trends — current (mA)</span>
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
    <p class="chart-legend" style="font-size:12px;color:var(--csp-text-muted);margin:0 0 8px"><strong>Legend:</strong> Target = setpoint (mA mode); one trace per anode (Anode 1–__NUM_CH__, firmware idx 0–__IDX_MAX__); Total mA = sum of channels. Click legend labels to show or hide series (at least one line stays on). DB is downsampled; the live tail updates from <code>latest.json</code> between refreshes.</p>
    <p class="chart-note">Downsampled from SQLite for performance.</p>
    <div class="chart-wrap"><canvas id="chart"></canvas></div>
  </div>

  <section id="history" class="section">
    <div class="section-header">
      <h2 class="section-title">History &amp; totals</h2>
    </div>
    <p class="section-sub">Wet sessions, today’s charge while PROTECTING, and per-channel statistics. Same <code>/api/*</code> as before; anchor links: <a href="#sessions">sessions</a> · <a href="#daily-totals">daily</a> · <a href="#stats">stats</a>.</p>

    <h3 class="history-sub" id="sessions">Recent wet sessions</h3>
    <p style="font-size:12px;color:var(--csp-text-muted);margin:-6px 0 10px">Last 24h, newest first</p>
    <table class="data-table">
      <thead>
        <tr>
          <th>Channel</th>
          <th>Started</th>
          <th>Ended</th>
          <th>Duration</th>
          <th class="num">Avg mA</th>
          <th class="num">Peak mA</th>
          <th class="num">Avg Z (Ω)</th>
        </tr>
      </thead>
      <tbody id="sessions-body" class="striped"></tbody>
    </table>

    <h3 class="history-sub" id="daily-totals">Today’s cumulative protection</h3>
    <p style="font-size:12px;color:var(--csp-text-muted);margin:-6px 0 10px">mA·s while PROTECTING; charge (C) = mA·s ÷ 1000</p>
    <table class="data-table">
      <thead>
        <tr>
          <th>Channel</th>
          <th>Wet time today</th>
          <th class="num">mA·s (protecting)</th>
          <th class="num">Charge (C)</th>
        </tr>
      </thead>
      <tbody id="daily-body" class="striped"></tbody>
    </table>

    <h3 class="history-sub" id="stats">Statistics — today</h3>
    <p style="font-size:12px;color:var(--csp-text-muted);margin:-6px 0 10px"><span id="stats-since"></span> <span class="stats-footnote" style="font-size:11px">† Ref Δ / temp: today’s average across all ticks (same value per row).</span></p>
    <table class="data-table">
      <thead>
        <tr>
          <th>Channel</th>
          <th>State</th>
          <th>Protecting today</th>
          <th class="num">Avg mA</th>
          <th class="num">Coverage</th>
          <th class="num">Wet cycles</th>
          <th class="num">Bus V avg</th>
          <th class="num">Avg Z (Ω)</th>
          <th class="num">Ref shift (mV)†</th>
          <th class="num">Temp (°F)†</th>
        </tr>
      </thead>
      <tbody id="stats-body" class="striped"></tbody>
    </table>
  </section>
</main>

<footer class="site-footer">
  Live feed: <code>latest.json</code> under <code>LOG_DIR</code>; history in SQLite / CSV. Exports: Trends section. Docs: <code>README.md</code>.
</footer>

<script>
const CH_COLORS = ['var(--ch0)','var(--ch1)','var(--ch2)','var(--ch3)','var(--ch4)'];
/* Hex for Chart.js line borders — canvas stroke does not resolve CSS var(). */
const CHART_CH_HEX = ['#38bdf8', '#34d399', '#fbbf24', '#f87171', '#a78bfa'];
const CHART_TARGET_HEX = '#c084fc';
const CHART_TOTAL_HEX = '#22d3ee';
const CHART_MUTED = '#a1a1aa';
const CHART_GRID = 'rgba(255, 255, 255, 0.07)';
const NUM_CH = __NUM_CH__;
const MA_NOISE_FLOOR = __MA_NOISE_FLOOR__;
const STATUS_HINT = {
  OK: 'On target: current vs effective setpoint looks healthy for this tick.',
  LOW: 'Marginal: current is low vs this channel target (or non-conducting path).',
  HIGH: 'Elevated: current is high vs this channel target.',
  ERR: 'Sensor or read failed for this channel; values may not reflect the anode.',
  OFF: 'Outputs idle: transient I2C (e.g. errno 5) while PWM at 0% — not treated as a live fault.',
  DRY: 'Treated as dry / non-conductive path by the state machine.',
  OPEN: 'Output path open or not in closed regulation.',
  OPEN_CIRCUIT: 'Very low current; treated as open path.',
};
let chart = null;
let activeMinutes = 60;
let chartMetric = 'ma';

const grid = document.getElementById('ch-grid');
for (let i = 0; i < NUM_CH; i++) {
  grid.innerHTML += `
    <div class="ch-card" id="card-${i}">
      <div class="ch-label">
        <span class="ch-dot" style="background:${CH_COLORS[i]}"></span>Anode ${i+1} <span class="muted">(idx ${i})</span>
      </div>
      <div class="ch-state state-OPEN" id="state-${i}">OPEN</div>
      <div class="ch-dl ch-meta" role="list">
        <div class="dl-row ch-ma-row">
          <span class="dl-k" title="Servo-controlled cathodic current for this anode path; compare to per-channel target in the controller.">Output current</span>
          <span class="dl-v"><span class="ch-ma" id="ma-${i}">— <small>mA</small></span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Effective mA setpoint this tick (CHANNEL_TARGET_MA override or runtime TARGET_MA after commissioning / outer-loop nudges).">Target (setpoint)</span>
          <span class="dl-v"><span id="tgt-${i}">—</span><span class="muted"> mA</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Fraction of time the output is on; the controller uses duty to regulate current.">PWM duty</span>
          <span class="dl-v"><span id="duty-${i}">—</span><span class="muted"> %</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Voltage at the channel bus sense (INA219); reflects supply and electrical path health.">Bus voltage</span>
          <span class="dl-v"><span id="busv-${i}">—</span><span class="muted"> V</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Approximate effective V/I in ohms for this tick; very high Z often means dry or weak conduction path.">Effective impedance</span>
          <span class="dl-v"><span id="z-${i}">—</span><span class="muted"> Ω</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Estimated average cell terminal voltage during PWM (bus × duty%).">Cell voltage (est.)</span>
          <span class="dl-v"><span id="vcell-${i}">—</span><span class="muted"> V</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Electrical power proxy: bus voltage × output current.">DC power</span>
          <span class="dl-v"><span id="pow-${i}">—</span><span class="muted"> W</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Integrated electrical energy in joules for this channel today (valid sensor reads only).">Energy today</span>
          <span class="dl-v"><span id="enj-${i}">—</span><span class="muted"> J</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="How much current changes per percentage point of duty change when computable; em dash when not available this tick.">mA per % duty (η)</span>
          <span class="dl-v"><span id="eff-${i}">—</span><span class="muted"> mA/%</span></span>
        </div>
        <div class="dl-row">
          <span class="dl-k" title="Controller health flag: OK on target; LOW marginal; ERR bad read; OFF idle bus glitch; DRY/OPEN not in closed conducting path.">Channel health</span>
          <span class="dl-v"><span id="status-${i}">—</span></span>
        </div>
      </div>
      <details class="ch-adv">
        <summary>Advanced telemetry</summary>
        <div class="ch-dl ch-extra" role="list">
          <div class="dl-row">
            <span class="dl-k" title="Whether the INA219 sample for this channel succeeded on this tick.">Sensor sample</span>
            <span class="dl-v"><span id="sens-${i}">—</span></span>
          </div>
          <div class="ch-sensor-err" id="ch-err-${i}"></div>
          <div class="dl-row">
            <span class="dl-k" title="Coulombs delivered today while PROTECTING (∫I·dt / 1000).">Charge today (Q)</span>
            <span class="dl-v"><span id="coul-${i}">—</span><span class="muted"> C</span></span>
          </div>
          <div class="dl-row">
            <span class="dl-k" title="Change in effective impedance vs the previous control tick.">Impedance change (ΔZ)</span>
            <span class="dl-v"><span id="dz-${i}">—</span><span class="muted"> Ω</span></span>
          </div>
          <div class="dl-row">
            <span class="dl-k" title="Rolling spread of recent impedance (variability / noise).">Impedance spread (Zσ)</span>
            <span class="dl-v"><span id="zstd-${i}">—</span><span class="muted"> Ω</span></span>
          </div>
          <div class="dl-row">
            <span class="dl-k" title="Conductance proxy (order of 1/Z); higher values suggest an easier current path.">σ proxy</span>
            <span class="dl-v"><span id="sigma-${i}">—</span><span class="muted"> s</span></span>
          </div>
          <div class="dl-row">
            <span class="dl-k" title="Current-to-voltage ratio I/V; raw tick vs exponentially smoothed value (process proxy).">FQI (smooth / raw)</span>
            <span class="dl-v"><span id="fqi-${i}">—</span> <span class="muted">(</span><span id="fqir-${i}">—</span><span class="muted"> raw)</span></span>
          </div>
          <div class="dl-row">
            <span class="dl-k" title="Rate of change of impedance over this control interval.">dZ/dt</span>
            <span class="dl-v"><span id="zrate-${i}">—</span><span class="muted"> Ω/s</span></span>
          </div>
          <div class="dl-row">
            <span class="dl-k" title="Small-signal resistance estimate from bus voltage and current deltas vs the previous tick.">dV/dI</span>
            <span class="dl-v"><span id="dvd-${i}">—</span><span class="muted"> Ω</span></span>
          </div>
          <div class="dl-row dl-row-stack">
            <span class="dl-k" title="Mapped film/wet hint from the controller (DRY, STABLE_WET, etc.).">Surface hint</span>
            <span class="dl-v"><span id="surf-${i}">—</span></span>
          </div>
          <div class="dl-row dl-row-banner">
            <span class="dl-v"><span id="zero-flag-${i}" style="display:none" class="elec-zero"></span></span>
          </div>
        </div>
      </details>
    </div>`;
}

const ctx = document.getElementById('chart').getContext('2d');
const TOTAL_DS = NUM_CH + 1;
const MAX_CHART_POINTS = 2400;

function dashLegendClickHandler(evt, legendItem, legend) {
  const ch = legend.chart;
  const idx = legendItem.datasetIndex;
  if (idx == null || idx < 0) return;
  const visible = ch.isDatasetVisible(idx);
  if (visible) {
    let n = 0;
    for (let i = 0; i < ch.data.datasets.length; i++) {
      if (ch.isDatasetVisible(i)) n++;
    }
    if (n <= 1) return;
  }
  ch.setDatasetVisibility(idx, !visible);
  ch.update('none');
}

chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: 'Avg target mA', data: [], borderColor: CHART_TARGET_HEX,
        borderDash: [6, 4], borderWidth: 2, pointRadius: 0,
        fill: false, tension: 0, hidden: false,
      },
      ...Array.from({length: NUM_CH}, (_, i) => ({
        label: `Anode ${i+1} (idx ${i})`,
        data: [], borderColor: CHART_CH_HEX[i % CHART_CH_HEX.length],
        backgroundColor: 'transparent',
        borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false, hidden: false,
      })),
      {
        label: 'Total mA',
        data: [],
        borderColor: CHART_TOTAL_HEX,
        borderWidth: 2.2,
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
        labels: {
          boxWidth: 14, font: { size: 12 }, usePointStyle: false,
          color: CHART_MUTED,
        },
        onClick: dashLegendClickHandler,
      },
      tooltip: { callbacks: { label: ctx => {
        const u = chartMetric === 'impedance' ? 'Ω' : 'mA';
        const y = ctx.parsed.y;
        return ` ${ctx.dataset.label}: ${y != null ? y.toFixed(2) : '—'} ${u}`;
      }}}
    },
    scales: {
      x: { ticks: { maxTicksLimit: 12, font: { size: 11 }, color: CHART_MUTED }, grid: { color: CHART_GRID } },
      y: {
        title: { display: true, text: 'mA', font: { size: 11 }, color: CHART_MUTED },
        min: 0,
        grace: '8%',
        ticks: { font: { size: 11 }, color: CHART_MUTED },
        grid: { color: CHART_GRID }
      }
    }
  }
});

function syncChartLiveTail(d) {
  if (!chart || !d.channels || d.error) return;
  if (d.feed_trust_channel_metrics === false || d.telemetry_incomplete) return;
  const lab = (d.ts && d.ts.length >= 19) ? d.ts.slice(11, 19) : '';
  if (!lab) return;

  let tgtMa = (d.target_ma != null && d.target_ma !== '')
    ? Number(d.target_ma) : null;
  if (d.target_ma_avg_live != null && d.target_ma_avg_live !== '' && Number.isFinite(Number(d.target_ma_avg_live))) {
    tgtMa = Number(d.target_ma_avg_live);
  }

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

/** Watts from latest.json; extra decimals when |P| < 0.01 W so small totals are visible. */
function fmtPowerW(w) {
  if (w == null || w === '') return '—';
  const n = Number(w);
  if (!Number.isFinite(n)) return '—';
  const a = Math.abs(n);
  if (a === 0) return '0.000 W';
  if (a < 0.01) return n.toFixed(6) + ' W';
  return n.toFixed(3) + ' W';
}

function paintChannelUnknown(i) {
  const stateEl = document.getElementById(`state-${i}`);
  stateEl.textContent = 'UNKNOWN';
  stateEl.className = 'ch-state state-UNKNOWN';
  document.getElementById(`ma-${i}`).innerHTML = '— <small>mA</small>';
  const tgtE = document.getElementById(`tgt-${i}`);
  if (tgtE) tgtE.textContent = '—';
  document.getElementById(`duty-${i}`).textContent = '—';
  document.getElementById(`busv-${i}`).textContent = '—';
  document.getElementById(`z-${i}`).textContent = '—';
  document.getElementById(`vcell-${i}`).textContent = '—';
  const st = document.getElementById(`status-${i}`);
  st.textContent = '—';
  st.className = '';
  st.title = '';
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

function setAlertNoneVisible(show) {
  const el = document.getElementById('alert-none');
  if (el) el.style.display = show ? '' : 'none';
}

function _dashChReadingOk(ch) {
  let readingOk = ch.reading_ok;
  if (readingOk === undefined) readingOk = ch.status !== 'ERR';
  return readingOk;
}

/** True when every channel reads OK and |mA| is below the INA noise floor (outputs at rest, not “broken”). */
function _dashAllChannelsIdleNoise(d) {
  if (!d.channels) return false;
  for (let i = 0; i < NUM_CH; i++) {
    const ch = d.channels[String(i)] || {};
    if (!_dashChReadingOk(ch)) return false;
    const maNum = Number(ch.ma);
    if (!Number.isFinite(maNum) || Math.abs(maNum) >= MA_NOISE_FLOOR) return false;
  }
  return true;
}

function _dashChannelMaHtml(stale, ch, maDisp) {
  if (stale) return '— <small>mA</small>';
  const readingOk = _dashChReadingOk(ch);
  const stt = ch.status || '';
  if (stt === 'ERR' || !readingOk) {
    const n = Number(ch.ma);
    return Number.isFinite(n)
      ? `${n.toFixed(3)} <small>mA</small>`
      : '— <small>mA</small>';
  }
  if (maDisp == null || !Number.isFinite(maDisp)) {
    return '— <small>mA</small>';
  }
  if (Math.abs(maDisp) < MA_NOISE_FLOOR) {
    const t = maDisp.toFixed(4);
    return `<span class="ch-ma-idle" title="Measured ${t} mA (below display floor)">Idle</span> <small class="ch-ma-sub">(sensor OK)</small>`;
  }
  return `${maDisp.toFixed(3)} <small>mA</small>`;
}

function _dashFeedTrustStale(d) {
  const age = d.feed_age_s;
  const thr = d.feed_stale_threshold_s ?? 3;
  if (d.feed_trust_channel_metrics === true) return false;
  if (d.feed_trust_channel_metrics === false) return true;
  const jAge = d.json_payload_age_s;
  return (typeof age === 'number' && age > thr)
    || (typeof jAge === 'number' && jAge > thr * 1.01)
    || !!d.telemetry_incomplete;
}

async function fetchLive() {
  try {
    const d = await fetch('/api/live?_t=' + Date.now(), { cache: 'no-store' }).then(r => r.json());
    const alertFeed = document.getElementById('alert-feed');
    const alertFault = document.getElementById('alert-fault');
    const alertSys = document.getElementById('alert-system');
    const alertInc = document.getElementById('alert-incomplete');
    if (d.error) {
      alertFeed.style.display = '';
      alertFeed.textContent = d.error;
      const ast = document.getElementById('alert-stale');
      if (ast) { ast.style.display = 'none'; ast.innerHTML = ''; }
      if (alertInc) { alertInc.style.display = 'none'; alertInc.textContent = ''; }
      if (alertFault) { alertFault.style.display = 'none'; alertFault.textContent = ''; }
      if (alertSys) { alertSys.style.display = 'none'; alertSys.innerHTML = ''; }
      setAlertNoneVisible(false);
      window._dashLastLive = null;
      document.getElementById('status-pill').textContent = 'No feed';
      const flsE = document.getElementById('feed-line-summary');
      if (flsE) { flsE.textContent = '—'; flsE.style.color = ''; }
      const fddE = document.getElementById('feed-diag-details');
      if (fddE) fddE.setAttribute('open', '');
      document.getElementById('health-cross').textContent = '—';
      document.getElementById('health-anywet').textContent = '—';
      document.getElementById('health-faults').textContent = '—';
      document.getElementById('health-refhw').textContent = '—';
      const fcp = document.getElementById('feed-trust-pill');
      if (fcp) { fcp.className = 'feed-pill stale'; fcp.textContent = 'No data'; }
      const ids = ['fc-thr','fc-file','fc-json','fc-seq','fc-pid','fc-reasons','fc-last-good'];
      ids.forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '—'; });
      const fwrap = document.getElementById('fc-tick-wrap');
      if (fwrap) fwrap.hidden = true;
      const fte = document.getElementById('fc-tick-err');
      if (fte) fte.textContent = '';
      const hlp = document.getElementById('health-latest-path');
      const hsp = document.getElementById('health-sqlite-path');
      const hm = document.getElementById('health-telemetry-meta');
      if (hlp) hlp.textContent = '—';
      if (hsp) hsp.textContent = '—';
      if (hm) hm.textContent = '—';
      document.getElementById('dot').style.background = '#94a3b8';
      for (let i = 0; i < NUM_CH; i++) paintChannelUnknown(i);
      return;
    }
    alertFeed.style.display = 'none';
    alertFeed.textContent = '';
    const alertStale = document.getElementById('alert-stale');
    window._dashLastLive = d;

    const age = d.feed_age_s;
    const thr = d.feed_stale_threshold_s ?? 3;
    const jPayloadAge = d.json_payload_age_s;
    const trust = d.feed_trust_channel_metrics;
    const stale = _dashFeedTrustStale(d);
    const inc = !!d.telemetry_incomplete;
    const reasons = Array.isArray(d.feed_stale_reasons) ? d.feed_stale_reasons : [];

    const fcp = document.getElementById('feed-trust-pill');
    if (fcp) {
      if (trust) {
        fcp.className = 'feed-pill ok';
        fcp.textContent = 'Live data';
      } else if (inc) {
        fcp.className = 'feed-pill degraded';
        fcp.textContent = 'Partial snapshot';
      } else {
        fcp.className = 'feed-pill stale';
        fcp.textContent = 'Not updating';
      }
    }
    const ft = document.getElementById('fc-thr');
    if (ft) ft.textContent = (typeof thr === 'number' && Number.isFinite(thr)) ? thr.toFixed(2) : '—';
    const ff = document.getElementById('fc-file');
    if (ff) ff.textContent = (typeof age === 'number' && Number.isFinite(age)) ? age.toFixed(3) : '—';
    const fj = document.getElementById('fc-json');
    if (fj) fj.textContent = (jPayloadAge != null && jPayloadAge !== '' && Number.isFinite(Number(jPayloadAge))) ? Number(jPayloadAge).toFixed(3) : '—';
    const fsq = document.getElementById('fc-seq');
    if (fsq) fsq.textContent = (d.telemetry_seq != null && d.telemetry_seq !== '') ? String(d.telemetry_seq) : '— (recovery or legacy)';
    const fpi = document.getElementById('fc-pid');
    if (fpi) fpi.textContent = (d.writer_pid != null && d.writer_pid !== '') ? String(d.writer_pid) : '—';
    const fr = document.getElementById('fc-reasons');
    if (fr) fr.textContent = reasons.length ? reasons.join(' · ') : '—';
    const flg = document.getElementById('fc-last-good');
    if (flg) {
      const lu = d.last_valid_channel_snapshot_ts_unix;
      flg.textContent = (lu != null && lu !== '' && Number.isFinite(Number(lu)))
        ? (fmtUnixTs(lu) + ' · ' + Number(lu).toFixed(3) + ' unix')
        : '—';
    }
    const tw = document.getElementById('fc-tick-wrap');
    const tec = (d.tick_writer_error || '').trim();
    if (tw && tec) {
      tw.hidden = false;
      const tline = document.getElementById('fc-tick-err');
      if (tline) tline.textContent = tec;
    } else if (tw) {
      tw.hidden = true;
    }

    const fls = document.getElementById('feed-line-summary');
    if (fls) {
      if (typeof age === 'number' && Number.isFinite(age)) {
        const jaStr = (typeof jPayloadAge === 'number' && Number.isFinite(jPayloadAge))
          ? (Number(jPayloadAge).toFixed(1) + 's') : '—';
        const tag = trust ? 'OK' : (inc ? 'Partial' : 'Stale');
        fls.textContent = tag + ' · file ' + age.toFixed(1) + 's · sample age ' + jaStr
          + (typeof thr === 'number' ? ' (thr ≤ ' + thr.toFixed(1) + 's)' : '');
        fls.style.color = trust ? 'var(--green)' : (inc ? 'var(--amber)' : 'var(--red)');
      } else {
        fls.textContent = '—';
        fls.style.color = '';
      }
    }
    const fdd = document.getElementById('feed-diag-details');
    if (fdd) {
      if (trust) fdd.removeAttribute('open');
      else fdd.setAttribute('open', '');
    }

    if (alertInc) {
      if (inc) {
        alertInc.style.display = '';
        alertInc.textContent = 'Telemetry incomplete (recovery path): channel mA and related fields are not a full control-loop sample. See tick_writer_error and system alerts. Last good snapshot time is in Overview → Advanced feed diagnostics.';
      } else {
        alertInc.style.display = 'none';
        alertInc.textContent = '';
      }
    }

    const pill = document.getElementById('status-pill');
    if (d.fault_latched) pill.textContent = 'Fault latched';
    else if (trust) pill.textContent = 'Live';
    else if (inc) pill.textContent = 'Degraded';
    else pill.textContent = 'Stale / untrusted';

    const dot = document.getElementById('dot');
    if (trust) dot.style.background = d.fault_latched ? '#f87171' : '#4ade80';
    else if (inc) dot.style.background = '#fb923c';
    else dot.style.background = '#fbbf24';

    const cr = d.cross || {};
    const icv = cr.i_cv, zcv = cr.z_cv;
    document.getElementById('health-cross').textContent =
      (icv != null && icv !== '') || (zcv != null && zcv !== '')
        ? `I_CV ${fmtOpt(icv, 4)} · Z_CV ${fmtOpt(zcv, 4)} (spread across channels)`
        : '—';
    document.getElementById('health-anywet').textContent =
      d.wet ? 'Yes — at least one channel is in PROTECTING (fine servo state)' : 'No';
    const fl = Array.isArray(d.faults) ? d.faults : [];
    document.getElementById('health-faults').textContent =
      fl.length ? fl.join(' · ') : 'None';
    document.getElementById('health-refhw').textContent =
      d.ref_hw_ok === true ? 'OK' : d.ref_hw_ok === false ? 'Problem — see Reference' : '—';

    const tp = d.telemetry_paths;
    const hLatest = document.getElementById('health-latest-path');
    const hSql = document.getElementById('health-sqlite-path');
    const hMeta = document.getElementById('health-telemetry-meta');
    if (tp && hLatest && hSql && hMeta) {
      hLatest.textContent = tp.latest_json || '—';
      hSql.textContent = tp.sqlite_db || '—';
      hMeta.textContent =
        'LOG_DIR from ' + (tp.log_dir_source || '—') +
        '. Start `iccp start` with the same COILSHIELD_LOG_DIR/ICCP_LOG_DIR and the same Python package checkout so paths match.';
    }

    const tsExtra = (d.ts_unix != null && d.ts_unix !== '')
      ? ' · unix ' + d.ts_unix : '';
    document.getElementById('ts').textContent = (d.ts || '—') + tsExtra;

    const tpw = d.total_power_w;
    const lastMaStr = (d.total_ma != null && d.total_ma !== '') ? fmtOpt(d.total_ma, 4) : null;
    const lastPwNum = (tpw != null && tpw !== '') ? Number(tpw) : null;
    const pwCapEl = document.getElementById('kpi-total-pw-cap');
    const tgtLive = (d.target_ma_avg_live != null && d.target_ma_avg_live !== '')
      ? fmtOpt(d.target_ma_avg_live, 3) : null;
    const tgtSet = fmtOpt(d.target_ma, 3);
    const tgtSuffix = tgtLive != null
      ? ` · settings default ${tgtSet} mA · snapshot avg setpoint ${tgtLive} mA`
      : ` · settings default ${tgtSet} mA`;
    if (stale) {
      document.getElementById('kpi-total-ma').textContent = '—';
      document.getElementById('kpi-total-cap').textContent = lastMaStr != null
        ? `Not live · last file had ΣI = ${lastMaStr} mA${tgtSuffix}`
        : `Not live${tgtSuffix}`;
      document.getElementById('kpi-total-pw').textContent = '—';
      if (pwCapEl) {
        pwCapEl.textContent = (lastPwNum != null && Number.isFinite(lastPwNum))
          ? `Not live · last file had ΣV×I = ${fmtPowerW(lastPwNum)}`
          : 'Not live · Σ V×I (control proxy)';
      }
    } else {
      const idleAll = _dashAllChannelsIdleNoise(d);
      if (idleAll) {
        document.getElementById('kpi-total-ma').textContent = 'Idle';
        document.getElementById('kpi-total-cap').textContent =
          `Outputs at rest (|I| < ${MA_NOISE_FLOOR.toFixed(3)} mA per channel) · sensors OK${tgtSuffix}`;
        const pwTiny = lastPwNum != null && Number.isFinite(lastPwNum) && Math.abs(lastPwNum) < 5e-5;
        const pwShow = pwTiny
          ? '~0 W'
          : ((lastPwNum != null && Number.isFinite(lastPwNum)) ? fmtPowerW(lastPwNum) : '—');
        document.getElementById('kpi-total-pw').textContent = pwShow;
        if (pwCapEl) {
          pwCapEl.textContent = pwTiny
            ? 'At rest · Σ V×I (negligible while idle)'
            : 'Live · Σ V×I (control proxy)';
        }
      } else {
        document.getElementById('kpi-total-ma').textContent =
          lastMaStr != null ? `${lastMaStr} mA` : '—';
        document.getElementById('kpi-total-cap').textContent =
          `Live · sum of channel mA${tgtSuffix}`;
        document.getElementById('kpi-total-pw').textContent =
          (lastPwNum != null && Number.isFinite(lastPwNum)) ? fmtPowerW(lastPwNum) : '—';
        if (pwCapEl) pwCapEl.textContent = 'Live · Σ V×I (control proxy)';
      }
    }
    document.getElementById('kpi-wet-ch').textContent =
      `${d.wet_channels != null ? d.wet_channels : '—'} / ${NUM_CH}`;
    const sup = d.supply_v_avg;
    document.getElementById('kpi-supply').textContent =
      (sup != null && sup !== '') ? `${fmtOpt(sup, 3)} V` : '—';
    document.getElementById('kpi-temp').textContent =
      d.temp_f != null && d.temp_f !== '' ? `${d.temp_f} °F` : '—';
    const sense = (d.ref_ads_sense != null && d.ref_ads_sense !== '')
      ? String(d.ref_ads_sense).trim() : '';
    const rawNum = (d.ref_raw_mv != null && d.ref_raw_mv !== '')
      ? `${Number(d.ref_raw_mv).toFixed(1)} mV` : '—';
    const raw = (rawNum !== '—' && sense) ? `${rawNum} (${sense})` : rawNum;
    const sh = (d.ref_shift_mv != null && d.ref_shift_mv !== '')
      ? `${Number(d.ref_shift_mv).toFixed(1)} mV` : '—';
    const bd = d.ref_status || '—';
    document.getElementById('kpi-ref-short').textContent = `${sh} · ${bd}`;
    document.getElementById('ref-raw').textContent = raw;
    document.getElementById('ref-shift').textContent = sh;
    document.getElementById('ref-band').textContent = bd;
    document.getElementById('ref-baseline').textContent =
      d.ref_baseline_set ? 'Yes' : 'No';
    const hw = (d.ref_hw_message || '').trim();
    document.getElementById('ref-hwmsg').textContent = hw || '—';
    const hlt = d.health_alert ? ' ⚠' : '';
    const hs =
      d.system_health != null && d.system_health !== ''
        ? `${Number(d.system_health).toFixed(2)}${hlt}`
        : '—';
    const rh = document.getElementById('ref-health');
    if (rh) rh.textContent = hs;

    const hintEl = document.getElementById('ref-hint-callout');
    const rh = (d.ref_hint || '').trim();
    if (rh) {
      hintEl.style.display = '';
      hintEl.textContent = rh;
    } else {
      hintEl.style.display = 'none';
      hintEl.textContent = '';
    }

    let anyAlert = false;
    if (alertFault) {
      if (d.fault_latched) {
        alertFault.style.display = '';
        alertFault.textContent = 'Fault latch is active — clear faults per operator procedure before continuing.';
        anyAlert = true;
      } else {
        alertFault.style.display = 'none';
        alertFault.textContent = '';
      }
    }

    if (alertStale) {
      const mtimeOrJson = reasons.indexOf('file_mtime') >= 0 || reasons.indexOf('json_ts') >= 0;
      if (!trust && mtimeOrJson && typeof age === 'number') {
        const p = (d.telemetry_paths && d.telemetry_paths.latest_json)
          ? d.telemetry_paths.latest_json : '';
        const ja = (typeof jPayloadAge === 'number' && Number.isFinite(jPayloadAge))
          ? ' · JSON <code>ts_unix</code> is <strong>' + jPayloadAge.toFixed(1) + 's</strong> old'
          : '';
        alertStale.style.display = '';
        alertStale.innerHTML =
          '<strong>File mtime or JSON sample time is too old</strong> — mtime ' +
          '<strong>' + age.toFixed(1) + 's</strong> ago' + ja +
          ' (threshold <strong>' + thr.toFixed(2) + 's</strong>). ' +
          'The UI does not show channel mA as “live” until mtime and <code>ts_unix</code> are both fresh. ' +
          (p ? 'This dashboard reads: <code>' + p + '</code>. ' : '') +
          'Start <code>iccp start</code> and match <code>COILSHIELD_LOG_DIR</code> / <code>iccp dashboard --log-dir</code>. ' +
          '<br><span style="font-size:12px;opacity:.95">Tip: <code>export COILSHIELD_LOG_DIR=/abs/path</code> for both processes.</span>';
        anyAlert = true;
      } else {
        alertStale.style.display = 'none';
        alertStale.innerHTML = '';
      }
    }
    if (alertInc && inc) anyAlert = true;

    const sal = alertSys;
    sal.innerHTML = '';
    const alerts = Array.isArray(d.system_alerts) ? d.system_alerts.filter(Boolean) : [];
    if (alerts.length) {
      sal.style.display = '';
      anyAlert = true;
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
    setAlertNoneVisible(!anyAlert);

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
      const maDisp = Number.isFinite(maNum) ? maNum : null;
      document.getElementById(`ma-${i}`).innerHTML = _dashChannelMaHtml(stale, ch, maDisp);
      const tgtEl = document.getElementById(`tgt-${i}`);
      if (tgtEl) {
        tgtEl.textContent = (ch.target_ma != null && ch.target_ma !== '')
          ? fmtOpt(ch.target_ma, 3) : '—';
      }
      document.getElementById(`duty-${i}`).textContent = fmtOpt(ch.duty, 2);
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
      st.title = STATUS_HINT[stt] || 'Channel status from the controller.';
      st.className = stt === 'OK' ? 'ok'
        : stt === 'ERR' ? 'err'
        : stt === 'OFF' ? 'off'
        : stt === 'HIGH' ? 'high'
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
      if (stt === 'OFF') {
        sens.textContent = 'OFF';
        sens.className = 'sensor-off';
      } else {
        sens.textContent = readingOk ? 'OK' : 'NO READ';
        sens.className = readingOk ? 'sensor-ok' : 'sensor-bad';
      }
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
      if (!stale && readingOk && maDisp != null && Math.abs(maDisp) < MA_NOISE_FLOOR && busOk && Math.abs(busV) >= 0.05) {
        zf.style.display = 'inline-block';
        zf.textContent = 'Bus voltage present with near-zero current — if unexpected, check wet path / bath.';
      } else {
        zf.style.display = 'none';
        zf.textContent = '';
      }
    }
    syncChartLiveTail(d);
  } catch (e) {
    const af = document.getElementById('alert-feed');
    const ast = document.getElementById('alert-stale');
    const ainc = document.getElementById('alert-incomplete');
    if (ast) { ast.style.display = 'none'; ast.innerHTML = ''; }
    if (ainc) { ainc.style.display = 'none'; ainc.textContent = ''; }
    if (af) {
      af.style.display = '';
      af.textContent = 'Network error loading /api/live';
    }
    setAlertNoneVisible(false);
    window._dashLastLive = null;
    document.getElementById('status-pill').textContent = 'No feed';
    document.getElementById('dot').style.background = '#94a3b8';
    const fcpn = document.getElementById('feed-trust-pill');
    if (fcpn) { fcpn.className = 'feed-pill stale'; fcpn.textContent = '—'; }
    const salE = document.getElementById('alert-system');
    if (salE) { salE.style.display = 'none'; salE.innerHTML = ''; }
  }
}

function setMetric(m) {
  chartMetric = m;
  document.getElementById('btn-metric-ma').className = m === 'ma' ? 'active' : '';
  document.getElementById('btn-metric-z').className = m === 'impedance' ? 'active' : '';
  document.getElementById('chart-section-title').textContent =
    m === 'impedance' ? 'Trends — impedance (Ω)' : 'Trends — current (mA)';
  chart.options.scales.y.title.text = m === 'impedance' ? 'Ω' : 'mA';
  chart.options.scales.y.title.color = CHART_MUTED;
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
    const tgtArr = (chartMetric === 'ma' && Array.isArray(d.avg_target_ma) && d.avg_target_ma.length === d.labels.length)
      ? d.avg_target_ma.map((x) => (x != null && x !== '' && Number.isFinite(Number(x)) ? Number(x) : null))
      : null;
    const tgt = d.target;
    chart.data.datasets[0].label = 'Avg target mA';
    chart.data.datasets[0].hidden = chartMetric === 'ma' ? false : true;
    chart.data.datasets[0].data = (tgtArr && chartMetric === 'ma')
      ? tgtArr
      : d.labels.map(() => (chartMetric === 'ma' ? tgt : null));
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

function fmtUnixTs(u) {
  if (u == null || u === '' || !Number.isFinite(Number(u))) return '—';
  try {
    const d = new Date(Number(u) * 1000);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' });
  } catch (e) { return '—'; }
}

async function fetchSessions() {
  try {
    const d = await fetch('/api/sessions?hours=24&limit=20', { cache: 'no-store' }).then(r => r.json());
    if (d.error) return;
    const tb = document.getElementById('sessions-body');
    if (!tb) return;
    const rows = d.sessions || [];
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="7">No wet sessions in the last 24h.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map((s) => {
      const chIdx = (s.channel != null && s.channel !== '') ? Number(s.channel) : NaN;
      const chNum = Number.isFinite(chIdx) ? chIdx + 1 : NaN;
      const dotColor = (Number.isFinite(chNum) && chNum >= 1 && chNum <= NUM_CH)
        ? CH_COLORS[chNum - 1] : '#64748b';
      const chLabel = Number.isFinite(chNum)
        ? `Anode ${chNum} (idx ${chIdx})` : '—';
      const dur = (s.duration_s != null && Number.isFinite(Number(s.duration_s)))
        ? fmtSecs(Number(s.duration_s)) : '—';
      const avg = (s.avg_ma != null) ? Number(s.avg_ma).toFixed(3) : '—';
      const peak = (s.peak_ma != null) ? Number(s.peak_ma).toFixed(3) : '—';
      const z = (s.avg_impedance_ohm != null) ? Number(s.avg_impedance_ohm).toFixed(0) : '—';
      return `<tr>
        <td><span class="ch-dot" style="background:${dotColor}"></span>${chLabel}</td>
        <td>${fmtUnixTs(s.started_at)}</td>
        <td>${fmtUnixTs(s.ended_at)}</td>
        <td>${dur}</td>
        <td class="num">${avg}</td>
        <td class="num">${peak}</td>
        <td class="num">${z}</td>
      </tr>`;
    }).join('');
  } catch (e) {}
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
        <td><span class="ch-dot" style="background:${CH_COLORS[s.ch-1]}"></span>Anode ${s.ch} <span class="muted">(idx ${s.ch - 1})</span></td>
        <td id="stat-state-${s.ch}">—</td>
        <td>${fmtSecs(s.protecting_s)}</td>
        <td class="num">${s.avg_ma.toFixed(3)} <span class="muted">mA</span></td>
        <td class="num">${s.protecting_pct}<span class="muted">%</span></td>
        <td class="num">${s.wet_cycles}</td>
        <td class="num">${s.avg_bus_v.toFixed(2)} <span class="muted">V</span></td>
        <td class="num">${Number(s.avg_impedance_ohm ?? 0).toFixed(0)}</td>
        <td class="num">${s.ref_shift_mv != null ? Number(s.ref_shift_mv).toFixed(1) : '—'}</td>
        <td class="num">${s.temp_f != null ? Number(s.temp_f).toFixed(1) : '—'}</td>
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
        <td><span class="ch-dot" style="background:${CH_COLORS[i]}"></span>Anode ${i+1} <span class="muted">(idx ${i})</span></td>
        <td>${fmtSecs(c.wet_s || 0)}</td>
        <td class="num">${(c.ma_s || 0).toFixed(0)}</td>
        <td class="num">${q.toFixed(4)}</td>
      </tr>`;
    }).join('');
  } catch (e) {}
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  fetchLive();
  loadHistory(activeMinutes);
});
setInterval(fetchLive, 400);
setInterval(() => loadHistory(activeMinutes), 2000);
setInterval(fetchStats, 5000);
setInterval(fetchDaily, 15000);
setInterval(fetchSessions, 45000);
fetchLive();
loadHistory(60);
fetchStats();
fetchDaily();
fetchSessions();
</script>
</body>
</html>
"""

DASHBOARD_HTML = (
    DASHBOARD_HTML.replace("__NUM_CH__", str(cfg.NUM_CHANNELS)).replace(
        "__IDX_MAX__", str(max(0, int(cfg.NUM_CHANNELS) - 1))
    )
    .replace(
        "__MA_NOISE_FLOOR__",
        f"{float(getattr(cfg, 'INA219_CURRENT_NOISE_FLOOR_MA', 0.03) or 0.03):.3f}",
    )
)


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


def main() -> None:
    p = argparse.ArgumentParser(description="CoilShield web dashboard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument(
        "--log-dir",
        metavar="DIR",
        default=None,
        help="Telemetry directory (absolute path). Same as COILSHIELD_LOG_DIR; applied from argv before config import.",
    )
    # --channels / --anodes: see config/argv_channels.py (import-time); allow unknowns for passthrough.
    args, _unknown = p.parse_known_args()

    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    _warn_sqlite_lag_support()
    _tp = cfg.resolved_telemetry_paths()
    print(f"CoilShield dashboard: http://127.0.0.1:{args.port} (bind {args.host}:{args.port})")
    print("Telemetry paths (must match `iccp start`):")
    print(f"  latest.json ← {_tp['latest_json']}")
    print(f"  SQLite      ← {_tp['sqlite_db']}")
    print(f"  LOG_DIR={_tp['log_dir']} (source: {_tp['log_dir_source']})")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


_DIRECT_EXEC_REDIRECT = (
    "Direct execution is not supported. Use the iccp CLI:\n"
    "  iccp start        # was: python3 main.py\n"
    "  iccp tui          # was: python3 tui.py\n"
    "  iccp probe        # was: python3 hw_probe.py\n"
    "  iccp dashboard    # was: python3 dashboard.py\n"
    "  iccp commission   # was: ad-hoc commissioning\n"
    "Install once with: pip install -e . (from repo root)\n"
)


if __name__ == "__main__":
    sys.stderr.write(_DIRECT_EXEC_REDIRECT)
    raise SystemExit(2)
