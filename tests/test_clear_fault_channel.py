"""Per-channel clear-fault side channel — docs/iccp-requirements.md §6.2 (Decision Q5).

`iccp clear-fault --channel N` must clear only channel N on the next controller
tick, leaving siblings' FAULT latch (if any) untouched. The CLI writes an
atomic JSON side file at `cfg.CLEAR_FAULT_CHANNEL_FILE`; `Controller.update()`
drains it once per tick.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config.settings as cfg
from control import STATE_V2_FAULT, ChannelState, Controller
from iccp_cli import _cmd_clear_fault


def _make_controller_with_faults(*fault_channels: int) -> Controller:
    ctrl = Controller()
    for ch in fault_channels:
        ctrl._latch_fault(ch, f"test latch ch{ch}")
        ctrl._states[ch].state_v2 = STATE_V2_FAULT
        ctrl._states[ch].fault_reason = "TEST:latched"
    return ctrl


def _ok_readings() -> dict[int, dict]:
    return {
        i: {"ok": True, "current": 0.0, "bus_v": 5.0}
        for i in range(cfg.NUM_CHANNELS)
    }


def test_cli_rejects_out_of_range_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cfg, "CLEAR_FAULT_CHANNEL_FILE", tmp_path / "clear_fault.channel", raising=False
    )
    assert _cmd_clear_fault(["--channel", "99"]) == 2


def test_cli_rejects_non_integer_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cfg, "CLEAR_FAULT_CHANNEL_FILE", tmp_path / "clear_fault.channel", raising=False
    )
    assert _cmd_clear_fault(["--channel", "not-a-number"]) == 2


def test_cli_writes_json_side_file_for_valid_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "clear_fault.channel"
    monkeypatch.setattr(cfg, "CLEAR_FAULT_CHANNEL_FILE", path, raising=False)
    assert _cmd_clear_fault(["--channel", "1"]) == 0
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["channel"] == 1


def test_controller_consumes_side_file_for_single_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tick that sees the JSON side file clears only channel N."""
    path = tmp_path / "clear_fault.channel"
    monkeypatch.setattr(cfg, "CLEAR_FAULT_CHANNEL_FILE", path, raising=False)
    # Make sure the all-channel file is absent so update() does not trigger global clear.
    absent = tmp_path / "clear_fault"
    monkeypatch.setattr(cfg, "CLEAR_FAULT_FILE", absent, raising=False)

    ctrl = _make_controller_with_faults(0, 2)
    assert ctrl._states[0].status == ChannelState.FAULT
    assert ctrl._states[2].status == ChannelState.FAULT

    path.write_text(json.dumps({"channel": 2, "ts": 1.0}), encoding="utf-8")
    ctrl.update(_ok_readings())
    # Channel 2 cleared; channel 0 untouched.
    assert ctrl._states[2].status != ChannelState.FAULT
    assert ctrl._states[2].state_v2 != STATE_V2_FAULT
    assert ctrl._states[0].status == ChannelState.FAULT
    # Side file consumed (deleted) after one tick.
    assert not path.exists()


def test_controller_ignores_malformed_side_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "clear_fault.channel"
    absent = tmp_path / "clear_fault"
    monkeypatch.setattr(cfg, "CLEAR_FAULT_CHANNEL_FILE", path, raising=False)
    monkeypatch.setattr(cfg, "CLEAR_FAULT_FILE", absent, raising=False)

    ctrl = _make_controller_with_faults(0)
    path.write_text("garbage-payload", encoding="utf-8")
    ctrl.update(_ok_readings())
    # Malformed file must still be consumed (so the loop isn't stuck on retries),
    # and channel 0 remains latched.
    assert not path.exists()
    assert ctrl._states[0].status == ChannelState.FAULT
