"""Zinc reference electrode shift and protection band."""

from __future__ import annotations

import pytest

import config.settings as cfg
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


def test_protection_status_from_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200)
    ref = ReferenceElectrode()
    ref.native_mv = 200.0
    monkeypatch.setattr(ReferenceElectrode, "read", lambda self, duties=None, statuses=None: 300.0)
    shift = ref.shift_mv(duties={}, statuses={})
    assert shift == 100.0
    assert ref.protection_status(shift) == "OK"
