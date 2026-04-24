"""Self-commissioning writes commissioning.json."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import config.settings as cfg
import commissioning
import sensors
from reference import ReferenceElectrode


def test_commissioning_run_writes_json(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    comm_path = root / "commissioning.json"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setattr(commissioning, "_COMM_FILE", comm_path)
    import reference as ref_mod

    monkeypatch.setattr(ref_mod, "_COMM_FILE", comm_path)

    monkeypatch.setattr(commissioning, "COMMISSIONING_SETTLE_S", 0)
    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_COUNT", 1)
    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S", 0.0)
    monkeypatch.setattr(commissioning, "RAMP_SETTLE_S", 0.0)
    monkeypatch.setattr(commissioning, "CONFIRM_TICKS", 1)
    monkeypatch.setattr(commissioning, "TARGET_RAMP_STEP_MA", 0.05)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_REPEAT_CUTS", 1)

    monkeypatch.setattr(commissioning, "_pump_control", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    ref = ReferenceElectrode()
    _cap_n = 0

    def _cap_native(*a, **k) -> tuple[float, str]:
        nonlocal _cap_n
        _cap_n += 1
        # Phase 1a: 210 mV; Phase 1b: 200 mV → +10 mV galvanic offset (1a−1b)
        return (210.0, "ok") if _cap_n == 1 else (200.0, "ok")

    monkeypatch.setattr(ReferenceElectrode, "capture_native", _cap_native)
    monkeypatch.setattr(
        ReferenceElectrode,
        "read",
        lambda self, duties=None, statuses=None, **kwargs: 210.0,
    )
    monkeypatch.setattr(
        commissioning,
        "_instant_off_ref_mv_and_restore",
        lambda *a, **k: (100.0, 110.0, 0.0),
    )

    ctrl = SimpleNamespace(
        all_outputs_off=lambda: None,
        output_duty_pct=lambda _ch: 0.0,
        set_output_duty_pct=lambda *_a, **_k: None,
        set_pwm_carrier_hz=lambda _hz: None,
        enter_static_gate_off=lambda: None,
        leave_static_gate_off=lambda: None,
        update=lambda readings: None,
        duties=lambda: {i: 40.0 for i in range(cfg.NUM_CHANNELS)},
        channel_statuses=lambda: {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)},
        set_thermal_pause=lambda _active: None,
        advance_shift_fsm=lambda *a, **k: None,
    )

    commissioning.run(
        ref,
        ctrl,
        sim_state=sensors.SimSensorState(),
        verbose=False,
        anode_placement_prompts=False,
    )

    assert comm_path.is_file()
    data = json.loads(comm_path.read_text())
    assert "native_mv" in data
    assert "commissioned_target_ma" in data
    assert "commissioned_at" in data
    assert isinstance(data["native_mv"], (int, float))
    assert data["native_mv"] == 210.0
    assert data.get("native_oc_anodes_in_mv") == 200.0
    assert data.get("galvanic_offset_mv") == 10.0
    assert data.get("galvanic_offset_baseline_mv") == 10.0
    assert data.get("final_shift_mv") == 110.0


def test_delivered_ma_report_formats_ina_channels() -> None:
    readings = {
        0: {"ok": True, "current": 0.4, "bus_v": 5.0},
        1: {"ok": True, "current": 0.1, "bus_v": 5.0},
        2: {"ok": False, "sensor_error": "NACK", "current": 0.0, "bus_v": 0.0},
    }
    # Pad to NUM_CHANNELS if needed
    for ch in range(3, cfg.NUM_CHANNELS):
        readings[ch] = {"ok": True, "current": 0.0, "bus_v": 5.0}
    s = commissioning._delivered_ma_report(readings)
    assert "A1=0.400 mA" in s
    assert "A2=0.100 mA" in s
    assert "N/A" in s and "NACK" in s
    assert "Σ=0.500 mA" in s or "Σ=" in s


def test_ina_confirm_off_details_current_fail() -> None:
    readings = {
        i: {"ok": True, "current": 0.0, "bus_v": 5.0} for i in range(cfg.NUM_CHANNELS)
    }
    readings[1] = {"ok": True, "current": 9.0, "bus_v": 5.0}
    ok, reasons = commissioning._ina_confirm_off_details(
        readings,
        None,
        cut_ch=None,
        mode="current",
    )
    assert ok is False
    assert any("Anode 2" in r and "idx 1" in r for r in reasons)
    assert "9.0" in reasons[0]


def test_ina_confirm_off_details_not_ok_shows_error() -> None:
    readings = {0: {"ok": False, "error": "NACK", "current": 0.0, "bus_v": 0.0}}
    ok, reasons = commissioning._ina_confirm_off_details(
        readings,
        None,
        cut_ch=0,
        mode="current",
    )
    assert ok is False
    assert "NACK" in reasons[0]
