"""
CoilShield — self-commissioning (native potential + current ramp).

Reset: delete commissioning.json or call commissioning.reset().
"""

from __future__ import annotations

import json
import os
import select
import statistics
import sys
import time
from contextlib import contextmanager
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import config.settings as cfg
import temp as temp_mod
from channel_labels import anode_hw_label
from console_ui import (
    commission_ina_compact,
    commission_log_main,
    print_commission_section,
    print_status_table,
)
from reference import (
    ReferenceElectrode,
    _update_comm_file,
    find_oc_curve_metrics,
    ref_hw_message,
)

if TYPE_CHECKING:
    from control import Controller

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"


def _commission_oc_debug() -> bool:
    """Set ``ICCP_COMMISSION_DEBUG=1`` to print full OC-curve head/tail samples (very noisy)."""
    return (os.environ.get("ICCP_COMMISSION_DEBUG", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


# Skip-hint to stderr while blocking on anode Enter. Select period matches control tick.
_TTY_ANODE_SKIP_HINT_S: float = 75.0
# Commission-only regulate / settle: time-remaining to instant-off.
_COMMISSION_PUMP_PROGRESS_S: float = 20.0


def _print_commission_status_like_start(
    controller: Any,
    reference: ReferenceElectrode,
    sim_state: Any | None,
    *,
    tick_dt_s: float | None = None,
) -> None:
    """One ``iccp start``-style status table: INA, PWM, state, ref — no ``latest.json`` row."""
    import sensors

    if sensors.SIM_MODE and sim_state is not None:
        readings = sensors.read_all_sim(sim_state)
    else:
        readings = sensors.read_all_real()
    faults, fault_latched = controller.update(readings)
    duties = controller.duties()
    if sim_state is not None:
        sim_state.duties = dict(duties)
    ch_status = controller.channel_statuses()
    any_wet = controller.any_wet()
    temp_f = temp_mod.read_fahrenheit()
    ref_raw, ref_shift = reference.read_raw_and_shift(
        duties=duties, statuses=ch_status, temp_f=temp_f
    )
    ref_band = (
        reference.protection_status(ref_shift) if ref_shift is not None else "—"
    )
    ref_valid, ref_valid_reason = reference.ref_valid()
    controller.advance_shift_fsm(
        readings,
        shift_mv=ref_shift,
        ref_valid=ref_valid,
        ref_valid_reason=ref_valid_reason,
    )
    z_med = {i: controller.median_impedance_ohm(i) for i in range(cfg.NUM_CHANNELS)}
    print_status_table(
        readings,
        faults,
        duties,
        fault_latched,
        ch_status,
        any_wet,
        ref_raw,
        ref_shift,
        ref_band,
        ref_hw_message(),
        temp_f,
        "",
        z_median=z_med,
        live_ch=None,
        ctrl=controller,
        tick_dt_s=tick_dt_s,
        path_tags=controller.channel_path_tags(),
    )


def _readline_wait_enter_for_anode_prompt(
    *,
    on_select_timeout: Callable[[], None] | None = None,
) -> None:
    """
    Block until the operator sends a line (Enter).

    Uses ``/dev/tty`` (binary) + :func:`select` with a timeout of ``SAMPLE_INTERVAL_S``:
    on each timeout, optional ``on_select_timeout`` (e.g. same status table as
    :func:`iccp start`). Skip hints to ``stderr`` every :data:`_TTY_ANODE_SKIP_HINT_S``.
    If ``/dev/tty`` cannot be opened, falls back to a single tick + :func:`input()`.
    """
    poll = max(0.05, float(getattr(cfg, "SAMPLE_INTERVAL_S", 0.5)))
    last_skip_announce = time.monotonic()
    try:
        f = open("/dev/tty", "rb", buffering=0)
    except OSError:
        if on_select_timeout is not None:
            try:
                on_select_timeout()
            except (OSError, ValueError) as e:
                print(f"[main] status line (anode wait): {e}", file=sys.stderr)
        try:
            input()
        except (EOFError, KeyboardInterrupt) as e:
            if isinstance(e, KeyboardInterrupt):
                raise
        return
    fd = f.fileno()
    try:
        while True:
            try:
                r, _, _ = select.select([fd], [], [], float(poll))
            except (OSError, ValueError):
                f.readline()
                break
            if r:
                f.readline()
                break
            if on_select_timeout is not None:
                try:
                    on_select_timeout()
                except (OSError, ValueError) as e:
                    print(f"[main] status line (anode wait): {e}", file=sys.stderr)
            if time.monotonic() - last_skip_announce >= float(_TTY_ANODE_SKIP_HINT_S):
                print(
                    "[main] To skip anode pauses: set ICCP_COMMISSION_NO_ANODE_PROMPTS=1 or "
                    " use iccp commission --no-anode-prompts",
                    file=sys.stderr,
                )
                last_skip_announce = time.monotonic()
    except KeyboardInterrupt:
        raise
    finally:
        try:
            f.close()
        except OSError:
            pass


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
    controller: Any | None = None,
    reference: ReferenceElectrode | None = None,
    sim_state: Any | None = None,
) -> None:
    if not _anode_placement_should_interact(anode_placement_prompts):
        return
    t_relax = float(getattr(cfg, "T_RELAX", 30.0))
    next_on_enter = {
        "before_phase1": "native Ecorr / baseline (Phase 1, open-circuit)",
        "after_phase1": "current ramp toward target shift (Phase 2)",
    }.get(step, "next commissioning step")
    if step == "before_phase1":
        print_commission_section("Anode placement — before native Ecorr (Phase 1)")
        print(
            "[main] Remove anode assemblies from the bath (open-circuit), "
            "then press Enter."
        )
    elif step == "after_phase1":
        print_commission_section("Anode placement — before current ramp (Phase 2)")
        print(
            "[main] Install anodes, then press Enter to continue the ramp."
        )
    else:  # pragma: no cover
        print_commission_section(f"Anode placement (internal: {step!r})")
        print("[main] Press Enter when ready.")
    print(f"[main] Next on Enter: {next_on_enter}")
    if step == "before_phase1":
        print(
            f"[main] After Enter, the reference window (T_RELAX = {t_relax:g} s) "
            "will show time remaining until the median is taken."
        )
    on_timeout: Callable[[], None] | None = None
    if (
        controller is not None
        and reference is not None
    ):
        _last = time.monotonic()

        def _on_timeout() -> None:
            nonlocal _last
            now = time.monotonic()
            _print_commission_status_like_start(
                controller,
                reference,
                sim_state,
                tick_dt_s=now - _last,
            )
            _last = now

        on_timeout = _on_timeout
    _readline_wait_enter_for_anode_prompt(on_select_timeout=on_timeout)


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
# Shorter pre-burst dwell when the OC **curve** is enabled (``COMMISSIONING_OC_INFLECTION_SKIP_RATES`` handles
# the inductive ring inside the burst).
OC_CURVE_PREBURST_S: float = float(getattr(cfg, "COMMISSIONING_OC_CURVE_PREBURST_S", 0.3))


def _check_comm_wall_deadline(deadline_mono: float | None) -> None:
    if deadline_mono is not None and time.monotonic() >= float(deadline_mono):
        raise RuntimeError(
            "Commissioning aborted: exceeded COMMISSIONING_WALL_TIMEOUT_S (see config/settings.py)."
        )


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
    commission_log_main("commissioning.json cleared — will re-commission on next boot")


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
    print("[main] INA219 OC confirm timeout — last gate failures:")
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
            commission_log_main(msg)
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
            commission_log_main(msg)
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
        # log() already prefixes [main] when verbose — do not print twice.
        if log is not None:
            log(ok_msg)
        else:
            commission_log_main(ok_msg)


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
    wall_deadline_mono: float | None = None,
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
            if log is not None:
                log(w)
            else:
                commission_log_main(w)
        if curve_on:
            # Short pre-burst settle; the burst’s skip_rates strip the inductive ring from samples.
            time.sleep(max(0.0, OC_CURVE_PREBURST_S))
            samples = reference.collect_oc_decay_samples()
            if log is not None and samples:
                n = len(samples)
                t0, p0 = samples[0]
                t1, p1 = samples[-1]
                log(
                    f"OC window: {n} pts, t={float(t0):.3f}…{float(t1):.3f} s, "
                    f"ref {float(p0):.1f}…{float(p1):.1f} mV"
                )
                if _commission_oc_debug():
                    if n > 6:
                        log(
                            f"OC detail: head={samples[:3]!r} "
                            f"tail={samples[-3:]!r}"
                        )
                    else:
                        log(f"OC detail: {samples!r}")
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

    _check_comm_wall_deadline(wall_deadline_mono)
    with _commissioning_pwm_hz_context(controller):
        if sequential:
            raw_vals: list[float] = []
            for cut_ch in range(cfg.NUM_CHANNELS):
                inf_mv, _rate = _one_cut_and_sample(cut_ch)
                raw_vals.append(inf_mv)
                if log is not None:
                    log(
                        f"OC sequential {anode_hw_label(cut_ch)}: inflection "
                        f"{raw_vals[-1]:.1f} mV (min across anodes for shift)"
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
                        f"OC cut {cut_i + 1}/{ncuts}: inflection {inf_mv:.1f} mV, "
                        f"depol_slope {rate_mv_s:.3f} mV/s"
                    )
                _restore_saved_pwm()
                if cut_i + 1 < ncuts and repol_s > 0.0:
                    _pump_control(
                        controller,
                        sim_state,
                        repol_s,
                        reference=reference,
                        wall_deadline_mono=wall_deadline_mono,
                    )
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
    *,
    reference: ReferenceElectrode | None = None,
    wall_deadline_mono: float | None = None,
    commission_log: Callable[[str], None] | None = None,
    progress_label: str = "Regulate",
    progress_next: str = "instant-off",
    progress_interval_s: float = _COMMISSION_PUMP_PROGRESS_S,
) -> None:
    """Run the normal control loop for duration_s (settle / ramp).

    If ``commission_log`` is set (commissioning UI only), log **time remaining** until
    ``progress_next`` at ``progress_interval_s`` (monotonic wall, not tick drift).
    """
    duration = max(0.0, float(duration_s))
    t_end = time.monotonic() + duration
    last_progress_announce: float = 0.0
    cl = commission_log
    need_progress = cl is not None and duration > 0.5 and float(progress_interval_s) > 0
    if need_progress:
        # First line on first loop iteration.
        last_progress_announce = time.monotonic() - float(progress_interval_s)
    while time.monotonic() < t_end:
        if need_progress and cl is not None:
            now = time.monotonic()
            if now - last_progress_announce >= float(progress_interval_s):
                rem = max(0.0, t_end - now)
                rsec = int(rem)
                mm, ss = divmod(rsec, 60)
                cl(
                    f"{progress_label} — ~{mm:d}:{ss:02d} until {progress_next}"
                )
                last_progress_announce = now
        _check_comm_wall_deadline(wall_deadline_mono)
        readings = _sensor_readings(sim_state)
        controller.update(readings)
        if sim_state is not None:
            sim_state.duties = controller.duties()
        if reference is not None:
            tf = temp_mod.read_fahrenheit()
            duties = controller.duties()
            st = controller.channel_statuses()
            _raw, ref_shift = reference.read_raw_and_shift(
                duties=duties, statuses=st, temp_f=tf
            )
            ref_valid, ref_valid_reason = reference.ref_valid()
            controller.advance_shift_fsm(
                readings,
                shift_mv=ref_shift,
                ref_valid=ref_valid,
                ref_valid_reason=ref_valid_reason,
            )
        time.sleep(cfg.SAMPLE_INTERVAL_S)


