"""
CoilShield — self-commissioning (native potential + current ramp).

Reset: delete commissioning.json or call commissioning.reset().
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import config.settings as cfg
import temp as temp_mod
from channel_labels import anode_hw_label
from reference import ReferenceElectrode, _update_comm_file, find_oc_curve_metrics

if TYPE_CHECKING:
    from control import Controller

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"

def _anode_placement_should_interact(
    anode_placement_prompts: bool | None,
) -> bool:
    """True when we should block on operator Enter (removed / installed anodes)."""
    import sensors

    if anode_placement_prompts is False:
        return False
    if sensors.SIM_MODE:
        return False
    if anode_placement_prompts is None and not bool(
        getattr(cfg, "COMMISSIONING_ANODE_PLACEMENT_PROMPTS", True)
    ):
        return False
    if (os.environ.get("ICCP_COMMISSION_NO_ANODE_PROMPTS", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    return bool(sys.stdin.isatty())


def _anode_placement_pause(
    step: str,
    *,
    anode_placement_prompts: bool | None,
) -> None:
    if not _anode_placement_should_interact(anode_placement_prompts):
        return
    ts = f"[commission {time.strftime('%H:%M:%S')}]"
    if step == "before_phase1":
        banner = (
            f"\n{ts} Anode placement — before native Ecorr (Phase 1)\n"
            "  • Confirm anode assemblies are **removed** from the electrolyte (open-circuit).\n"
            "  Press Enter to start the reference measurement… "
        )
    elif step == "after_phase1":
        banner = (
            f"\n{ts} Anode placement — before CP ramp (Phase 2)\n"
            "  • Phase 1 (native) is done. **Install** anode assemblies in the cell.\n"
            "  Press Enter to continue with current ramp and polarization check… "
        )
    else:  # pragma: no cover
        banner = f"\n{ts} (internal: unknown anode step {step!r})\nPress Enter… "

    try:
        print(banner, end="", flush=True)
        input()
    except EOFError:
        pass


def _native_capture_fail_hint(cap_reason: str) -> str:
    """User-facing line for failed Phase 1 capture (see reference.capture_native reasons)."""
    t_relax = float(getattr(cfg, "T_RELAX", 30.0))
    stab = float(getattr(cfg, "NATIVE_STABILITY_MV", 5.0))
    islope = float(getattr(cfg, "NATIVE_SLOPE_MV_PER_MIN", 2.0))
    if "unstable_p2p" in cap_reason:
        return (
            "The reference (ADS) wobble (max−min) over the relax window exceeded NATIVE_STABILITY_MV. "
            f"On wet/noisy pans this is common — try NATIVE_STABILITY_MV = 8.0 or 10.0 (now {stab:g} mV), "
            f"or longer T_RELAX (now {t_relax:g} s) / quieter fluid. See config/settings.py."
        )
    if "slope_" in cap_reason or "slope" in cap_reason:
        return (
            "The reference drifted across the first vs last third of samples. "
            f"Try NATIVE_SLOPE_MV_PER_MIN = 0 (disable) or raise the limit (now {islope:g} mV/min), "
            "or extend T_RELAX. See config/settings.py."
        )
    if "rest_current" in cap_reason or "I_REST" in cap_reason:
        i = float(getattr(cfg, "I_REST_MA", 1.0))
        return (
            f"Shunt current did not rest below I_REST_MA ({i:g} mA) before capture — see "
            "I_REST_MA, COMMISSIONING_PHASE1_NATIVE_ABORT_I_MA, docs/mosfet-off-verification.md."
        )
    if "read_failed" in cap_reason:
        return "ADS1115 or mux read failed during capture — check I2C, TCA channel, and wiring."
    return f"See reference.capture_native / NATIVE_STABILITY_MV / T_RELAX in config.settings."


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
        data = json.loads(_COMM_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(
            f"[commissioning] invalid commissioning.json (treat as needs commissioning): {e}",
            file=sys.stderr,
        )
        return True
    except OSError as e:
        print(
            f"[commissioning] cannot read commissioning.json: {e}",
            file=sys.stderr,
        )
        return True
    return "native_mv" not in data


def native_recapture_due() -> bool:
    """True when the stored native baseline is older than `NATIVE_RECAPTURE_S` (spec §2.3).

    Used by the runtime to schedule a mid-run Phase 1 without triggering a full
    re-commissioning. Returns False if no timestamp is available (legacy file) so we
    do not spam re-captures on a healthy install that never stored a unix ts.
    """
    if not _COMM_FILE.exists():
        return False
    try:
        data = json.loads(_COMM_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    due = data.get("native_recapture_due_unix")
    if due is None:
        return False
    try:
        return time.time() >= float(due)
    except (TypeError, ValueError):
        return False


def load_commissioned_target() -> float:
    if not _COMM_FILE.exists():
        return cfg.TARGET_MA
    try:
        return float(
            json.loads(_COMM_FILE.read_text(encoding="utf-8")).get(
                "commissioned_target_ma", cfg.TARGET_MA
            )
        )
    except json.JSONDecodeError as e:
        print(
            f"[commissioning] invalid commissioning.json (commissioned_target_ma fallback): {e}",
            file=sys.stderr,
        )
        return cfg.TARGET_MA
    except (OSError, TypeError, ValueError) as e:
        print(
            f"[commissioning] commissioned_target_ma read error: {e}",
            file=sys.stderr,
        )
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


def _delivered_ma_report(readings: dict[int, dict]) -> str:
    """One-line INA shunt currents (actual mA) for commissioning logs after settle."""
    parts: list[str] = []
    total = 0.0
    any_ok = False
    for ch in range(cfg.NUM_CHANNELS):
        r = readings.get(ch, {})
        tag = f"A{ch + 1}"
        if r.get("ok"):
            cur = float(r.get("current", 0.0) or 0.0)
            parts.append(f"{tag}={cur:.3f} mA")
            total += cur
            any_ok = True
        else:
            err = (r.get("sensor_error") or r.get("error") or "no read").strip()
            short = err[:40] + ("…" if len(err) > 40 else "")
            parts.append(f"{tag}=N/A ({short})")
    sum_s = f"Σ={total:.3f} mA" if any_ok else "Σ=N/A"
    return f"INA delivered: {', '.join(parts)}; {sum_s}"


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
            reasons.append(f"{anode_hw_label(ch)} INA219 not ok ({err})")
            return False, reasons
        cur = abs(float(r.get("current", 999.0)))
        cur_ok = cur < i_max
        if mode_l == "current":
            if not cur_ok:
                reasons.append(
                    f"{anode_hw_label(ch)} |I|={cur:.4f} mA >= {i_max:g} mA (mode=current)"
                )
                return False, reasons
        elif mode_l == "delta_v":
            if not pre_bus or ch not in pre_bus:
                reasons.append(f"{anode_hw_label(ch)} delta_v: missing pre_bus snapshot")
                return False, reasons
            dv = pre_bus[ch] - float(r.get("bus_v", 0.0))
            if dv < dv_min:
                reasons.append(
                    f"{anode_hw_label(ch)} bus delta {dv:.4f} V < {dv_min:g} V (mode=delta_v; "
                    f"pre={pre_bus[ch]:.4f} V now={float(r.get('bus_v', 0.0)):.4f} V)"
                )
                return False, reasons
        elif mode_l == "both":
            if not cur_ok:
                reasons.append(
                    f"{anode_hw_label(ch)} |I|={cur:.4f} mA >= {i_max:g} mA (mode=both)"
                )
                return False, reasons
            if pre_bus and ch in pre_bus:
                dv = pre_bus[ch] - float(r.get("bus_v", 0.0))
                if dv < dv_min:
                    reasons.append(
                        f"{anode_hw_label(ch)} bus delta {dv:.4f} V < {dv_min:g} V (mode=both)"
                    )
                    return False, reasons
        else:
            if not cur_ok:
                reasons.append(
                    f"{anode_hw_label(ch)} |I|={cur:.4f} mA >= {i_max:g} mA (mode={mode_l!r})"
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
            f"      {anode_hw_label(ch)}: ok={r.get('ok')} I={r.get('current', '—')} "
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
        d = float(controller.output_duty_pct(ch))
        if d > 1e-6:
            issues.append(f"{anode_hw_label(ch)} duty={d:.4f}%")
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
            issues.append(f"{anode_hw_label(ch)} INA219 not ok ({err})")
            continue
        cur = abs(float(r.get("current", 999.0)))
        if cur >= i_max_ma:
            issues.append(
                f"{anode_hw_label(ch)} |I|={cur:.4f} mA (threshold {i_max_ma:g} mA)"
            )
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
        controller.all_outputs_off()
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
        joined = "; ".join(shunt_issues) if shunt_issues else "check wiring / leakage"
        n_ch = int(getattr(cfg, "NUM_CHANNELS", 4))
        list_note = ""
        if shunt_issues and len(shunt_issues) < n_ch:
            list_note = (
                f" Anodes not listed had |I| < {i_gate:g} mA (only failing anodes appear; "
                "`idx 0` = first anode, `Anode N` = harness order)."
            )
        msg = (
            "shunt current still ≥ "
            f"{i_gate:g} mA on one or more channels after {t_out:g}s — "
            + joined
            + list_note
            + f"; {tail}"
        )
        if post_long_settle:
            raise RuntimeError(
                "Native measurement aborted — channels not at rest. "
                "Check for leakage current before measuring baseline. "
                + msg
                + " If systemd or another `iccp start` still runs, stop it first — only one "
                "process may drive PWM; see docs/mosfet-off-verification.md. Otherwise raise "
                "COMMISSIONING_PHASE1_NATIVE_ABORT_I_MA (and COMMISSIONING_OC_CONFIRM_I_MA) only "
                "if you accept a weaker idle-current gate."
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
        controller.set_pwm_carrier_hz(int(alt))
        yield
    finally:
        controller.set_pwm_carrier_hz(base)


@contextmanager
def _phase1_static_gate_context(controller: Any) -> Any:
    """
    Optional Phase 1: true gate-off via static LOW (see COMMISSIONING_PHASE1_STATIC_GATE_LOW).
    Always balanced with leave_static_gate_off in finally.
    """
    if not bool(getattr(cfg, "COMMISSIONING_PHASE1_STATIC_GATE_LOW", True)):
        yield
        return
    controller.enter_static_gate_off()
    try:
        yield
    finally:
        controller.leave_static_gate_off()


def _instant_off_ref_mv_and_restore(
    controller: Controller,
    reference: ReferenceElectrode,
    sim_state: Any | None,
    *,
    log: Callable[[str], None] | None = None,
    repeat_cuts: int | None = None,
    repolarize_s: float | None = None,
    temp_f: float | None = None,
) -> tuple[float, float | None, float]:
    """
    OC sample: save duties → cut → (INA219 confirm) → ADS curve + inflection
    (or legacy dwell + read) → restore duties via set_duty → one update tick.

    Returns ``(raw_mv, shift_mv, ref_depol_rate_mv_s)``. Depolarization rate is 0 in legacy
    dwell mode. With ``COMMISSIONING_OC_REPEAT_CUTS`` > 1 (non-sequential OC), takes the median
    scalar and median rate between repolarize-soak intervals.
    """

    saved_duties = {
        ch: float(controller.output_duty_pct(ch)) for ch in range(cfg.NUM_CHANNELS)
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
        tf = temp_f if temp_f is not None else temp_mod.read_fahrenheit()
        pre_bus = _pre_bus_snapshot()
        if cut_ch is None:
            controller.all_outputs_off()
        else:
            for c in range(cfg.NUM_CHANNELS):
                controller.set_output_duty_pct(
                    c, 0.0 if c == cut_ch else saved_duties[c]
                )
        if not _wait_ina_oc_confirm(sim_state, pre_bus, cut_ch=cut_ch):
            w = (
                "WARNING: INA219 off-confirm timed out; continuing with ADS/ref OC read "
                "(degraded — tune COMMISSIONING_OC_CONFIRM_* / OCBUS_*)."
            )
            print(f"[commission {time.strftime('%H:%M:%S')}] {w}")
            if log is not None:
                log(w)
        if curve_on:
            # Match legacy path: dwell at 0% before ADS burst so first samples are not
            # dominated by the immediate post-cut transient (non-curve branch uses this sleep).
            time.sleep(INSTANT_OFF_WINDOW_S)
            samples = reference.collect_oc_decay_samples()
            if log is not None and samples:
                n = len(samples)
                if n > 6:
                    log(
                        f"  OC curve n={n} head={samples[:3]!r} … "
                        f"tail={samples[-3:]!r}"
                    )
                else:
                    log(f"  OC curve n={n} pts={samples!r}")
            inf, rate = find_oc_curve_metrics(samples)
            return float(reference.ref_temp_adjust_mv(float(inf), tf)), float(rate)
        time.sleep(INSTANT_OFF_WINDOW_S)
        return float(reference.read(temp_f=tf)), 0.0

    def _restore_saved_pwm() -> None:
        for ch, duty in saved_duties.items():
            controller.set_output_duty_pct(ch, duty)
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
                        f"  OC sequential {anode_hw_label(cut_ch)}: inflection "
                        f"{raw_vals[-1]:.1f} mV (using min across anodes for shift)"
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
    temp_f: float | None = None,
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
        temp_f=temp_f,
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


def _phase1_spec_native_capture(
    reference: ReferenceElectrode,
    controller: Any,
    sim_state: Any | None,
) -> tuple[float | None, str]:
    """Phase 1 per docs/iccp-requirements.md §3.3: median native, rest gate, static LOW."""
    controller.all_outputs_off()
    i_rest = float(getattr(cfg, "I_REST_MA", 1.0))

    def _rest_ok() -> bool:
        r = _sensor_readings(sim_state)
        for ch in range(cfg.NUM_CHANNELS):
            rd = r.get(ch, {})
            if rd.get("ok") and abs(float(rd.get("current", 0.0) or 0.0)) > i_rest:
                return False
        return True

    def _static_low() -> None:
        try:
            controller.enter_static_gate_off()
        except Exception as e:  # pragma: no cover
            print(f"[commission] static_gate_low: {e}", file=sys.stderr)

    def _restore() -> None:
        try:
            controller.leave_static_gate_off()
        except Exception as e:  # pragma: no cover
            print(f"[commission] leave static_gate: {e}", file=sys.stderr)

    with _commissioning_pwm_hz_context(controller):
        controller.set_thermal_pause(True)
        try:
            return reference.capture_native(
                temp_f=temp_mod.read_fahrenheit(),
                rest_current_ok=_rest_ok,
                static_gate_low=_static_low,
                gate_restore=_restore,
            )
        finally:
            controller.set_thermal_pause(False)


def run(
    reference: ReferenceElectrode,
    controller: Controller,
    sim_state: Any | None = None,
    verbose: bool = True,
    anode_placement_prompts: bool | None = None,
) -> float:
    import sensors

    def log(msg: str) -> None:
        if verbose:
            print(f"[commission {time.strftime('%H:%M:%S')}] {msg}")

    # Phase 1 — native potential (docs/iccp-requirements.md §3.3: median, stability/slope gates)
    log("Phase 1 — measuring native corrosion potential (spec capture / median)")
    with _phase1_static_gate_context(controller):
        _verify_phase1_drive_off(controller, sim_state, log=log if verbose else None)
    _anode_placement_pause("before_phase1", anode_placement_prompts=anode_placement_prompts)
    native_mv, cap_reason = _phase1_spec_native_capture(reference, controller, sim_state)
    if native_mv is None:
        raise RuntimeError(
            f"Phase 1 native capture failed: {cap_reason}. "
            f"{_native_capture_fail_hint(cap_reason)}"
        )
    pan_tf = temp_mod.read_fahrenheit()
    reference.save_native(native_mv, native_temp_f=pan_tf)
    if pan_tf is not None:
        log(f"Native reference scalar: {native_mv:.1f} mV (pan temp {pan_tf:.1f} °F recorded)")
    else:
        log(f"Native reference scalar: {native_mv:.1f} mV")
    log(
        f"Target polarization shift: ≥{cfg.TARGET_SHIFT_MV} mV (native − reading); "
        f"ref typically falls toward ~{native_mv - cfg.TARGET_SHIFT_MV:.1f} mV under CP"
    )

    _anode_placement_pause("after_phase1", anode_placement_prompts=anode_placement_prompts)

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
        if verbose:
            r_settle = _sensor_readings(sim_state)
            log(f"  {_delivered_ma_report(r_settle)}")

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
    if verbose:
        r_lock = _sensor_readings(sim_state)
        log(f"  {_delivered_ma_report(r_lock)}")
    _final_raw, final_shift, _f_depol = _instant_off_ref_mv_and_restore(
        controller, reference, sim_state, log=log if verbose else None
    )
    # Nested per-channel hints (docs/iccp-requirements.md §8.1 Phase 3). Written alongside
    # legacy `commissioned_target_ma` / `final_shift_mv` for backward compatibility; the
    # default setup today applies the same target to every channel, so mirror that here.
    channels_hints: dict[str, dict[str, Any]] = {}
    for ch in range(cfg.NUM_CHANNELS):
        per_ch_target = float(
            getattr(cfg, "CHANNEL_TARGET_MA", {}).get(ch, current_target_ma)
        )
        channels_hints[str(ch)] = {
            "commissioned_target_ma": round(per_ch_target, 3),
            "final_shift_mv": final_shift,
        }
    _update_comm_file(
        {
            "commissioned_target_ma": current_target_ma,
            "commissioned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "final_shift_mv": final_shift,
            "channels": channels_hints,
        }
    )
    log("Done.")
    return current_target_ma


def run_native_only(
    reference: ReferenceElectrode,
    controller: Controller,
    sim_state: Any | None = None,
    verbose: bool = True,
    anode_placement_prompts: bool | None = None,
) -> tuple[float | None, str]:
    """Phase 1 only: re-capture the native baseline without ramp/lock phases.

    Drives all channels to 0% PWM (plus Phase 1 static gate-low context), runs the new
    `ReferenceElectrode.capture_native` primitive, and — on success — persists the new
    native_mv / native_measured_at / native_recapture_due_unix fields via save_native.
    Returns (native_mv, reason). Reason is "ok" on success; otherwise a diagnostic string
    the caller should surface via telemetry / CLI exit code.
    """

    def log(msg: str) -> None:
        if verbose:
            print(f"[commission {time.strftime('%H:%M:%S')}] {msg}")

    log("Phase 1 (native-only) — measuring native corrosion potential")
    _anode_placement_pause("before_phase1", anode_placement_prompts=anode_placement_prompts)
    native_mv, reason = _phase1_spec_native_capture(reference, controller, sim_state)

    if native_mv is None:
        log(f"native capture failed: {reason}")
        return None, reason

    pan_tf = temp_mod.read_fahrenheit()
    reference.save_native(native_mv, native_temp_f=pan_tf)
    log(f"native_mv = {native_mv:.2f} mV (reason={reason})")
    return native_mv, reason
