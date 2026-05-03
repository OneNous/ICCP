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
    monkeypatch.setattr(cfg, "COMMISSIONING_SHIFT_CONFIRM_MODE", "streak")
    monkeypatch.setattr(commissioning, "CONFIRM_TICKS", 1)
    monkeypatch.setattr(commissioning, "TARGET_RAMP_STEP_MA", 0.05)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_REPEAT_CUTS", 1)

    monkeypatch.setattr(commissioning, "_pump_control", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    ref = ReferenceElectrode()

    def _cap_native(*a, **k) -> tuple[float, str]:
        return (200.0, "ok")

    monkeypatch.setattr(ReferenceElectrode, "capture_native", _cap_native)
    monkeypatch.setattr(
        ReferenceElectrode,
        "read",
        lambda self, duties=None, statuses=None, **kwargs: 210.0,
    )
    monkeypatch.setattr(
        commissioning,
        "_instant_off_ref_mv_and_restore",
        lambda *a, **k: (100.0, -100.0, 0.0),  # shift = raw − native baseline = 100 − 200
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
    assert data.get("schema_version") == int(
        getattr(cfg, "COMMISSIONING_JSON_SCHEMA_VERSION", 2)
    )
    assert isinstance(data["native_mv"], (int, float))
    assert data["native_mv"] == 200.0
    assert data.get("native_oc_anodes_in_mv") is None
    assert data.get("galvanic_offset_mv") is None
    assert data.get("galvanic_offset_baseline_mv") is None
    assert data.get("final_shift_mv") == -100.0
    assert data.get("commissioning_complete") is True


def test_needs_commissioning_false_when_complete(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    p = tmp_path / "commissioning.json"
    p.write_text(
        json.dumps(
            {
                "native_mv": 100.0,
                "commissioned_target_ma": 0.5,
                "commissioning_complete": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(commissioning, "_COMM_FILE", p)
    assert commissioning.needs_commissioning() is False
    assert capsys.readouterr().err == ""


def test_needs_commissioning_true_when_incomplete(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "commissioning.json"
    p.write_text(
        json.dumps(
            {
                "native_mv": 100.0,
                "commissioned_target_ma": 0.5,
                "commissioning_complete": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(commissioning, "_COMM_FILE", p)
    assert commissioning.needs_commissioning() is True


def test_needs_commissioning_legacy_file_warns_once(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    p = tmp_path / "commissioning.json"
    p.write_text(
        json.dumps({"native_mv": 100.0, "commissioned_target_ma": 0.4}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(commissioning, "_COMM_FILE", p)
    monkeypatch.setattr(commissioning, "_legacy_commissioning_complete_flag_warned", False)
    assert commissioning.needs_commissioning() is False
    err1 = capsys.readouterr().err
    assert "commissioning_complete" in err1
    assert commissioning.needs_commissioning() is False
    assert capsys.readouterr().err == ""


def test_commissioning_field_mode_skips_anode_pause_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field mode skips the Phase 1 Enter pause; commissioning still does a single native capture."""
    root = tmp_path
    comm_path = root / "commissioning.json"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setattr(commissioning, "_COMM_FILE", comm_path)
    import reference as ref_mod

    monkeypatch.setattr(ref_mod, "_COMM_FILE", comm_path)
    monkeypatch.setattr(cfg, "COMMISSIONING_FIELD_MODE", True, raising=False)

    monkeypatch.setattr(commissioning, "COMMISSIONING_SETTLE_S", 0)
    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_COUNT", 1)
    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S", 0.0)
    monkeypatch.setattr(commissioning, "RAMP_SETTLE_S", 0.0)
    monkeypatch.setattr(cfg, "COMMISSIONING_SHIFT_CONFIRM_MODE", "streak")
    monkeypatch.setattr(commissioning, "CONFIRM_TICKS", 1)
    monkeypatch.setattr(commissioning, "TARGET_RAMP_STEP_MA", 0.05)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_REPEAT_CUTS", 1)

    monkeypatch.setattr(commissioning, "_pump_control", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    ref = ReferenceElectrode()
    cap_calls = 0

    def _cap_native(*a, **k) -> tuple[float, str]:
        nonlocal cap_calls
        cap_calls += 1
        return (175.0, "ok")

    monkeypatch.setattr(ReferenceElectrode, "capture_native", _cap_native)
    monkeypatch.setattr(
        ReferenceElectrode,
        "read",
        lambda self, duties=None, statuses=None, **kwargs: 175.0,
    )
    monkeypatch.setattr(
        commissioning,
        "_instant_off_ref_mv_and_restore",
        lambda *a, **k: (275.0, 100.0, 0.0),  # shift = 275 − baseline 175
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
        anode_placement_prompts=True,
    )

    assert cap_calls == 1
    assert comm_path.is_file()
    data = json.loads(comm_path.read_text())
    assert data["native_mv"] == 175.0
    assert data.get("native_oc_anodes_in_mv") is None
    assert data.get("galvanic_offset_mv") is None
    assert data.get("final_shift_mv") == 100.0
    assert data.get("commissioning_complete") is True


def test_phase2_linear_ramp_average_confirms_ring_around_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Noisy per-tick shifts whose mean is in-band finish without requiring a flat streak."""
    monkeypatch.setattr(cfg, "COMMISSIONING_SHIFT_CONFIRM_MODE", "average")
    monkeypatch.setattr(cfg, "COMMISSIONING_SHIFT_CONFIRM_SAMPLES", 3)
    monkeypatch.setattr(commissioning, "RAMP_SETTLE_S", 0.0)
    monkeypatch.setattr(commissioning, "_pump_control", lambda *a, **k: None)
    monkeypatch.setattr(
        commissioning,
        "_commission_shift_band_mv",
        lambda _ref: (-105.0, -95.0),
    )
    seq = [-102.0, -98.0, -100.0]
    n = {"i": 0}

    def _io(*a, **k):
        i = min(n["i"], len(seq) - 1)
        v = seq[n["i"]]
        n["i"] += 1
        return (1.0, v, 0.0)

    monkeypatch.setattr(commissioning, "_instant_off_ref_mv_and_restore", _io)
    ref = ReferenceElectrode()
    ctrl = SimpleNamespace(
        all_outputs_off=lambda: None,
        output_duty_pct=lambda _ch: 0.0,
        set_output_duty_pct=lambda *_a, **_k: None,
        set_pwm_carrier_hz=lambda _hz: None,
        enter_static_gate_off=lambda: None,
        leave_static_gate_off=lambda: None,
        update=lambda readings: None,
        duties=lambda: {i: 0.0 for i in range(cfg.NUM_CHANNELS)},
        channel_statuses=lambda: {i: "OPEN" for i in range(cfg.NUM_CHANNELS)},
        set_thermal_pause=lambda _active: None,
        advance_shift_fsm=lambda *a, **k: None,
    )
    out, hist = commissioning._phase2_linear_ramp_mA(
        ref,
        ctrl,
        sensors.SimSensorState(),
        lambda _m: None,
        False,
        None,
        "test-oc",
        start_ma=0.5,
    )
    assert out == 0.5
    assert len(hist) == 3
    assert n["i"] == 3


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


def test_phase2_active_channel_lines_all_default_adds_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "NUM_CHANNELS", 4, raising=False)
    monkeypatch.setattr(cfg, "ACTIVE_CHANNEL_INDICES", None, raising=False)
    lines = commissioning._phase2_active_channel_lines()
    assert len(lines) == 2
    assert "A1, A2, A3, A4" in lines[0]
    assert "By default" in lines[1] and "--anode 1" in lines[1]


def test_phase2_active_channel_subset_single_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "NUM_CHANNELS", 4, raising=False)
    monkeypatch.setattr(
        cfg, "ACTIVE_CHANNEL_INDICES", frozenset({0}), raising=False
    )
    lines = commissioning._phase2_active_channel_lines()
    assert len(lines) == 1
    assert "A1" in lines[0] and "A2" not in lines[0]
    assert "By default" not in lines[0]


def test_load_commissioned_target_warns_without_schema_version(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    p = tmp_path / "commissioning.json"
    p.write_text('{"commissioned_target_ma": 1.5}', encoding="utf-8")
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(commissioning, "_COMM_FILE", p)
    commissioning._commissioning_schema_warned.clear()
    assert commissioning.load_commissioned_target() == 1.5
    err = capsys.readouterr().err
    assert "schema_version" in err


def test_commissioning_binary_ma_lo_uses_lsb_default() -> None:
    assert float(cfg.COMMISSIONING_BINARY_MA_LO) >= float(cfg.INA219_CURRENT_LSB_MA) * 0.99
    assert float(cfg.COMMISSIONING_BINARY_MA_LO) >= 1e-4


def test_ina219_diag_digest_lines_skips_ok() -> None:
    readings = {
        0: {"ok": True, "current": 0.03},
        1: {
            "ok": False,
            "error": "DeviceRangeError: overflow",
            "diag": {
                "config_hex": "0x199f",
                "shunt_raw": 32760,
                "bus_raw": 65535,
                "pga_bits": 3,
                "ovf": True,
                "cnvr": True,
                "bus_v": 4.9,
                "current_ma": 0.0,
            },
        },
    }
    lines = commissioning._ina219_diag_digest_lines(readings)
    assert len(lines) == 1
    assert "INA219 diag" in lines[0]
    assert "shunt_raw=32760" in lines[0]
    assert "ovf=True" in lines[0]
