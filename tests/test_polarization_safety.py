"""polarization_safety — absolute mV helpers vs config."""

from __future__ import annotations

import types

import pytest

import polarization_safety as ps


def _cfg(**kw: object) -> types.SimpleNamespace:
    base = dict(
        REF_ENABLED=True,
        CATHODE_ABSOLUTE_POTENTIAL_ENABLED=True,
        CATHODE_ABSOLUTE_MV_SIGN=1.0,
        POLARIZATION_HARD_CUTOFF_MV=-1080.0,
        POLARIZATION_FLOOR_WARNING_MV=-900.0,
        PROTECTION_WINDOW_MV_LO=-1069.0,
        PROTECTION_WINDOW_MV_HI=-969.0,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_trips_hard_cutoff_more_negative_than_limit() -> None:
    cfg = _cfg()
    assert ps.trips_hard_polarization_cutoff(-1081.0, cfg) is True
    assert ps.trips_hard_polarization_cutoff(-1080.0, cfg) is False
    assert ps.trips_hard_polarization_cutoff(-1000.0, cfg) is False


def test_sign_inverts_comparison() -> None:
    cfg = _cfg(CATHODE_ABSOLUTE_MV_SIGN=-1.0)
    # Physical +1081 with sign=-1 → effective -1081 → trips
    assert ps.trips_hard_polarization_cutoff(1081.0, cfg) is True


def test_floor_warning_too_positive() -> None:
    cfg = _cfg()
    assert ps.below_unprotected_floor_warning(-850.0, cfg) is True
    assert ps.below_unprotected_floor_warning(-950.0, cfg) is False


def test_protection_window_inclusive() -> None:
    cfg = _cfg()
    assert ps.instant_off_raw_in_protection_window(-1000.0, cfg) is True
    assert ps.instant_off_raw_in_protection_window(-1069.0, cfg) is True
    assert ps.instant_off_raw_in_protection_window(-969.0, cfg) is True
    assert ps.instant_off_raw_in_protection_window(-1100.0, cfg) is False
    assert ps.instant_off_raw_in_protection_window(-900.0, cfg) is False


def test_disabled_when_flag_off() -> None:
    cfg = _cfg(CATHODE_ABSOLUTE_POTENTIAL_ENABLED=False)
    assert ps.absolute_potential_safety_enabled(cfg) is False
    assert ps.trips_hard_polarization_cutoff(-5000.0, cfg) is False
