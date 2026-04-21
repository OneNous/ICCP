#!/usr/bin/env python3
"""
CoilShield ICCP — Textual live monitor (SSH-friendly).

Launch (after ``pip install -e .`` from repo root):
    iccp tui
    coilshield-tui          # same entry point, minimal typing
    python3 tui.py

Run while ``main.py`` / ``iccp -start`` is running. Reads ``latest.json`` every
``--poll-interval`` seconds (same feed as the web dashboard).

Keys: ``d`` request diagnostic snapshot, ``D`` read snapshot only, ``f`` clear
fault latch, ``t`` telemetry paths, ``p`` hardware probe (allowlisted:
``hw_probe.py --skip-pwm``), ``1``/``2`` Live / Diagnostics tabs, ``q`` quit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from config.argv_log_dir import apply_coilshield_log_dir_from_argv

apply_coilshield_log_dir_from_argv(sys.argv[1:])

import config.settings as cfg

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.screen import ModalScreen
    from textual.widgets import DataTable, Footer, Header, RichLog, Static, TabbedContent, TabPane
except ImportError as e:  # pragma: no cover - import guard for Pi without textual
    print(
        "textual is required: pip install textual  "
        "(see requirements.txt)",
        file=sys.stderr,
    )
    raise SystemExit(1) from e

PROJECT_ROOT = Path(__file__).resolve().parent
LATEST_PATH = cfg.LOG_DIR / cfg.LATEST_JSON_NAME


def read_latest() -> dict:
    """Same contract as dashboard._latest()."""
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "no data yet — is main.py running?"}


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
    if "error" in data:
        return f"[bold red]{data['error']}[/]\n[path] {LATEST_PATH}"

    lines: list[str] = []
    ts = data.get("ts") or "—"
    sim = data.get("sim_time")
    head = f"[bold]CoilShield[/]  [dim]{ts}[/]"
    if sim:
        head += f"  [cyan]sim {sim}[/]"
    lines.append(head)

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
    shift_s = _fmt_float(shift, 1) + " mV" if shift is not None else "—"
    raw_s = _fmt_float(raw, 1) + " mV" if raw is not None else "—"
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
                _fmt_float(duty, 1),
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
    """Run ``hw_probe.py --skip-pwm`` only (no arbitrary argv)."""
    cmd = [sys.executable, str(PROJECT_ROOT / "hw_probe.py"), "--skip-pwm"]
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


class InfoModal(ModalScreen[None]):
    """Scrollable text / JSON; q or Esc closes."""

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
            Static("[bold]Hardware probe[/]  [dim]hw_probe.py --skip-pwm[/]", id="ptitle"),
            RichLog(id="probe_body", wrap=True, highlight=False, markup=False),
            Static("[dim]q Esc — close[/]", id="phint"),
            id="pcol",
        )

    async def on_mount(self) -> None:
        log = self.query_one("#probe_body", RichLog)
        log.write("Running (allowlisted): python3 hw_probe.py --skip-pwm\n\n")
        code, out = await asyncio.to_thread(run_allowlisted_probe)
        log.write(out)

    def action_dismiss(self) -> None:
        self.dismiss()


class CoilShieldTUI(App[None]):
    """Poll latest.json, channel table, diagnostics tab, operator actions."""

    TITLE = "CoilShield ICCP"
    CSS = """
    Screen { layout: vertical; }
    #rootcol { height: 1fr; }
    #tabs { height: 1fr; min-height: 8; }
    #header {
        height: auto;
        max-height: 40%;
        border: tall $accent;
        padding: 0 1;
    }
    #channels { height: 1fr; min-height: 6; }
    #diaglog { height: 1fr; min-height: 6; }
    #status {
        height: auto;
        max-height: 3;
        padding: 0 1;
        background: $boost;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("d", "request_diagnostic", "Diag req"),
        Binding("D", "read_diagnostic", "Diag read"),
        Binding("f", "clear_fault", "Clear fault"),
        Binding("t", "show_paths", "Paths"),
        Binding("p", "run_probe", "Probe"),
        Binding("1", "tab_live", "Live"),
        Binding("2", "tab_diag", "Diag tab"),
    ]

    def __init__(self, poll_s: float) -> None:
        super().__init__()
        self._poll_s = poll_s
        self._status_until = 0.0
        self._status_msg = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="rootcol"):
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
            yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
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
        self.refresh_snapshot()
        self._prime_diag_panel()

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
                "Press d to request a snapshot (main.py must be running).\n"
                "Press D to re-read the file only.\n"
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
        self._focus_diag_tab()

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
                    "Is main.py running? (Snapshot is rate-limited.)\n"
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

    def refresh_snapshot(self) -> None:
        self._maybe_clear_status()
        data = read_latest()
        self.query_one("#header", Static).update(build_header_text(data))
        table = self.query_one("#channels", DataTable)
        table.clear(columns=False)
        if "error" in data and "channels" not in data:
            return
        for row in channel_rows(data):
            table.add_row(*row)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CoilShield Textual live monitor (latest.json)")
    p.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        metavar="SEC",
        help="seconds between file reads (default: 0.25)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    to_parse = argv if argv is not None else sys.argv[1:]
    args = _parse_args(to_parse)
    if args.poll_interval <= 0:
        print("--poll-interval must be positive", file=sys.stderr)
        return 2
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    CoilShieldTUI(poll_s=args.poll_interval).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
