"""
CoilShield — self-commissioning (native potential + current ramp).

Reset: delete commissioning.json or call commissioning.reset().
"""

from __future__ import annotations

import json
import statistics
import time
from contextlib import contextmanager
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import config.settings as cfg
from reference import ReferenceElectrode, _update_comm_file, find_oc_curve_metrics

if TYPE_CHECKING:
    from control import Controller

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"

COMMISSIONING_SETTLE_S: int = getattr(cfg, "COMMISSIONING_SETTLE_S", 60)
TARGET_RAMP_STEP_MA: float = float(getattr(cfg, "COMMISSIONING_RAMP_STEP_MA", 0.15))
RAMP_SETTLE_S: float = float(getattr(cfg, "COMMISSIONING_RAMP_SETTLE_S", 60.0))
CONFIRM_TICKS: int = 5
# Legacy single dwell (s) when COMMISSIONING_OC_CURVE_ENABLED is False.
INSTANT_OFF_WINDOW_S: float = float(getattr(cfg, "COMMISSIONING_INSTANT_OFF_S", 2.0))


def needs_commissioning() -> bool:
    if not _COMM_FILE.exists():
        return True
    try:
        return "native_mv" not in json.loads(_COMM_FILE.read_text())
    except Exception:
        return True


def load_commissioned_target() -> float:
    if not _COMM_FILE.exists():
        return cfg.TARGET_MA
    try:
        return float(
            json.loads(_COMM_FILE.read_text()).get(
                "commissioned_target_ma", cfg.TARGET_MA
            )
        )
    except Exception:
        return cfg.TARGET_MA


def reset() -> None:
    if _COMM_FILE.exists():
        _COMM_FILE.unlink()
    print("[commission] Cleared. Will re-commission on next boot.")


def _sensor_readings(sim_state: Any | None) -> dict[int, dict]:
    import sensors

    if sensors.SIM_MODE:
        return sensors.read_all_sim(sim_state)
    return sensors.read_all_real()


def _snapshot_bus_v(readings: dict[int, dict]) -> dict[int, float]:
    out: dict[int, float] = {}
    for ch in range(cfg.NUM_CHANNELS):
        r = readings.get(ch, {})
        if r.get("ok"):
            out[ch] = float(r["bus_v"])
    return out


def _ina_confirm_off_details(
    readings: dict[int, dict],
    pre_bus: dict[int, float] | None,
    *,
    cut_ch: int | None,
    mode: str,
) -> tuple[bool, list[str]]:
    """Return (all gates pass, human-readable failure lines for the checked channels)."""
    mode_l = str(mode).lower().strip()
    if mode_l in ("", "none"):
        return True, []
    chs = range(cfg.NUM_CHANNELS) if cut_ch is None else (cut_ch,)
    i_max = float(getattr(cfg, "COMMISSIONING_OC_CONFIRM_I_MA", 0.15))
    dv_min = float(getattr(cfg, "COMMISSIONING_OCBUS_MAX_DELTA_V", 0.05))
    reasons: list[str] = []
    for ch in chs:
        r = readings.get(ch, {})
        if not r.get("ok"):
            err = r.get("error", "unknown")
            reasons.append(f"CH{ch} INA219 not ok ({err})")
            return False, reasons
        cur = abs(float(r.get("current", 999.0)))
        cur_ok = cur < i_max
        if mode_l == "current":
            if not cur_ok:
                reasons.append(
                    f"CH{ch} |I|={cur:.4f} mA >= {i_max:g} mA (mode=current)"
                )
                return False, reasons
        elif mode_l == "delta_v":
            if not pre_bus or ch not in pre_bus:
                reasons.append(f"CH{ch} delta_v: missing pre_bus snapshot")
                return False, reasons
            dv = pre_bus[ch] - float(r.get("bus_v", 0.0))
            if dv < dv_min:
                reasons.append(
                    f"CH{ch} bus delta {dv:.4f} V < {dv_min:g} V (mode=delta_v; "
                    f"pre={pre_bus[ch]:.4f} V now={float(r.get('bus_v', 0.0)):.4f} V)"
                )
                return False, reasons
        elif mode_l == "both":
            if not cur_ok:
                reasons.append(
                    f"CH{ch} |I|={cur:.4f} mA >= {i_max:g} mA (mode=both)"
                )
                return False, reasons
            if pre_bus and ch in pre_bus:
                dv = pre_bus[ch] - float(r.get("bus_v", 0.0))
                if dv < dv_min:
                    reasons.append(
                        f"CH{ch} bus delta {dv:.4f} V < {dv_min:g} V (mode=both)"
                    )
                    return False, reasons
        else:
            if not cur_ok:
                reasons.append(
                    f"CH{ch} |I|={cur:.4f} mA >= {i_max:g} mA (mode={mode_l!r})"
                )
                return False, reasons
    return True, []


