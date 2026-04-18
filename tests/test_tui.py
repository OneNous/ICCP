"""Headless Textual smoke test for tui.py (no real terminal required)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import config.settings as cfg
from textual.widgets import DataTable, Static


@pytest.fixture
def latest_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "latest.json"
    monkeypatch.setattr("tui.LATEST_PATH", p)
    return p


def test_read_latest_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tui.LATEST_PATH", tmp_path / "nope.json")
    from tui import read_latest

    d = read_latest()
    assert "error" in d


def test_build_header_and_rows(latest_path: Path) -> None:
    from tui import build_header_text, channel_rows

    sample = {
        "ts": "2026-01-01T12:00:00",
        "wet": True,
        "fault_latched": False,
        "faults": ["test fault"],
        "total_ma": 1.2,
        "total_power_w": 0.01,
        "supply_v_avg": 12.0,
        "temp_f": 72.5,
        "ref_raw_mv": 100.0,
        "ref_shift_mv": -5.0,
        "ref_status": "OK",
        "ref_hw_ok": True,
        "ref_hw_message": "INA219 ref",
        "ref_hint": "hint line",
        "ref_baseline_set": True,
        "channels": {
            str(i): {
                "state": "PROTECTING" if i == 0 else "DRY",
                "ma": 1.2 if i == 0 else 0.0,
                "duty": 15.0 if i == 0 else 0.0,
                "bus_v": 12.0,
                "status": "OK",
                "impedance_ohm": 10000.0 if i == 0 else 1e12,
                "cell_voltage_v": 1.8 if i == 0 else 0.0,
                "power_w": 0.0144 if i == 0 else 0.0,
                "energy_today_j": 10.0,
                "efficiency_ma_per_pct": 0.08 if i == 0 else None,
                "surface_hint": "STABLE_WET" if i == 0 else "DRY",
            }
            for i in range(cfg.NUM_CHANNELS)
        },
    }
    latest_path.write_text(json.dumps(sample), encoding="utf-8")
    h = build_header_text(sample)
    assert "72.5" in h
    assert "test fault" in h
    rows = channel_rows(sample)
    assert len(rows) == cfg.NUM_CHANNELS
    assert rows[0][7] == "Y"


def test_tui_app_renders_table(latest_path: Path) -> None:
    from tui import CoilShieldTUI

    sample = {
        "ts": "2026-01-01T12:00:00",
        "wet": False,
        "fault_latched": False,
        "faults": [],
        "total_ma": 0.0,
        "total_power_w": 0.0,
        "supply_v_avg": 12.0,
        "temp_f": 70.0,
        "ref_raw_mv": 50.0,
        "ref_shift_mv": None,
        "ref_status": "N/A",
        "ref_hw_ok": False,
        "ref_hw_message": "sim",
        "ref_hint": "",
        "ref_baseline_set": False,
        "channels": {
            str(i): {
                "state": "DRY",
                "ma": 0.0,
                "duty": 0.0,
                "bus_v": 12.0,
                "status": "OK",
                "impedance_ohm": 500000.0,
                "cell_voltage_v": 0.0,
                "power_w": 0.0,
                "energy_today_j": 0.0,
                "efficiency_ma_per_pct": None,
                "surface_hint": "DRY",
            }
            for i in range(cfg.NUM_CHANNELS)
        },
    }
    latest_path.write_text(json.dumps(sample), encoding="utf-8")

    async def _run() -> None:
        app = CoilShieldTUI(poll_s=60.0)
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            table = pilot.app.query_one("#channels", DataTable)
            assert table.row_count == cfg.NUM_CHANNELS
            pilot.app.query_one("#header", Static)

    asyncio.run(_run())
