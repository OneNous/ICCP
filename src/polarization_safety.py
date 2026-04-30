"""
Absolute cathode potential guard — **hybrid** with shift-based control.

**Shift (primary loop)** — unchanged: ``shift_mv = raw_mv − OCP_baseline`` with
**positive** shift meaning additional cathodic protection vs open circuit
(``reference.ReferenceElectrode``, ``docs/iccp-requirements.md`` §3.1). Inner
loop, commissioning ramps, and ``advance_shift_fsm`` continue to use shift.

**Absolute mV (optional safety layer)** — when
:data:`config.settings.CATHODE_ABSOLUTE_POTENTIAL_ENABLED` is True, the same
``ref.read()`` scalar is also compared to Ag/AgCl (3M KCl) **window** constants
after optional sign/scaling:

* Literature and commissioning briefs quote cathode potential **vs Ag/AgCl**
  with **more negative = more cathodically polarized** (e.g. Watkins/Davie
  aluminum window roughly −969 to −1069 mV).

* The ADS1115 reports ``AIN+ − AIN−`` in volts; ``reference`` multiplies by
  1000 and ``REF_ADS_SCALE`` / ``ref_ads_scale``. If your front-end yields the
  **opposite** polarity (positive mV when the fin is polarized negative vs
  ref), set :data:`config.settings.CATHODE_ABSOLUTE_MV_SIGN` to ``-1.0`` so
  limit checks see the same sign as the electrochemistry tables.

* Use **differential** mode with ref and cathode on the correct AIN pair; set
  ``ADS1115_FSR_V`` to match the PGA (e.g. ±4.096 V) per hardware.

This module only interprets ``raw_mv`` and ``cfg``; it does not touch I²C.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def cathode_mv_for_absolute_limits(raw_mv: float, cfg) -> float:
    """Scale ``raw_mv`` for comparison to ``POLARIZATION_*_MV`` constants."""
    sign = float(getattr(cfg, "CATHODE_ABSOLUTE_MV_SIGN", 1.0))
    return float(raw_mv) * sign


def absolute_potential_safety_enabled(cfg) -> bool:
    if not bool(getattr(cfg, "REF_ENABLED", True)):
        return False
    return bool(getattr(cfg, "CATHODE_ABSOLUTE_POTENTIAL_ENABLED", False))


def trips_hard_polarization_cutoff(raw_mv: float, cfg) -> bool:
    """
    True if ``raw_mv`` (after :func:`cathode_mv_for_absolute_limits`) is **more
    negative** than :data:`POLARIZATION_HARD_CUTOFF_MV` (alkaline-etching guard).
    """
    if not absolute_potential_safety_enabled(cfg):
        return False
    v = cathode_mv_for_absolute_limits(raw_mv, cfg)
    lim = float(getattr(cfg, "POLARIZATION_HARD_CUTOFF_MV", -1080.0))
    return v < lim


def below_unprotected_floor_warning(raw_mv: float, cfg) -> bool:
    """
    True if cathode is **less negative** than the floor (too positive) —
    i.e. not enough cathodic protection vs Ag/AgCl for the configured SKU.
    """
    if not absolute_potential_safety_enabled(cfg):
        return False
    v = cathode_mv_for_absolute_limits(raw_mv, cfg)
    floor = float(getattr(cfg, "POLARIZATION_FLOOR_WARNING_MV", -900.0))
    return v > floor


def instant_off_raw_in_protection_window(raw_mv: float, cfg) -> bool:
    """
    True if ``raw_mv`` lies in the aluminum (or SKU) band: not more negative than
    alkaline bound and not less negative than pitting bound.

    Defaults match Watkins/Davie vs Ag/AgCl (3M KCl): ``[-1069, -969]`` mV
    (more negative numbers are *left* on the number line).
    """
    if not absolute_potential_safety_enabled(cfg):
        return True
    v = cathode_mv_for_absolute_limits(raw_mv, cfg)
    lo = float(getattr(cfg, "PROTECTION_WINDOW_MV_LO", -1069.0))
    hi = float(getattr(cfg, "PROTECTION_WINDOW_MV_HI", -969.0))
    if lo > hi:
        lo, hi = hi, lo
    return lo <= v <= hi