def _ina_confirm_off(
    readings: dict[int, dict],
    pre_bus: dict[int, float] | None,
    *,
    cut_ch: int | None,
    mode: str,
) -> bool:
    return _ina_confirm_off_details(readings, pre_bus, cut_ch=cut_ch, mode=mode)[0]


def _wait_ina_oc_confirm(
    sim_state: Any | None,
    pre_bus: dict[int, float] | None,
    *,
    cut_ch: int | None,
) -> bool:
    import sensors

    if sensors.SIM_MODE:
        return True
    mode = str(getattr(cfg, "COMMISSIONING_OCBUS_CONFIRM_MODE", "current"))
    if mode.lower().strip() in ("", "none"):
        return True
    deadline = time.monotonic() + float(
        getattr(cfg, "COMMISSIONING_OC_CONFIRM_TIMEOUT_S", 0.5)
    )
    last_readings: dict[int, dict] = {}
    last_reasons: list[str] = []
    while time.monotonic() < deadline:
        readings = _sensor_readings(sim_state)
        last_readings = readings
        ok, reasons = _ina_confirm_off_details(
            readings, pre_bus, cut_ch=cut_ch, mode=mode
        )
        last_reasons = reasons
        if ok:
            return True
        time.sleep(0.005)
    print(
        f"[commission {time.strftime('%H:%M:%S')}] "
        "INA219 OC confirm timeout — last gate failures:"
    )
    for ln in last_reasons:
        print(f"    {ln}")
    print("    last per-channel snapshot (ok, mA, bus_v, err):")
    for ch in range(cfg.NUM_CHANNELS):
        r = last_readings.get(ch, {})
        print(
            f"      CH{ch}: ok={r.get('ok')} I={r.get('current', '—')} "
            f"bus_v={r.get('bus_v', '—')} err={r.get('error', '')!r}"
        )
    print(
        f"    mode={mode!r}  COMMISSIONING_OC_CONFIRM_I_MA="
        f"{getattr(cfg, 'COMMISSIONING_OC_CONFIRM_I_MA', 0.15)!r}  "
        f"COMMISSIONING_OCBUS_MAX_DELTA_V="
        f"{getattr(cfg, 'COMMISSIONING_OCBUS_MAX_DELTA_V', 0.05)!r}"
    )
    return False


def _pwm_duties_all_zero(controller: Any) -> tuple[bool, list[str]]:
    """True if every channel PWM duty is 0 (gates commanded off)."""
    issues: list[str] = []
    for ch in range(cfg.NUM_CHANNELS):
        d = float(controller._pwm.duty(ch))
        if d > 1e-6:
            issues.append(f"CH{ch} duty={d:.4f}%")
    return (len(issues) == 0, issues)


def _channels_shunt_below(
    readings: dict[int, dict], i_max_ma: float
) -> tuple[bool, list[str]]:
    """True if every ok channel has |shunt current| below i_max_ma (electrode drive ~ off)."""
    issues: list[str] = []
    for ch in range(cfg.NUM_CHANNELS):
        r = readings.get(ch, {})
        if not r.get("ok"):
            err = r.get("error", "unknown")
            issues.append(f"CH{ch} INA219 not ok ({err})")
            continue
        cur = abs(float(r.get("current", 999.0)))
        if cur >= i_max_ma:
            issues.append(f"CH{ch} |I|={cur:.4f} mA (threshold {i_max_ma:g} mA)")
    return (len(issues) == 0, issues)


