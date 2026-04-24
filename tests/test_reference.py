"""Reference electrode shift, protection band, and ADS1115 scale overrides."""

from __future__ import annotations

import json
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
    assert ref.protection_status(50.0) == "UNDER"  # < 0.8 * 100
    assert ref.protection_status(79.0) == "UNDER"
    assert ref.protection_status(80.0) == "OK"
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
    assert ref.baseline_mv_for_shift() == 275.0
    monkeypatch.setattr(
        ReferenceElectrode, "read", lambda self, *a, **k: 200.0
    )
    assert ref.shift_mv() == 75.0  # 275 - 200


def test_protection_status_from_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200)
    ref = ReferenceElectrode()
    ref.native_mv = 200.0
    # shift = native − raw → 100 mV when reading has fallen 100 mV vs native
    monkeypatch.setattr(
        ReferenceElectrode,
        "read",
        lambda self, duties=None, statuses=None, **kwargs: 100.0,
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
