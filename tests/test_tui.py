"""Headless Textual smoke test for tui.py (no real terminal required)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

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
                "state": "PROTECTING" if i == 0 else "OPEN",
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
                "state": "OPEN",
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


def test_telemetry_paths_text_smoke() -> None:
    from tui import telemetry_paths_text

    s = telemetry_paths_text()
    assert "latest_json" in s or "error" in s


def test_clear_fault_file_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tui import clear_fault_file

    p = tmp_path / "clear_fault"
    monkeypatch.setattr(cfg, "CLEAR_FAULT_FILE", p)
    ok, detail = clear_fault_file()
    assert ok is True
    assert p.is_file()
    assert str(p) in detail


def test_run_allowlisted_probe_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from tui import run_allowlisted_probe

    def fake_run(*_a, **_k):
        m = MagicMock()
        m.stdout = "ok\n"
        m.stderr = ""
        m.returncode = 0
        return m

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, out = run_allowlisted_probe()
    assert code == 0
    assert "ok" in out


def test_build_header_includes_diag_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tui import build_header_text

    monkeypatch.setattr(cfg, "LATEST_JSON_INCLUDE_DIAG", True, raising=False)
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
        "diag": {"mux": 1, "ok": True},
        "channels": {
            str(i): {
                "state": "OPEN",
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
    h = build_header_text(sample)
    assert "diag" in h
    assert "mux" in h


def test_log_dir_argv_applies_before_settings_import(tmp_path: Path) -> None:
    """Match dashboard/tui: COILSHIELD_LOG_DIR from argv before config.settings loads."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    code = (
        "import os, sys\n"
        f"os.environ.pop('COILSHIELD_LOG_DIR', None)\n"
        f"os.environ.pop('ICCP_LOG_DIR', None)\n"
        "from config.argv_log_dir import apply_coilshield_log_dir_from_argv\n"
        f"apply_coilshield_log_dir_from_argv(['--log-dir', r'{tmp_path}'])\n"
        "import importlib\n"
        "s = importlib.import_module('config.settings')\n"
        f"assert s.LOG_DIR.resolve() == __import__('pathlib').Path(r'{tmp_path}').resolve()\n"
    )
    subprocess.check_call(
        [sys.executable, "-c", code],
        cwd=str(root),
        env={**os.environ, "PYTHONPATH": str(root)},
    )


async def _tui_key_actions_smoke_async(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tui import CoilShieldTUI

    p = tmp_path / "latest.json"
    monkeypatch.setattr("tui.LATEST_PATH", p)
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
                "state": "OPEN",
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
    p.write_text(json.dumps(sample), encoding="utf-8")

    app = CoilShieldTUI(poll_s=60.0)
    async with app.run_test() as pilot:
        await pilot.press("t")
        await pilot.pause(0.15)
        await pilot.press("escape")
        await pilot.pause(0.05)
        await pilot.press("f")
        await pilot.pause(0.05)
        await pilot.press("2")
        await pilot.pause(0.05)
        await pilot.press("1")
        await pilot.pause(0.05)


def test_tui_key_actions_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_tui_key_actions_smoke_async(tmp_path, monkeypatch))