def _wait_phase1_shunts_off(
    sim_state: Any | None,
    i_max_ma: float,
    timeout_s: float,
) -> tuple[bool, list[str]]:
    """Poll INA219 until all channels look electrically idle or timeout (hardware only)."""
    import sensors

    if sensors.SIM_MODE:
        return True, []
    deadline = time.monotonic() + max(0.05, float(timeout_s))
    last_issues: list[str] = []
    while time.monotonic() < deadline:
        readings = _sensor_readings(sim_state)
        ok, issues = _channels_shunt_below(readings, i_max_ma)
        if ok:
            return True, []
        last_issues = issues
        time.sleep(0.02)
    return False, last_issues


def _verify_phase1_drive_off(
    controller: Any,
    sim_state: Any | None,
    *,
    log: Callable[[str], None] | None,
    post_long_settle: bool = False,
) -> None:
    """
    Confirm MOSFETs are commanded 0% and shunts show no CP current (Phase 1, open-circuit).
    Call after all_off(); typically once before the long settle and again immediately before
    native reference averaging. Logs only.
    """
    if not bool(getattr(cfg, "COMMISSIONING_PHASE1_OFF_VERIFY", True)):
        return

    pwm_ok, pwm_issues = _pwm_duties_all_zero(controller)
    if not pwm_ok:
        msg = "WARNING: PWM not at 0% when commanding off — " + "; ".join(pwm_issues)
        if log is not None:
            log(msg)
        else:
            print(f"[commission {time.strftime('%H:%M:%S')}] {msg}")
        controller._pwm.all_off()
        if sim_state is not None:
            sim_state.duties = controller.duties()

    i_loose = float(getattr(cfg, "COMMISSIONING_OC_CONFIRM_I_MA", 0.15))
    i_strict = float(getattr(cfg, "COMMISSIONING_PHASE1_NATIVE_ABORT_I_MA", 0.1))
    i_gate = i_strict if post_long_settle else i_loose
    t_out = float(getattr(cfg, "COMMISSIONING_PHASE1_OFF_CONFIRM_TIMEOUT_S", 3.0))
    shunt_ok, shunt_issues = _wait_phase1_shunts_off(sim_state, i_gate, t_out)
    if not shunt_ok:
        tail = (
            "after long settle, persistent shunt current suggests wiring/leakage or "
            "instrumentation — native baseline may be biased."
            if post_long_settle
            else (
                "shunts may still be decaying before long settle — native baseline may be biased."
            )
        )
        msg = (
            "shunt current still ≥ "
            f"{i_gate:g} mA on one or more channels after {t_out:g}s — "
            + ("; ".join(shunt_issues) if shunt_issues else "check wiring / leakage")
            + f"; {tail}"
        )
        if post_long_settle:
            raise RuntimeError(
                "Native measurement aborted — channels not at rest. "
                "Check for leakage current before measuring baseline. "
                + msg
            )
        msg = "WARNING: " + msg
        if log is not None:
            log(msg)
        else:
            print(f"[commission {time.strftime('%H:%M:%S')}] {msg}")
    else:
        nch = int(getattr(cfg, "NUM_CHANNELS", 4))
        if post_long_settle:
            ok_msg = (
                f"Phase 1 off-check after settle: all {nch} channels PWM 0% and |I| < {i_gate:g} mA "
                "(gates closed, no CP drive through shunts)."
            )
        else:
            ok_msg = (
                f"Phase 1 off-check: all {nch} channels PWM 0% and |I| < {i_gate:g} mA "
                "(gates closed, no CP drive through shunts)."
            )
        # log() already prints with [commission HH:MM:SS] when verbose — do not print twice.
        if log is not None:
            log(ok_msg)
        else:
            print(f"[commission {time.strftime('%H:%M:%S')}] {ok_msg}")


@contextmanager
def _commissioning_pwm_hz_context(controller: Any) -> Any:
    alt = getattr(cfg, "COMMISSIONING_PWM_HZ", None)
    if alt is None:
        yield
        return
    base = int(cfg.PWM_FREQUENCY_HZ)
    try:
        controller._pwm.set_pwm_frequency_hz(int(alt))
        yield
    finally:
        controller._pwm.set_pwm_frequency_hz(base)


