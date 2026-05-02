"""Reference electrode shift, protection band, and ADS1115 scale overrides."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import pytest

import config.settings as cfg
import reference as ref_mod
from reference import ReferenceElectrode


def test_protection_status_known_bands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200)
    ref = ReferenceElectrode()
    assert ref.protection_status(None) == "UNKNOWN"
    assert ref.protection_status(50.0) == "UNDER"  # < TARGET_SHIFT_MV
    assert ref.protection_status(79.0) == "UNDER"
    assert ref.protection_status(80.0) == "UNDER"
    assert ref.protection_status(99.9) == "UNDER"
    assert ref.protection_status(100.0) == "OK"  # exactly TARGET_SHIFT_MV
    assert ref.protection_status(200.0) == "OK"
    assert ref.protection_status(200.1) == "OVER"


def test_read_raw_and_shift_single_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "NUM_CHANNELS", 2)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 80)
    monkeypatch.setattr(cfg, "SIM_NATIVE_ZINC_MV", 200.0)
    ref = ReferenceElectrode()
    ref.native_mv = 200.0
    duties = {0: 80.0, 1: 0.0}
    statuses = {0: "PROTECTING", 1: "OPEN"}
    raw, shift = ref.read_raw_and_shift(duties=duties, statuses=statuses)
    assert raw == ref.last_raw_mv
    assert shift is not None
    assert 15 <= (shift or 0) <= 40


def test_shift_mv_sim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "NUM_CHANNELS", 2)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 80)
    monkeypatch.setattr(cfg, "SIM_NATIVE_ZINC_MV", 200.0)
    ref = ReferenceElectrode()
    ref.native_mv = 200.0
    duties = {0: 80.0, 1: 0.0}
    statuses = {0: "PROTECTING", 1: "OPEN"}
    shift = ref.shift_mv(duties=duties, statuses=statuses)
    assert shift is not None
    assert 15 <= shift <= 40  # ~25 mV from one full-duty protecting channel + noise


def test_ina219_scalar_mv_bus_and_shunt() -> None:
    class _Fake:
        def voltage(self) -> float:
            return 0.2

        def shunt_voltage(self) -> float:
            return 15.5

    f = _Fake()
    from reference import _ina219_scalar_mv

    assert _ina219_scalar_mv(f, "bus_v") == 200.0
    assert _ina219_scalar_mv(f, "shunt_mv") == 15.5


def test_collect_oc_decay_samples_duration_mode_sim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMMISSIONING_OC_DURATION_MODE samples over a wall-time window (SIM)."""
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_DURATION_MODE", True)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_CURVE_DURATION_S", 0.06)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_CURVE_POLL_S", 0.0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    ref = ReferenceElectrode()
    pts = ref.collect_oc_decay_samples()
    assert len(pts) >= 4
    assert all(isinstance(p[0], float) and isinstance(p[1], float) for p in pts)


def test_baseline_mv_for_shift_prefers_anodes_in_ocp(monkeypatch: pytest.MonkeyPatch) -> None:
    ref = ReferenceElectrode()
    ref.native_mv = 308.0
    ref.native_oc_anodes_in_mv = 275.0
    ref.galvanic_offset_mv = 33.0
    assert ref.baseline_mv_for_shift() == 275.0
    monkeypatch.setattr(
        ReferenceElectrode, "read", lambda self, *a, **k: 200.0
    )
    assert ref.shift_mv() == -75.0  # 200 - 275 (raw − baseline)
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200)
    # Total from 1a = 100 mV → only 100−33 = 67 mV more needed from 1b baseline
    assert ref.effective_shift_target_mv() == 67.0
    assert ref.effective_max_shift_mv() == 167.0


def test_protection_status_from_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200)
    ref = ReferenceElectrode()
    ref.native_mv = 200.0
    # shift = raw − native → 100 mV when reading has risen 100 mV vs native
    monkeypatch.setattr(
        ReferenceElectrode,
        "read",
        lambda self, duties=None, statuses=None, **kwargs: 300.0,
    )
    shift = ref.shift_mv(duties={}, statuses={})
    assert shift == 100.0
    assert ref.protection_status(shift) == "OK"


