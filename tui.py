#!/usr/bin/env python3
"""
CoilShield ICCP — Textual terminal control center (SSH-friendly).

Launch (after ``pip install -e .`` from repo root):
    iccp tui

Live feed: ``latest.json`` (same as web dashboard). Trends: SQLite ``readings``.
With no ``COILSHIELD_LOG_DIR`` / ``--log-dir``, Linux follows a running ``iccp start``; else set
env to match the controller explicitly.

Tabs: Live | Diagnostics | Commands | Trends. Keys: 1–4 tabs, ? help, q quit.

Direct execution (``python3 tui.py``) is not supported — it prints a redirect and
exits. The module stays importable so ``iccp tui`` can drive it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from config.argv_log_dir import (
    apply_coilshield_log_dir_from_argv,
    apply_coilshield_log_dir_from_running_controller_if_unset,
)
from config.argv_channels import apply_coilshield_active_channels_from_argv

apply_coilshield_log_dir_from_argv(sys.argv[1:])
apply_coilshield_log_dir_from_running_controller_if_unset()
if apply_coilshield_active_channels_from_argv(sys.argv[1:]) == 2:
    raise SystemExit(2)

import config.settings as cfg

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.coordinate import Coordinate
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Header,
        RichLog,
        Static,
        TabbedContent,
        TabPane,
    )
except ImportError as e:  # pragma: no cover - import guard for Pi without textual
    print(
        "textual is required: pip install textual  "
        "(see requirements.txt)",
        file=sys.stderr,
    )
    raise SystemExit(1) from e

from telemetry_queries import db_path, trends_table_rows

PROJECT_ROOT = Path(__file__).resolve().parent
_TCSS_FILE = Path(__file__).resolve().with_suffix(".tcss")
LATEST_PATH = cfg.LOG_DIR / cfg.LATEST_JSON_NAME

HELP_TEXT = """\
CoilShield terminal control center

LIVE DATA
  This app reads ONE file: latest.json under LOG_DIR (see KPI strip for path).
  The controller (`iccp start`, foreground or systemd) must write the SAME directory.
  If numbers freeze: run `iccp live` and compare paths to `systemctl cat iccp`.

TABS
  1 Live | 2 Diagnostics | 3 Commands | 4 Trends

KEYS
  r refresh  d request diag  D read diag  f clear fault  t paths  p probe
  ? this help  q quit

SAFE ACTIONS (also on Commands tab)
  Clear fault, diagnostic request/read, paths, allowlisted hw_probe --skip-pwm.

NOT FROM THIS UI
  Commissioning and a second controller need systemd stopped first — use CLI.