def _phase1_spec_native_capture(
    reference: ReferenceElectrode,
    controller: Any,
    sim_state: Any | None,
    *,
    on_relax_progress: Callable[[float, int], None] | None = None,
) -> tuple[float | None, str]:
    """Phase 1 per docs/iccp-requirements.md §3.3: median native, rest gate, static LOW."""
    controller.all_outputs_off()
    i_rest = float(getattr(cfg, "I_REST_MA", 1.0))
    use_static_ctx = bool(getattr(cfg, "COMMISSIONING_PHASE1_STATIC_GATE_LOW", True))

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

    def _capture_once() -> tuple[float | None, str]:
        with _commissioning_pwm_hz_context(controller):
            controller.set_thermal_pause(True)
            try:
                return reference.capture_native(
                    temp_f=temp_mod.read_fahrenheit(),
                    rest_current_ok=_rest_ok,
                    # When True: outer ``_phase1_static_gate_context`` holds static LOW; do not
                    # pair duplicate enter/leave with reference.capture_native (its finally still
                    # calls gate_restore when non-None).
                    static_gate_low=None if use_static_ctx else _static_low,
                    gate_restore=None if use_static_ctx else _restore,
                    on_relax_progress=on_relax_progress,
                )
            finally:
                controller.set_thermal_pause(False)

    if use_static_ctx:
        with _phase1_static_gate_context(controller):
            return _capture_once()
    return _capture_once()


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
            commission_log_main(msg)

    if verbose:
        print()
        print_commission_section("Phase 1 — native Ecorr (open-circuit, spec capture)")
    with _phase1_static_gate_context(controller):
        _verify_phase1_drive_off(controller, sim_state, log=log if verbose else None)
    _anode_placement_pause(
        "before_phase1",
        anode_placement_prompts=anode_placement_prompts,
        controller=controller,
        reference=reference,
        sim_state=sim_state,
    )

    def on_relax_progress(remaining: float, n: int) -> None:
        if not verbose:
            return
        commission_log_main(
            f"Reference window: ~{remaining:.0f} s until median; {n} sample(s) so far"
        )

    native_mv, cap_reason = _phase1_spec_native_capture(
        reference, controller, sim_state, on_relax_progress=on_relax_progress
    )
    if native_mv is None:
        raise RuntimeError(
            f"Phase 1 native capture failed: {cap_reason}. "
            f"{_native_capture_fail_hint(cap_reason)}"
        )
    pan_tf = temp_mod.read_fahrenheit()
    reference.save_native(native_mv, native_temp_f=pan_tf)
    if pan_tf is not None:
        log(
            f"Native baseline: {native_mv:.1f} mV (pan {pan_tf:.1f} °F);  "
            f"goal ≥{cfg.TARGET_SHIFT_MV} mV shift → ref ≈{native_mv - cfg.TARGET_SHIFT_MV:.1f} mV under CP"
        )
    else:
        log(
            f"Native baseline: {native_mv:.1f} mV;  "
            f"goal ≥{cfg.TARGET_SHIFT_MV} mV shift → ref ≈{native_mv - cfg.TARGET_SHIFT_MV:.1f} mV under CP"
        )

    _anode_placement_pause(
        "after_phase1",
        anode_placement_prompts=anode_placement_prompts,
        controller=controller,
        reference=reference,
        sim_state=sim_state,
    )

    original_target_ma = float(cfg.TARGET_MA)
    wto = float(getattr(cfg, "COMMISSIONING_WALL_TIMEOUT_S", 0.0) or 0.0)
    comm_deadline: float | None = (time.monotonic() + wto) if wto > 0 else None
    if comm_deadline is not None and verbose:
        log(
            f"Wall limit: {wto:g} s from here (COMMISSIONING_WALL_TIMEOUT_S)"
        )
    if verbose:
        print()
        print_commission_section("Phase 2 — ramp to target shift")
    try:
        _phase2_3_ramp_lock(
            reference,
            controller,
            sim_state,
            log=log,
            verbose=verbose,
            comm_deadline=comm_deadline,
        )
    except Exception:
        cfg.TARGET_MA = original_target_ma
        raise
    return float(cfg.TARGET_MA)


