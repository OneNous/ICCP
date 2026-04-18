#!/usr/bin/env python3
"""
CoilShield ICCP — Textual live monitor (SSH-friendly).

Run while main.py is running (same as the web dashboard data source):
    python3 tui.py

Reads logs/latest.json (atomic snapshot every tick from DataLogger).
"""

from __future__ import annotations

import argparse
import json
import sys

import config.settings as cfg

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import DataTable, Footer, Header, Static
except ImportError as e:  # pragma: no cover - import guard for Pi without textual
    print(
        "textual is required: pip install textual  "
        "(see requirements.txt)",
        file=sys.stderr,
    )
    raise SystemExit(1) from e

LATEST_PATH = cfg.LOG_DIR / cfg.LATEST_JSON_NAME


def read_latest() -> dict:
    """Same contract as dashboard._latest()."""
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "no data yet — is main.py running?"}


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


class CoilShieldTUI(App[None]):
    """Poll latest.json and render channel table."""

    TITLE = "CoilShield ICCP"
    CSS = """
    Screen { layout: vertical; }
    #header {
        height: auto;
        max-height: 40%;
        border: tall $accent;
        padding: 0 1;
    }
    #channels { height: 1fr; min-height: 6; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    def __init__(self, poll_s: float) -> None:
        super().__init__()
        self._poll_s = poll_s

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Vertical(
            Static("", id="header"),
            DataTable(
                id="channels",
                zebra_stripes=True,
                show_cursor=False,
            ),
            id="main",
        )
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

    def action_refresh_now(self) -> None:
        self.refresh_snapshot()

    def refresh_snapshot(self) -> None:
        data = read_latest()
        self.query_one("#header", Static).update(build_header_text(data))
        table = self.query_one("#channels", DataTable)
        table.clear(columns=False)
        if "error" in data and "channels" not in data:
            return
        for row in channel_rows(data):
            table.add_row(*row)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CoilShield Textual live monitor (latest.json)")
    p.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        metavar="SEC",
        help="seconds between file reads (default: 0.25)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    if args.poll_interval <= 0:
        print("--poll-interval must be positive", file=sys.stderr)
        return 2
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    CoilShieldTUI(poll_s=args.poll_interval).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