def _instant_off_ref_mv_and_restore(
    controller: Controller,
    reference: ReferenceElectrode,
    sim_state: Any | None,
    *,
    log: Callable[[str], None] | None = None,
    repeat_cuts: int | None = None,
    repolarize_s: float | None = None,
) -> tuple[float, float | None, float]:
    """
    OC sample: save duties → cut → (INA219 confirm) → ADS curve + inflection
    (or legacy dwell + read) → restore duties via set_duty → one update tick.

    Returns ``(raw_mv, shift_mv, ref_depol_rate_mv_s)``. Depolarization rate is 0 in legacy
    dwell mode. With ``COMMISSIONING_OC_REPEAT_CUTS`` > 1 (non-sequential OC), takes the median
    scalar and median rate between repolarize-soak intervals.
    """

    saved_duties = {
        ch: float(controller._pwm.duty(ch)) for ch in range(cfg.NUM_CHANNELS)
    }
    curve_on = bool(getattr(cfg, "COMMISSIONING_OC_CURVE_ENABLED", True))
    sequential = bool(getattr(cfg, "COMMISSIONING_OC_SEQUENTIAL_CHANNELS", False))
    repeats = max(
        1,
        int(
            repeat_cuts
            if repeat_cuts is not None
            else getattr(cfg, "COMMISSIONING_OC_REPEAT_CUTS", 1)
        ),
    )
    repol_s = float(
        repolarize_s
        if repolarize_s is not None
        else getattr(cfg, "COMMISSIONING_OC_REPOLARIZE_S", 10.0)
    )

    def _pre_bus_snapshot() -> dict[int, float] | None:
        mode = str(getattr(cfg, "COMMISSIONING_OCBUS_CONFIRM_MODE", "current"))
        if mode.lower().strip() in ("", "none"):
            return None
        return _snapshot_bus_v(_sensor_readings(sim_state))

    def _one_cut_and_sample(cut_ch: int | None) -> tuple[float, float]:
        pre_bus = _pre_bus_snapshot()
        if cut_ch is None:
            controller._pwm.all_off()
        else:
            for c in range(cfg.NUM_CHANNELS):
                controller._pwm.set_duty(c, 0.0 if c == cut_ch else saved_duties[c])
        if not _wait_ina_oc_confirm(sim_state, pre_bus, cut_ch=cut_ch):
            w = (
                "WARNING: INA219 off-confirm timed out; continuing with ADS/ref OC read "
                "(degraded — tune COMMISSIONING_OC_CONFIRM_* / OCBUS_*)."
            )
            print(f"[commission {time.strftime('%H:%M:%S')}] {w}")
            if log is not None:
                log(w)
        if curve_on:
            samples = reference.collect_oc_decay_samples()
            inf, rate = find_oc_curve_metrics(samples)
            return float(inf), float(rate)
        time.sleep(INSTANT_OFF_WINDOW_S)
        return float(reference.read()), 0.0

    def _restore_saved_pwm() -> None:
        for ch, duty in saved_duties.items():
            controller._pwm.set_duty(ch, duty)
        if sim_state is not None:
            sim_state.duties = dict(saved_duties)

    raw_inst: float
    depol_rate: float = 0.0

    with _commissioning_pwm_hz_context(controller):
        if sequential:
            raw_vals: list[float] = []
            for cut_ch in range(cfg.NUM_CHANNELS):
                inf_mv, _rate = _one_cut_and_sample(cut_ch)
                raw_vals.append(inf_mv)
                if log is not None:
                    log(
                        f"  OC sequential CH{cut_ch}: inflection {raw_vals[-1]:.1f} mV "
                        f"(using min across CH for shift)"
                    )
                _restore_saved_pwm()
                readings = _sensor_readings(sim_state)
                controller.update(readings)
                if sim_state is not None:
                    sim_state.duties = controller.duties()
            raw_inst = min(raw_vals)
        else:
            raws: list[float] = []
            rates: list[float] = []
            use_repeat = repeats > 1 and not sequential
            ncuts = repeats if use_repeat else 1
            for cut_i in range(ncuts):
                inf_mv, rate_mv_s = _one_cut_and_sample(None)
                raws.append(inf_mv)
                rates.append(rate_mv_s)
                if log is not None and use_repeat:
                    log(
                        f"  OC cut {cut_i + 1}/{ncuts}: inflection {inf_mv:.1f} mV "
                        f"depol_slope={rate_mv_s:.3f} mV/s"
                    )
                _restore_saved_pwm()
                if cut_i + 1 < ncuts and repol_s > 0.0:
                    _pump_control(controller, sim_state, repol_s)
            if use_repeat:
                raw_inst = float(statistics.median(raws))
                depol_rate = float(statistics.median(rates))
            else:
                raw_inst = raws[0]
                depol_rate = rates[0]

    if sim_state is not None:
        sim_state.duties = dict(saved_duties)
    readings = _sensor_readings(sim_state)
    controller.update(readings)
    if sim_state is not None:
        sim_state.duties = controller.duties()

    shift: float | None = None
    if reference.native_mv is not None:
        shift = round(float(reference.native_mv) - raw_inst, 2)
    return raw_inst, shift, depol_rate