def _phase2_3_ramp_lock(
    reference: ReferenceElectrode,
    controller: Controller,
    sim_state: Any | None,
    *,
    log: Callable[[str], None],
    verbose: bool,
    comm_deadline: float | None,
) -> None:
    """Phase 2 ramp + Phase 3 lock; mutates ``cfg.TARGET_MA`` on success."""
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
        _check_comm_wall_deadline(comm_deadline)
        cfg.TARGET_MA = round(current_target_ma, 3)
        _pump_control(
            controller,
            sim_state,
            RAMP_SETTLE_S,
            reference=reference,
            wall_deadline_mono=comm_deadline,
            commission_log=log if verbose else None,
            progress_label="Phase 2 regulate",
            progress_next="instant-off",
            progress_interval_s=_COMMISSION_PUMP_PROGRESS_S,
        )
        if verbose:
            r_settle = _sensor_readings(sim_state)
            log(
                f"Setpoint {current_target_ma:.3f} mA · {RAMP_SETTLE_S:.0f}s regulate  |  "
                f"{commission_ina_compact(r_settle, num_channels=cfg.NUM_CHANNELS)}"
            )
        raw, shift, _depol = _instant_off_ref_mv_and_restore(
            controller,
            reference,
            sim_state,
            log=log if verbose else None,
            wall_deadline_mono=comm_deadline,
        )
        shift_str = f"{shift:.1f}" if shift is not None else "N/A"
        if verbose:
            log(
                f"Instant-off ({oc_desc})  →  ref@off {raw:.1f} mV, "
                f"shift (native−off) {shift_str} / {cfg.TARGET_SHIFT_MV} mV"
            )

        tol = float(getattr(cfg, "COMMISSIONING_SHIFT_CONFIRM_TOLERANCE", 0.9))
        thr = float(cfg.TARGET_SHIFT_MV)
        if shift is not None and shift >= thr:
            confirm_count += 1
            if verbose:
                log(f"Shift in band — streak {confirm_count}/{CONFIRM_TICKS}")
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
        current_target_ma = float(cfg.MAX_MA)
        cfg.TARGET_MA = round(current_target_ma, 3)
        log(
            "WARNING: reached MAX_MA without achieving target shift — "
            "check bonding, anode contact, and water conductivity."
        )

    _check_comm_wall_deadline(comm_deadline)
    phase3_s = max(
        float(RAMP_SETTLE_S),
        float(getattr(cfg, "COMMISSIONING_PHASE3_LOCK_SETTLE_S", 30.0)),
    )
    if verbose:
        print()
        print_commission_section("Phase 3 — lock, final OC, and save")
    log(
        f"Lock {current_target_ma:.3f} mA/ch — {phase3_s:.0f}s final regulate, then last instant-off"
    )
    _pump_control(
        controller,
        sim_state,
        phase3_s,
        reference=reference,
        wall_deadline_mono=comm_deadline,
        commission_log=log if verbose else None,
        progress_label="Phase 3 (final) regulate",
        progress_next="last instant-off",
        progress_interval_s=_COMMISSION_PUMP_PROGRESS_S,
    )
    if verbose:
        r_lock = _sensor_readings(sim_state)
        log(
            f"After {phase3_s:.0f}s  |  "
            f"{commission_ina_compact(r_lock, num_channels=cfg.NUM_CHANNELS)}"
        )
    _final_raw, final_shift, _f_depol = _instant_off_ref_mv_and_restore(
        controller,
        reference,
        sim_state,
        log=log if verbose else None,
        wall_deadline_mono=comm_deadline,
    )
    # Nested per-channel hints (docs/iccp-requirements.md §8.1 Phase 3). One reference
    # electrode → one system shift; per-channel copy is for schema compatibility only.
    channels_hints: dict[str, dict[str, Any]] = {}
    for ch in range(cfg.NUM_CHANNELS):
        per_ch_target = float(
            getattr(cfg, "CHANNEL_TARGET_MA", {}).get(ch, current_target_ma)
        )
        channels_hints[str(ch)] = {
            "commissioned_target_ma": round(per_ch_target, 3),
            "system_final_shift_mv": final_shift,
            "final_shift_mv": final_shift,
        }
    comm_payload = {
        "commissioned_target_ma": current_target_ma,
        "commissioned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "final_shift_mv": final_shift,
        "channels": channels_hints,
    }
    # Full file replace: no merge with stale keys from an older commissioning.json.
    # Preserve ref_ads_scale if it was set in the file (calibration / manual).
    ref_ads_scale: float | None = None
    if _COMM_FILE.exists():
        try:
            _old = json.loads(_COMM_FILE.read_text(encoding="utf-8"))
            if (
                isinstance(_old, dict)
                and _old.get("ref_ads_scale") is not None
            ):
                ref_ads_scale = float(_old["ref_ads_scale"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    full: dict = {**reference.native_baseline_file_payload(), **comm_payload}
    if ref_ads_scale is not None:
        full["ref_ads_scale"] = ref_ads_scale
    _update_comm_file(full, replace=True)
    log("Done.")


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
            commission_log_main(msg)

    if verbose:
        print()
        print_commission_section("Phase 1 — native only (re-capture baseline)")
    with _phase1_static_gate_context(controller):
        _verify_phase1_drive_off(controller, sim_state, log=log if verbose else None)
    _anode_placement_pause(
        "before_phase1",
        anode_placement_prompts=anode_placement_prompts,
        controller=controller,
        reference=reference,
        sim_state=sim_state,
    )

    def on_relax_progress(remaining: float, n: int) -> None:
        if not verbose:
            return
        commission_log_main(
            f"Reference window: ~{remaining:.0f} s until median; {n} sample(s) so far"
        )

    native_mv, reason = _phase1_spec_native_capture(
        reference, controller, sim_state, on_relax_progress=on_relax_progress
    )

    if native_mv is None:
        log(f"native capture failed: {reason}")
        return None, reason

    pan_tf = temp_mod.read_fahrenheit()
    reference.save_native(native_mv, native_temp_f=pan_tf)
    log(f"native_mv = {native_mv:.2f} mV (reason={reason})")
    return native_mv, reason