"""


def read_latest() -> dict:
    """
    Load latest.json with explicit error kinds (no misleading 'controller not running'
    copy when the file exists but is wrong or corrupt).
    """
    path = LATEST_PATH
    if not path.is_file():
        return {
            "_tui_read_status": "missing",
            "_tui_read_detail": str(path),
            "channels": {},
        }
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return {
            "_tui_read_status": "io",
            "_tui_read_detail": str(e),
            "channels": {},
        }
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {
            "_tui_read_status": "json",
            "_tui_read_detail": str(e),
            "channels": {},
        }
    if not isinstance(data, dict):
        return {
            "_tui_read_status": "bad_type",
            "_tui_read_detail": "root JSON is not an object",
            "channels": {},
        }
    _enrich_live_file_metadata(data, path)
    return data


def _enrich_live_file_metadata(data: dict, path: Path) -> None:
    """Augment snapshot like dashboard._live_envelope (file ages + thresholds)."""
    try:
        st = path.stat()
        data["feed_file_mtime_unix"] = round(st.st_mtime, 6)
        data["feed_age_s"] = round(time.time() - st.st_mtime, 3)
    except OSError:
        data["feed_file_mtime_unix"] = None
        data["feed_age_s"] = None
    thr = max(3.0, 3.0 * float(cfg.SAMPLE_INTERVAL_S))
    data["feed_stale_threshold_s"] = thr
    data["sample_interval_s"] = float(cfg.SAMPLE_INTERVAL_S)
    data["telemetry_paths"] = cfg.resolved_telemetry_paths()
    tsu = data.get("ts_unix")
    if tsu is not None and tsu != "":
        try:
            data["feed_age_json_s"] = round(max(0.0, time.time() - float(tsu)), 3)
        except (TypeError, ValueError):
            data["feed_age_json_s"] = None
    else:
        data["feed_age_json_s"] = None


def _json_ts_stale(data: dict) -> bool:
    age = data.get("feed_age_json_s")
    thr = data.get("feed_stale_threshold_s")
    if age is None or thr is None:
        return False
    try:
        return float(age) > float(thr)
    except (TypeError, ValueError):
        return False


def _disk_feed_stale(data: dict) -> bool:
    age = data.get("feed_age_s")
    thr = data.get("feed_stale_threshold_s")
    if age is None or thr is None:
        return False
    try:
        return float(age) > float(thr)
    except (TypeError, ValueError):
        return False


def _diagnostic_request_path() -> Path:
    return cfg.LOG_DIR / getattr(cfg, "DIAGNOSTIC_REQUEST_FILE", "request_diag")


def _diagnostic_snapshot_path() -> Path:
    return cfg.LOG_DIR / getattr(cfg, "DIAGNOSTIC_SNAPSHOT_JSON", "diagnostic_snapshot.json")


def _fmt_float(x: object, nd: int, empty: str = "—") -> str:
    if x is None:
        return empty
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return empty


def _fmt_int(x: object, empty: str = "—") -> str:
    if x is None:
        return empty
    try:
        return f"{int(round(float(x))):,d}"
    except (TypeError, ValueError):
        return empty


def _fmt_eff(x: object) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.3f}"
    except (TypeError, ValueError):
        return "—"


def _diag_line_from_latest(data: dict) -> str | None:
    if not getattr(cfg, "LATEST_JSON_INCLUDE_DIAG", False):
        return None
    block = data.get("diag")
    if not isinstance(block, dict) or not block:
        return None
    compact = json.dumps(block, separators=(",", ":"))
    if len(compact) > 220:
        compact = compact[:217] + "..."
    return compact


def build_header_text(data: dict) -> str:
    st = data.get("_tui_read_status")
    if st == "missing":
        return (
            "[bold red]latest.json missing[/]\n"
            f"[dim]{data.get('_tui_read_detail', '')}[/]\n"
            "Set COILSHIELD_LOG_DIR or run: iccp tui --log-dir /abs/path/logs\n"
            "[dim]Press ? for help.[/]"
        )
    if st == "io":
        return f"[bold red]Cannot read latest.json[/]\n[dim]{data.get('_tui_read_detail','')}[/]"
    if st == "json":
        return (
            "[bold red]latest.json is not valid JSON[/]\n"
            f"[dim]{data.get('_tui_read_detail','')}[/]\n"
            "File exists but is corrupt or partial."
        )
    if st == "bad_type":
        return f"[bold red]Unexpected JSON root[/]\n[dim]{data.get('_tui_read_detail','')}[/]"

    if "error" in data and "channels" not in data:
        return f"[bold red]{data.get('error', 'error')}[/]\n[path] {LATEST_PATH}"

    lines: list[str] = []
    ts = data.get("ts") or "—"
    sim = data.get("sim_time")
    head = f"[bold]CoilShield[/]  [dim]{ts}[/]"
    if sim:
        head += f"  [cyan]sim {sim}[/]"
    lines.append(head)

    if _disk_feed_stale(data) or _json_ts_stale(data):
        lines.append(
            "[bold red]STALE FEED[/]  snapshot older than threshold — "
            "controller may be stopped or writing a different LOG_DIR.  [dim]t = paths[/]"
        )

    temp = data.get("temp_f")
    temp_s = f"{float(temp):.1f} °F" if isinstance(temp, (int, float)) else "—"
    wet = int(data.get("wet", 0))
    latched = int(data.get("fault_latched", 0))
    faults = data.get("faults") or []
    fault_s = "; ".join(str(f) for f in faults) if faults else "—"
    fc = "red" if faults or latched else "green"
    lines.append(
        f"Temp {temp_s}   AnyWet={wet}   "
        f"[{fc}]Latch={latched}  Faults: {fault_s}[/]"
    )

    tpw = data.get("total_power_w")
    tma = data.get("total_ma")
    lines.append(
        f"Σ mA={_fmt_float(tma, 4)}   ΣP={_fmt_float(tpw, 4)} W   "
        f"supply≈{_fmt_float(data.get('supply_v_avg'), 3)} V"
    )

    ref_hw = str(data.get("ref_hw_message") or "").strip() or "—"
    raw = data.get("ref_raw_mv")
    shift = data.get("ref_shift_mv")
    band = data.get("ref_status") or "—"
    sense = str(data.get("ref_ads_sense") or "").strip()
    shift_s = _fmt_float(shift, 1) + " mV" if shift is not None else "—"
    raw_s = _fmt_float(raw, 1) + " mV" if raw is not None else "—"
    if sense:
        raw_s = f"{raw_s}  ({sense})" if raw_s != "—" else raw_s
    lines.append(f"Ref: {ref_hw}")
    lines.append(
        f"  raw={raw_s}   shift={shift_s}   band={band}   "
        f"hw_ok={data.get('ref_hw_ok', False)}   "
        f"baseline={'yes' if data.get('ref_baseline_set') else 'no'}"
    )
    hint = str(data.get("ref_hint") or "").strip()
    if hint:
        lines.append(f"  [dim]{hint}[/]")
    diag_line = _diag_line_from_latest(data)
    if diag_line:
        lines.append(f"  [dim]diag {diag_line}[/]")
    return "\n".join(lines)


def build_kpi_strip(data: dict) -> tuple[str, str, str, str, str]:
    """Returns (feed_banner, kpi_meta, kpi_feed, kpi_temp, kpi_ma)."""
    tp = data.get("telemetry_paths")
    if not isinstance(tp, dict):
        try:
            tp = cfg.resolved_telemetry_paths()
        except Exception:
            tp = {}
    src = str(tp.get("log_dir_source") or "—")
    lj = str(tp.get("latest_json") or str(LATEST_PATH))
    if len(lj) > 72:
        lj = lj[:35] + "…" + lj[-30:]
    meta = f"[dim]{src}[/]\n{lj}"

    d_age = data.get("feed_age_s")
    j_age = data.get("feed_age_json_s")
    thr = data.get("feed_stale_threshold_s")
    d_s = f"{d_age:.2f}s" if isinstance(d_age, (int, float)) else "—"
    j_s = f"{j_age:.2f}s" if isinstance(j_age, (int, float)) else "—"
    t_s = f"{float(thr):.1f}s" if isinstance(thr, (int, float)) else "—"
    feed = f"diskΔ {d_s}  jsonΔ {j_s}  thr {t_s}"

    if data.get("_tui_read_status"):
        feed = "[red]no valid snapshot[/]  " + feed

    banner = ""
    if data.get("_tui_disk_stall"):
        banner = (
            "[bold red]latest.json mtime not advancing[/] — "
            "wrong COILSHIELD_LOG_DIR/--log-dir or controller not writing this file."
        )
    elif _disk_feed_stale(data) or _json_ts_stale(data):
        banner = (
            "[bold yellow]Stale telemetry[/] — compare path above to systemd "
            "`Environment=COILSHIELD_LOG_DIR` on the iccp unit."
        )

    if data.get("_tui_read_status"):
        temp_s = "—"
        ma_s = "—"
    else:
        temp = data.get("temp_f")
        temp_s = f"{float(temp):.1f}°F" if isinstance(temp, (int, float)) else "—"
        tma = data.get("total_ma")
        ma_s = _fmt_float(tma, 3) + " mA" if tma is not None else "—"

    return banner, meta, feed, f"Temp {temp_s}", f"Σ {ma_s}"


def channel_rows(data: dict) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    chmap = data.get("channels") if isinstance(data.get("channels"), dict) else {}
    for i in range(cfg.NUM_CHANNELS):
        row = chmap.get(str(i), {})
        if not isinstance(row, dict):
            row = {}
        if not row:
            out.append(
                (
                    str(i + 1),
                    "—",
                    "—",
                    "—",
                    "—",
                    "—",
                    "—",
                    "",
                    "—",
                    "—",
                    "—",
                    "—",
                )
            )
            continue
        state = str(row.get("state", "—"))
        wet = "Y" if state == "PROTECTING" else ""
        ma = row.get("ma")
        bus_v = row.get("bus_v")
        duty = row.get("duty")
        z = row.get("impedance_ohm")
        if row.get("status") == "ERR" or not row:
            z_s = "—"
            imp_disp = "—"
        else:
            try:
                ma_f = float(ma or 0)
                if ma_f > 0.01:
                    z_s = _fmt_int(z)
                    imp_disp = z_s
                else:
                    z_s = "open"
                    imp_disp = "open"
            except (TypeError, ValueError):
                z_s = "—"
                imp_disp = "—"
        out.append(
            (
                str(i + 1),
                state[:12],
                _fmt_float(bus_v, 3),
                _fmt_float(ma, 2),
                _fmt_float(duty, 2),
                imp_disp,
                _fmt_float(row.get("cell_voltage_v"), 3),
                wet,
                _fmt_float(row.get("power_w"), 4),
                _fmt_float(row.get("energy_today_j"), 2),
                _fmt_eff(row.get("efficiency_ma_per_pct")),
                str(row.get("surface_hint") or "")[:14],
            )
        )
    return out


def telemetry_paths_text() -> str:
    try:
        return json.dumps(cfg.resolved_telemetry_paths(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


def iccp_version_text() -> str:
    try:
        import importlib.metadata as md

        return f"coilshield-iccp {md.version('coilshield-iccp')}"
    except Exception:
        return "coilshield-iccp version unknown (pip install -e .)"


def touch_request_diagnostic() -> None:
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    _diagnostic_request_path().touch()


def read_diagnostic_snapshot_raw() -> str | None:
    snap = _diagnostic_snapshot_path()
    if not snap.is_file():
        return None
    return snap.read_text(encoding="utf-8")


def clear_fault_file() -> tuple[bool, str]:
    path = cfg.CLEAR_FAULT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return True, str(path)
    except OSError as e:
        return False, str(e)


def run_allowlisted_probe() -> tuple[int, str]:
    cmd = [
        sys.executable,
        "-c",
        "import sys; sys.argv = ['hw_probe', '--skip-pwm']; "
        "import hw_probe; raise SystemExit(hw_probe.main())",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = f"\n\n[exit code {proc.returncode}]"
        return proc.returncode, out + tail
    except subprocess.TimeoutExpired:
        return 124, "hw_probe timed out after 180s"
    except OSError as e:
        return 1, f"probe failed: {e}"


def export_sqlite_copy() -> tuple[bool, str]:
    src = db_path()
    if not src.is_file():
        return False, "No SQLite database at " + str(src)
    dst = cfg.LOG_DIR / f"coilshield_export_{int(time.time())}.db"
    try:
        cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True, str(dst)
    except OSError as e:
        return False, str(e)


def export_csv_today_copy() -> tuple[bool, str]:
    today = time.strftime("%Y-%m-%d")
    name = f"{cfg.LOG_BASE_NAME}_{today}.csv"
    src = cfg.LOG_DIR / name
    if not src.is_file():
        return False, f"No CSV for today: {src}"
    dst = cfg.LOG_DIR / f"{cfg.LOG_BASE_NAME}_{today}_export_{int(time.time())}.csv"
    try:
        shutil.copy2(src, dst)
        return True, str(dst)
    except OSError as e:
        return False, str(e)


class InfoModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    InfoModal {
        align: center middle;
        width: 88%;
        height: 88%;
        border: thick $accent;
        background: $surface;
    }
    InfoModal RichLog {
        height: 1fr;
        border: tall $boost;
    }
    """

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(f"[bold]{self._title}[/]", id="modal_title"),
            RichLog(id="modal_body", wrap=True, highlight=False, markup=False),
            Static("[dim]q Esc — close[/]", id="modal_hint"),
            id="modal_col",
        )

    def on_mount(self) -> None:
        self.query_one("#modal_body", RichLog).write(self._body)

    def action_dismiss(self) -> None:
        self.dismiss()


class ProbeModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    ProbeModal {
        align: center middle;
        width: 88%;
        height: 88%;
        border: thick $accent;
        background: $surface;
    }
    ProbeModal RichLog {
        height: 1fr;
        border: tall $boost;
    }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold]Hardware probe[/]  [dim]iccp probe --skip-pwm[/]", id="ptitle"),
            RichLog(id="probe_body", wrap=True, highlight=False, markup=False),
            Static("[dim]q Esc — close[/]", id="phint"),
            id="pcol",
        )

    async def on_mount(self) -> None:
        log = self.query_one("#probe_body", RichLog)
        log.write("Running (allowlisted): iccp probe --skip-pwm\n\n")
        code, out = await asyncio.to_thread(run_allowlisted_probe)
        log.write(out)

    def action_dismiss(self) -> None:
        self.dismiss()


class CoilShieldTUI(App[None]):
    """Terminal control center: live JSON, SQLite trends, operator commands."""

    TITLE = "CoilShield ICCP"
    CSS_PATH = str(_TCSS_FILE) if _TCSS_FILE.is_file() else None

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("?", "show_help", "Help"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("d", "request_diagnostic", "Diag req"),
        Binding("D", "read_diagnostic", "Diag read"),
        Binding("f", "clear_fault", "Clear fault"),
        Binding("t", "show_paths", "Paths"),
        Binding("p", "run_probe", "Probe"),
        Binding("1", "tab_live", "Live"),
        Binding("2", "tab_diag", "Diag"),
        Binding("3", "tab_cmd", "Cmd"),
        Binding("4", "tab_trends", "Trends"),
    ]

    def __init__(self, poll_s: float) -> None:
        super().__init__()
        self._poll_s = poll_s
        self._status_until = 0.0
        self._status_msg = ""
        self._last_disk_mtime: float | None = None
        self._disk_same_count = 0
        self._channels_seeded = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="rootcol"):
            yield Static("", id="feed_banner")
            with Horizontal(id="kpi_row"):
                yield Static("", id="kpi_meta")
                yield Static("", id="kpi_feed")
                yield Static("", id="kpi_temp")
                yield Static("", id="kpi_ma")
            with TabbedContent(id="tabs", initial="live-pane"):
                with TabPane("Live", id="live-pane"):
                    yield Vertical(
                        Static("", id="header"),
                        DataTable(
                            id="channels",
                            zebra_stripes=True,
                            show_cursor=False,
                        ),
                        id="live_inner",
                    )
                with TabPane("Diagnostics", id="diag-pane"):
                    yield RichLog(
                        id="diaglog",
                        wrap=True,
                        highlight=False,
                        markup=False,
                    )
                with TabPane("Commands", id="cmd-pane"):
                    with VerticalScroll(id="cmd_scroll"):
                        yield Static("[bold]Safe while controller runs[/]")
                        yield Button("Clear fault latch", id="btn_clear_fault", variant="primary")
                        yield Button("Request diagnostic snapshot", id="btn_req_diag")
                        yield Button("Re-read diagnostic snapshot", id="btn_read_diag")
                        yield Button("Show telemetry paths", id="btn_paths")
                        yield Button("Hardware probe (skip PWM)", id="btn_probe")
                        yield Button("View latest.json (read-only)", id="btn_latest_json")
                        yield Button("Show version", id="btn_version")
                        yield Button("Export SQLite DB copy", id="btn_export_db")
                        yield Button("Export today's CSV copy", id="btn_export_csv")
                        yield Static("[dim]Advanced — stop systemd iccp before using CLI[/]")
                        yield Button("Commission / start controller (disabled)", id="btn_disabled", disabled=True)
                with TabPane("Trends", id="trends-pane"):
                    yield Vertical(
                        Static(
                            "Recent readings (downsampled). Refreshes every 5s.",
                            id="trends_hint",
                        ),
                        DataTable(id="trends_table", zebra_stripes=True, show_cursor=False),
                        id="trends_inner",
                    )
            yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        table = self.query_one("#channels", DataTable)
        table.add_columns(
            "CH",
            "State",
            "BusV",
            "mA",
            "PWM%",
            "Z Ω",
            "Vc",
            "Wet",
            "P(W)",
            "E(J)",
            "η",
            "Surface",
        )
        self.set_interval(self._poll_s, self.refresh_snapshot)
        self.set_interval(5.0, self.refresh_trends)
        self.refresh_snapshot()
        self._prime_diag_panel()
        self.refresh_trends()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn_clear_fault":
            self.action_clear_fault()
        elif bid == "btn_req_diag":
            self.action_request_diagnostic()
        elif bid == "btn_read_diag":
            self.action_read_diagnostic()
        elif bid == "btn_paths":
            self.action_show_paths()
        elif bid == "btn_probe":
            self.action_run_probe()
        elif bid == "btn_latest_json":
            self._show_latest_json_modal()
        elif bid == "btn_version":
            self.push_screen(InfoModal("Version", iccp_version_text()))
        elif bid == "btn_export_db":
            ok, msg = export_sqlite_copy()
            self._set_status(msg if ok else f"Export failed: {msg}", 6.0 if ok else 10.0)
        elif bid == "btn_export_csv":
            ok, msg = export_csv_today_copy()
            self._set_status(msg if ok else f"Export failed: {msg}", 6.0 if ok else 10.0)

    def _show_latest_json_modal(self) -> None:
        data = read_latest()
        try:
            body = json.dumps(data, indent=2, default=str)
        except TypeError:
            body = str(data)
        self.push_screen(InfoModal("latest.json (snapshot)", body))

    def action_show_help(self) -> None:
        self.push_screen(InfoModal("Help", HELP_TEXT))

    def _set_status(self, msg: str, seconds: float = 4.0) -> None:
        self._status_msg = msg
        self._status_until = time.time() + seconds
        try:
            self.query_one("#status", Static).update(msg)
        except Exception:
            pass

    def _maybe_clear_status(self) -> None:
        if self._status_msg and time.time() > self._status_until:
            self._status_msg = ""
            try:
                self.query_one("#status", Static).update("")
            except Exception:
                pass

    def _focus_diag_tab(self) -> None:
        try:
            self.query_one(TabbedContent).active = "diag-pane"
        except Exception:
            pass

    def _prime_diag_panel(self) -> None:
        raw = read_diagnostic_snapshot_raw()
        log = self.query_one("#diaglog", RichLog)
        log.clear()
        if raw is None:
            log.write(
                "No diagnostic_snapshot.json yet.\n"
                "Use Request diagnostic or key d (controller must be running).\n"
            )
        else:
            try:
                log.write(json.dumps(json.loads(raw), indent=2))
            except Exception:
                log.write(raw)

    def action_refresh_now(self) -> None:
        self.refresh_snapshot()

    def action_tab_live(self) -> None:
        try:
            self.query_one(TabbedContent).active = "live-pane"
        except Exception:
            pass

    def action_tab_diag(self) -> None:
        try:
            self.query_one(TabbedContent).active = "diag-pane"
        except Exception:
            pass

    def action_tab_cmd(self) -> None:
        try:
            self.query_one(TabbedContent).active = "cmd-pane"
        except Exception:
            pass

    def action_tab_trends(self) -> None:
        try:
            self.query_one(TabbedContent).active = "trends-pane"
        except Exception:
            pass

    def action_show_paths(self) -> None:
        self.push_screen(InfoModal("Telemetry paths", telemetry_paths_text()))

    def action_clear_fault(self) -> None:
        ok, detail = clear_fault_file()
        if ok:
            self._set_status(f"clear_fault OK → {detail}")
        else:
            self._set_status(f"clear_fault FAILED: {detail}", 8.0)

    def action_read_diagnostic(self) -> None:
        raw = read_diagnostic_snapshot_raw()
        log = self.query_one("#diaglog", RichLog)
        log.clear()
        if raw is None:
            log.write("No diagnostic_snapshot.json at:\n" + str(_diagnostic_snapshot_path()))
            self._set_status("No snapshot file")
        else:
            try:
                log.write(json.dumps(json.loads(raw), indent=2))
            except Exception:
                log.write(raw)
            self._set_status("Snapshot read")
        self._focus_diag_tab()

    def action_request_diagnostic(self) -> None:
        touch_request_diagnostic()
        self._set_status("request_diag touched; waiting for new snapshot…")

        snap = _diagnostic_snapshot_path()
        start_mtime = snap.stat().st_mtime if snap.is_file() else 0.0

        def worker() -> None:
            deadline = time.time() + 45.0
            body: str | None = None
            while time.time() < deadline:
                if snap.is_file() and snap.stat().st_mtime > start_mtime:
                    try:
                        raw = snap.read_text(encoding="utf-8")
                        try:
                            body = json.dumps(json.loads(raw), indent=2)
                        except Exception:
                            body = raw
                    except OSError as e:
                        body = f"read error: {e}"
                    break
                time.sleep(0.35)
            if body is None:
                body = (
                    "Timeout (45s): no new diagnostic_snapshot.json.\n"
                    "Is the controller running? (Snapshot is rate-limited.)\n"
                    f"Expected: {_diagnostic_snapshot_path()}"
                )

            payload = body

            def apply_ui() -> None:
                try:
                    log = self.query_one("#diaglog", RichLog)
                    log.clear()
                    log.write(payload)
                    self._focus_diag_tab()
                    self._set_status("Diagnostic snapshot loaded", 3.0)
                except Exception:
                    pass

            self.call_from_thread(apply_ui)

        threading.Thread(target=worker, daemon=True).start()

    def action_run_probe(self) -> None:
        self.push_screen(ProbeModal())

    def _update_disk_stall_flag(self, path: Path, data: dict) -> None:
        try:
            m = path.stat().st_mtime if path.is_file() else None
        except OSError:
            m = None
        if m is None:
            self._last_disk_mtime = None
            self._disk_same_count = 0
            data["_tui_disk_stall"] = False
            return
        if self._last_disk_mtime is not None and m == self._last_disk_mtime:
            self._disk_same_count += 1
        else:
            self._disk_same_count = 0
        self._last_disk_mtime = m
        stall_polls = max(8, int(6 / max(self._poll_s, 0.05)))
        data["_tui_disk_stall"] = bool(path.is_file() and self._disk_same_count >= stall_polls)

    def refresh_trends(self) -> None:
        try:
            table = self.query_one("#trends_table", DataTable)
        except Exception:
            return
        cols, rows = trends_table_rows(minutes=60, max_rows=50)
        if not cols:
            table.clear(columns=True)
            return
        table.clear(columns=True)
        table.add_columns(*cols)
        for row in rows:
            table.add_row(*row)

    def refresh_snapshot(self) -> None:
        self._maybe_clear_status()
        path = LATEST_PATH
        data = read_latest()
        self._update_disk_stall_flag(path, data)

        banner, meta, feed, kt, km = build_kpi_strip(data)
        try:
            self.query_one("#feed_banner", Static).update(banner)
            self.query_one("#kpi_meta", Static).update(meta)
            self.query_one("#kpi_feed", Static).update(feed)
            self.query_one("#kpi_temp", Static).update(kt)
            self.query_one("#kpi_ma", Static).update(km)
        except Exception:
            pass

        self.query_one("#header", Static).update(build_header_text(data))
        table = self.query_one("#channels", DataTable)

        if not self._channels_seeded:
            for _ in range(cfg.NUM_CHANNELS):
                table.add_row(*(["—"] * 12))
            self._channels_seeded = True

        rows = channel_rows(data)
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                table.update_cell_at(Coordinate(ri, ci), str(val))

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CoilShield Textual control center")
    p.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        metavar="SEC",
        help="seconds between latest.json reads (default: 0.25)",
    )
    # --channels / --anodes are consumed in config/argv_channels.py at import; allow here
    # so `iccp tui --channels 0,1` does not error after the subcommand reinvokes tui.
    args, _unknown = p.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    to_parse = argv if argv is not None else sys.argv[1:]
    args = _parse_args(to_parse)
    if args.poll_interval <= 0:
        print("--poll-interval must be positive", file=sys.stderr)
        return 2
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    CoilShieldTUI(poll_s=args.poll_interval).run()
    return 0


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