def instant_off_ref_measurement(
    controller: Any,
    reference: ReferenceElectrode,
    sim_state: Any | None = None,
    *,
    log: Callable[[str], None] | None = None,
) -> tuple[float, float | None, float]:
    """
    Same instant-off / OC-curve path as commissioning (for runtime outer-loop monitoring).
    Returns ``(raw_mv, shift_mv, ref_depol_rate_mv_s)``.

    Uses ``OUTER_LOOP_OC_REPEAT_CUTS`` / ``OUTER_LOOP_OC_REPOLARIZE_S`` so the slow loop
    stays responsive; commissioning keeps its own repeat/repolarize settings.
    """
    rpt = int(getattr(cfg, "OUTER_LOOP_OC_REPEAT_CUTS", 1))
    rep = float(getattr(cfg, "OUTER_LOOP_OC_REPOLARIZE_S", 0.0))
    return _instant_off_ref_mv_and_restore(
        controller,
        reference,
        sim_state,
        log=log,
        repeat_cuts=rpt,
        repolarize_s=rep,
    )


def _pump_control(
    controller: Controller,
    sim_state: Any | None,
    duration_s: float,
) -> None:
    """Run the normal control loop for duration_s (settle / ramp)."""
    t_end = time.monotonic() + duration_s
    while time.monotonic() < t_end:
        readings = _sensor_readings(sim_state)
        controller.update(readings)
        if sim_state is not None:
            sim_state.duties = controller.duties()
        time.sleep(cfg.SAMPLE_INTERVAL_S)