def test_ref_ads_scale_from_commissioning_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "commissioning.json"
    p.write_text(
        json.dumps({"native_mv": 100.0, "ref_ads_scale": 0.5}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ref_mod, "_COMM_FILE", p)
    ref_mod._reload_comm_ref_ads_scale()
    assert ref_mod._effective_ref_ads_scale() == 0.5


def test_ref_temp_adjust_mv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "REF_TEMP_COMP_MV_PER_F", -0.5)
    ref = ReferenceElectrode()
    ref.native_temp_f = 70.0
    assert ref.ref_temp_adjust_mv(200.0, None) == 200.0
    assert ref.ref_temp_adjust_mv(200.0, 72.0) == 199.0


def test_ref_temp_uses_base_f_without_native_temp_in_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When native_temp_f is missing, anchor is REF_TEMP_COMP_BASE_F (see config.settings)."""
    monkeypatch.setattr(cfg, "REF_TEMP_COMP_MV_PER_F", 1.0)
    monkeypatch.setattr(cfg, "REF_TEMP_COMP_BASE_F", 77.0)
    ref = ReferenceElectrode()
    ref.native_temp_f = None
    assert ref.ref_temp_adjust_mv(100.0, 78.0) == pytest.approx(101.0)


def test_read_holds_last_good_after_i2c_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transient ref ADC failures must not replace ref_raw with 0.0 in telemetry."""
    monkeypatch.setattr(ref_mod, "SIM_MODE", False)
    ref = ReferenceElectrode()
    ref.native_mv = 300.0
    n = [0]

    def fake_hw() -> float:
        n[0] += 1
        if n[0] % 2 == 1:
            ref_mod._REF_LAST_READ_FAILED = False
            return 310.0
        ref_mod._REF_LAST_READ_FAILED = True
        return 0.0

    monkeypatch.setattr(ref_mod, "_read_raw_mv_hw", fake_hw)
    assert ref.read() == pytest.approx(310.0)
    assert ref.read() == pytest.approx(310.0)
    assert ref.read() == pytest.approx(310.0)
    raw, shift = ref.read_raw_and_shift()
    assert raw == pytest.approx(310.0)
    assert shift == pytest.approx(10.0)


def test_capture_native_commissioning_slope_gate_skippable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large drift over T_RELAX fails NATIVE_SLOPE unless commissioning slope is 0 (skip)."""
    monkeypatch.setattr(ref_mod, "SIM_MODE", True)
    # Wall-clock: ~11 samples (one per ramp step) then stop — median must match ``seq``.
    monkeypatch.setattr(cfg, "T_RELAX", 1.55)
    monkeypatch.setattr(cfg, "NATIVE_SAMPLE_INTERVAL_S", 0.14)
    monkeypatch.setattr(cfg, "NATIVE_CAPTURE_RETRIES", 0)
    monkeypatch.setattr(cfg, "T_REST_CONFIRM", 0.05)
    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_CAPTURE_STABILITY_MV", 100.0)
    monkeypatch.setattr(cfg, "NATIVE_SLOPE_MV_PER_MIN", 2.0)
    seq = [
        700.0,
        705.0,
        710.0,
        715.0,
        720.0,
        725.0,
        730.0,
        735.0,
        740.0,
        745.0,
        750.0,
    ]
    ref = ReferenceElectrode()
    idx: list[int] = [0]

    def _read(self: ReferenceElectrode, **kwargs: object) -> float:
        i = min(idx[0], len(seq) - 1)
        idx[0] += 1
        return seq[i]

    monkeypatch.setattr(ReferenceElectrode, "read", _read)

    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_CAPTURE_SLOPE_MV_PER_MIN", None)
    idx[0] = 0
    med_none, reason_none = ref.capture_native(rest_current_ok=lambda: True)
    assert med_none is None
    assert "slope" in reason_none

    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_CAPTURE_SLOPE_MV_PER_MIN", 0.0)
    idx[0] = 0
    med_ok, reason_ok = ref.capture_native(rest_current_ok=lambda: True)
    assert reason_ok == "ok"
    assert med_ok is not None
    assert min(seq) <= float(med_ok) <= max(seq)
    assert float(med_ok) == pytest.approx(statistics.median(seq), abs=8.0)
