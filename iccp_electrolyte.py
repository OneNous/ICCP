"""
Shared electrolyte / branch electrical helpers (CoilShield ICCP).

Used by logger, control feedforward, and sim so impedance and feedforward
predictions use one definition.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

import config.settings as cfg


def cell_impedance_ohm(
    bus_v: float, current_ma: float, i_floor: float | None = None
) -> float:
    """DC-ish ratio V_bus / I(A), floored for numerical stability (matches DataLogger)."""
    if i_floor is None:
        i_floor = float(getattr(cfg, "Z_COMPUTE_I_A_MIN", 1e-6))
    i_a = max(float(current_ma) / 1000.0, float(i_floor))
    return round(float(bus_v) / i_a, 2)


def ina219_nominal_current_lsb_ma() -> float:
    """
    Nominal shunt-current ADC step in mA: (shunt LSB in V) / R_shunt * 1000.

    INA219 shunt voltage LSB is PGA-dependent; default 10 µV (±40 mV, gain ÷1).
    """
    r = float(getattr(cfg, "INA219_SHUNT_OHMS", 1.0) or 1.0)
    lsb_v = float(getattr(cfg, "INA219_SHUNT_LSB_V", 1e-5) or 1e-5)
    return (lsb_v / max(r, 1e-9)) * 1000.0


def effective_target_ma_floor() -> float:
    """Minimum meaningful mA setpoint: max(explicit :data:`TARGET_MA_FLOOR`, sensor LSB)."""
    explicit = float(getattr(cfg, "TARGET_MA_FLOOR", 0.0) or 0.0)
    if not bool(getattr(cfg, "INA219_ENFORCE_CURRENT_LSB_FLOOR", True)):
        return explicit
    lsb = ina219_nominal_current_lsb_ma()
    return max(explicit, lsb)


def predict_duty_feedforward(
    target_ma: float, bus_v: float, median_z_ohm: float
) -> float:
    """
    Steady-state duty (%) to reach target mA if Z ≈ median and V_cell ≈ bus * duty%.

    duty ≈ 100 * I * Z / V_bus
    """
    vbus = max(float(bus_v), 1e-6)
    m = max(float(median_z_ohm), 1.0)
    t = max(float(target_ma), 0.0)
    v_cell = (t / 1000.0) * m
    duty = (v_cell / vbus) * 100.0
    from control import duty_pct_cap_for_vcell

    lo = float(getattr(cfg, "PWM_MIN_DUTY", 0.0) or 0.0)
    hi = min(
        float(getattr(cfg, "PWM_MAX_DUTY", 100.0) or 100.0),
        float(duty_pct_cap_for_vcell(vbus, cfg)),
    )
    return max(lo, min(hi, float(duty)))


def estimate_c_dl_f(current_ma: float, depol_slope_mv_s: float) -> float | None:
    """
    Rough double-layer capacitance (F) from I and depolarization slope: C = I / |dV/dt|.

    depol_slope_mv_s: signed mV/s (negative when potential falls after current cut).
    """
    s = float(depol_slope_mv_s)
    if not math.isfinite(s) or s >= 0:
        return None
    i_a = max(float(current_ma) / 1000.0, 0.0)
    dv_v_s = abs(s) / 1000.0
    if dv_v_s < 1e-12:
        return None
    c = i_a / dv_v_s
    if not math.isfinite(c) or c < 0:
        return None
    return c


def append_median_z(deq: Any, x: float) -> float | None:
    """Append *x* to a ``deque`` of Z samples; return current median (or None if empty)."""
    deq.append(float(x))
    if len(deq) < 1:
        return None
    return float(statistics.median(deq))


def health_composite(
    anode_score: float,
    surface_score: float,
    polarization_score: float,
) -> float:
    """Weighted 0..1 health (plan: 0.4 / 0.35 / 0.25)."""
    a = min(1.0, max(0.0, float(anode_score)))
    s = min(1.0, max(0.0, float(surface_score)))
    p = min(1.0, max(0.0, float(polarization_score)))
    w0 = float(getattr(cfg, "HEALTH_WEIGHT_ANODE", 0.4))
    w1 = float(getattr(cfg, "HEALTH_WEIGHT_SURFACE", 0.35))
    w2 = float(getattr(cfg, "HEALTH_WEIGHT_POLARIZATION", 0.25))
    tot = w0 + w1 + w2
    if tot <= 0:
        return 0.0
    return (a * w0 + s * w1 + p * w2) / tot


def anode_activity_score(
    galvanic_offset_mv: float | None, baseline_mv: float | None
) -> float:
    """Higher when galvanic offset stays near/above first-install baseline (losing offset = bad)."""
    if (
        baseline_mv is None
        or galvanic_offset_mv is None
        or float(baseline_mv) <= 1e-6
    ):
        return 0.5
    b = float(baseline_mv)
    a = float(galvanic_offset_mv)
    return max(0.0, min(1.0, a / b))


def surface_z_score(
    z_ohm: float, z_baseline_ohm: float | None
) -> float:
    """1.0 at or below baseline Z; degrades as Z rises above (rising = worse)."""
    if z_baseline_ohm is None or float(z_baseline_ohm) <= 0:
        return 0.5
    b = float(z_baseline_ohm)
    a = max(float(z_ohm), 1.0)
    r = a / b
    if r <= 1.0:
        return 1.0
    return max(0.0, 1.0 - min(1.0, r - 1.0))


def polarization_depol_score(
    depol_mv_s: float | None, depol_baseline_mv_s: float | None
) -> float:
    """
    For negative depol slopes, prefer magnitude not collapsing vs commissioning baseline
    (shallower = worse).
    """
    if depol_mv_s is None or depol_baseline_mv_s is None:
        return 0.5
    a = float(depol_mv_s)
    b = float(depol_baseline_mv_s)
    if not math.isfinite(a) or not math.isfinite(b):
        return 0.5
    if a >= 0 or b >= 0:
        return 0.5
    return max(0.0, min(1.0, a / b))


def system_health_composite(
    anode: float, surface: float, polarization: float
) -> float:
    return health_composite(anode, surface, polarization)


def score_from_ratio(actual: float, baseline: float, *, lower_is_better: bool) -> float:
    """Map ratio to 0..1; 1.0 = healthy at baseline, degrades as ratio moves wrong way."""
    b = float(baseline)
    a = float(actual)
    if not math.isfinite(b) or abs(b) < 1e-12 or not math.isfinite(a):
        return 0.5
    r = a / b
    if lower_is_better:
        if r <= 1.0:
            return 1.0
        return max(0.0, 1.0 - (r - 1.0))
    if r >= 1.0:
        return 1.0
    return max(0.0, r)