def run(
    reference: ReferenceElectrode,
    controller: Controller,
    sim_state: Any | None = None,
    verbose: bool = True,
) -> float:
    import sensors

    def log(msg: str) -> None:
        if verbose:
            print(f"[commission {time.strftime('%H:%M:%S')}] {msg}")

    native_n = int(getattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_COUNT", 30))
    native_iv = float(getattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S", 2.0))

    # Phase 1 — native potential
    log("Phase 1 — measuring native corrosion potential")
    controller._pwm.all_off()
    _verify_phase1_drive_off(controller, sim_state, log=log if verbose else None)
    log(f"Channels off. Settling {COMMISSIONING_SETTLE_S}s ...")
    _pump_control(controller, sim_state, float(COMMISSIONING_SETTLE_S))

    controller._pwm.all_off()
    if sim_state is not None:
        sim_state.duties = controller.duties()

    _verify_phase1_drive_off(
        controller, sim_state, log=log if verbose else None, post_long_settle=True
    )

    log(
        f"Averaging {native_n} reference samples "
        f"({native_iv:g}s apart, ~{native_n * native_iv:.0f}s window) ..."
    )
    # One FSM tick after settle, then freeze statuses — native loop does not run update()
    # so REGULATE cannot apply probe duty between reads.
    readings = _sensor_readings(sim_state)
    controller.update(readings)
    controller._pwm.all_off()
    if sim_state is not None:
        sim_state.duties = controller.duties()
    status_snap = {
        ch: controller.channel_statuses().get(ch, "OPEN") for ch in range(cfg.NUM_CHANNELS)
    }
    zero_duties = {ch: 0.0 for ch in range(cfg.NUM_CHANNELS)}

    samples: list[float] = []
    with _commissioning_pwm_hz_context(controller):
        for _ in range(native_n):
            controller._pwm.all_off()
            if sim_state is not None:
                sim_state.duties = dict(zero_duties)
            samples.append(
                reference.read(duties=zero_duties, statuses=status_snap)
            )
            time.sleep(native_iv)

    native_mv = round(sum(samples) / len(samples), 2)
    reference.save_native(native_mv)
    log(f"Native reference scalar: {native_mv:.1f} mV")
    log(
        f"Target polarization shift: ≥{cfg.TARGET_SHIFT_MV} mV (native − reading); "
        f"ref typically falls toward ~{native_mv - cfg.TARGET_SHIFT_MV:.1f} mV under CP"
    )

    # Phase 2 — ramp until target shift
    log("Phase 2 — ramping current toward target polarization")
    current_target_ma = max(cfg.TARGET_MA * 0.1, 0.05)
    confirm_count = 0
    curve_on = bool(getattr(cfg, "COMMISSIONING_OC_CURVE_ENABLED", True))
    if curve_on:
        if bool(getattr(cfg, "COMMISSIONING_OC_DURATION_MODE", False)):
            ds = float(getattr(cfg, "COMMISSIONING_OC_CURVE_DURATION_S", 3.0))
            oc_desc = f"OC duration {ds:g}s window + inflection, duty restore"
        else:
            burst_n = int(getattr(cfg, "COMMISSIONING_OC_BURST_SAMPLES", 20))
            burst_iv = float(getattr(cfg, "COMMISSIONING_OC_BURST_INTERVAL_S", 0.01))
            if burst_iv <= 0.0:
                oc_desc = (
                    f"OC curve {burst_n} samples back-to-back (ADC-paced), "
                    "inflection, duty restore"
                )
            else:
                oc_desc = f"OC curve {burst_n}×{burst_iv:g}s + inflection, duty restore"
    else:
        oc_desc = f"dwell {INSTANT_OFF_WINDOW_S:.1f}s + single read"

    while current_target_ma <= cfg.MAX_MA:
        cfg.TARGET_MA = round(current_target_ma, 3)
        log(
            f"  TARGET_MA = {current_target_ma:.3f} mA, "
            f"regulating {RAMP_SETTLE_S:.0f}s ..."
        )
        _pump_control(controller, sim_state, RAMP_SETTLE_S)

        log(f"  instant-off ({oc_desc}) …")
        raw, shift, _depol = _instant_off_ref_mv_and_restore(
            controller, reference, sim_state, log=log if verbose else None
        )
        shift_str = f"{shift:.1f}" if shift is not None else "N/A"
        log(
            f"  ref@off: {raw:.1f} mV  shift(native−off): {shift_str} / "
            f"{cfg.TARGET_SHIFT_MV} mV"
        )

        tol = float(getattr(cfg, "COMMISSIONING_SHIFT_CONFIRM_TOLERANCE", 0.9))
        thr = float(cfg.TARGET_SHIFT_MV)
        if shift is not None and shift >= thr:
            confirm_count += 1
            log(f"  target reached ({confirm_count}/{CONFIRM_TICKS})")
            if confirm_count >= CONFIRM_TICKS:
                break
        elif shift is not None and shift >= thr * tol:
            pass
        else:
            # Decay streak on bad samples (no hard reset) — noisy tap water.
            confirm_count = max(0, confirm_count - 1)
            ramp_coarse = float(getattr(cfg, "COMMISSIONING_RAMP_STEP_MA", 0.15))
            ramp_fine = float(getattr(cfg, "COMMISSIONING_RAMP_FINE_STEP_MA", 0.05))
            near_frac = float(
                getattr(cfg, "COMMISSIONING_RAMP_FINE_NEAR_SHIFT_FRAC", 0.5)
            )
            step = (
                ramp_fine
                if shift is not None and shift > thr * near_frac
                else ramp_coarse
            )
            current_target_ma = round(current_target_ma + step, 3)
    else:
        log(
            "WARNING: reached MAX_MA without achieving target shift — "
            "check bonding, anode contact, and water conductivity."
        )

    log(f"Phase 3 — locking in at {current_target_ma:.3f} mA/ch")
    phase3_s = max(
        float(RAMP_SETTLE_S),
        float(getattr(cfg, "COMMISSIONING_PHASE3_LOCK_SETTLE_S", 30.0)),
    )
    log(f"  final regulate / settle {phase3_s:.0f}s before last instant-off …")
    _pump_control(controller, sim_state, phase3_s)
    _final_raw, final_shift, _f_depol = _instant_off_ref_mv_and_restore(
        controller, reference, sim_state, log=log if verbose else None
    )
    _update_comm_file(
        {
            "commissioned_target_ma": current_target_ma,
            "commissioned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "final_shift_mv": final_shift,
        }
    )
    log("Done.")
    return current_target_ma
