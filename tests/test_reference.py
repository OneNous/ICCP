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


def test_shift_mv_sim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "NUM_CHANNELS", 2)
    monkeypatch.setattr(cfg, "PWM_MAX_DUTY", 80)
    monkeypatch.setattr(cfg, "SIM_NATIVE_ZINC_MV", 200.0)
    ref = ReferenceElectrode()
    ref.native_mv = 200.0
    duties = {0: 80.0, 1: 0.0}
    statuses = {0: "PROTECTING", 1: "DORMANT"}
    shift = ref.shift_mv(duties=duties, statuses=statuses)
    assert shift is not None
    assert 15 <= shift <= 40  # ~25 mV from one full-duty protecting channel + noise


def test_protection_status_from_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "TARGET_SHIFT_MV", 100)
    monkeypatch.setattr(cfg, "MAX_SHIFT_MV", 200)
    ref = ReferenceElectrode()
    ref.native_mv = 200.0
    monkeypatch.setattr(ReferenceElectrode, "read", lambda self, duties=None, statuses=None: 300.0)
    shift = ref.shift_mv(duties={}, statuses={})
    assert shift == 100.0
    assert ref.protection_status(shift) == "OK"
