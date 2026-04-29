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
from iccp_electrolyte import cell_impedance_ohm
from channel_labels import anode_hw_label
from console_ui import (
    commission_ina_compact,
    commission_log_main,
    print_commission_section,
)
from cli_events import emit, output_mode
from reference import (
    ReferenceElectrode,
    _update_comm_file,
    find_oc_curve_metrics,
    ref_hw_message,
    ref_instant_legend,
)

if TYPE_CHECKING:
    from control import Controller

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"
# Avoid stderr spam if load_commissioned_target() is called more than once.
_commissioning_schema_warned: set[str] = set()
_legacy_commissioning_complete_flag_warned: bool = False


def _warn_commissioning_json_schema(data: object) -> None:
    """Log once if commissioning.json is missing or older :data:`COMMISSIONING_JSON_SCHEMA_VERSION`."""
    if not isinstance(data, dict):
        return
    exp = int(getattr(cfg, "COMMISSIONING_JSON_SCHEMA_VERSION", 2))
    g = data.get("schema_version")
    g_key = f"missing:{g!r}" if g is None else f"sv:{g!r}"
    if g_key in _commissioning_schema_warned:
        return
    _commissioning_schema_warned.add(g_key)
    if g is None:
        if output_mode() == "jsonl":
            emit(
                {
                    "level": "warn",
                    "cmd": "commission",
                    "source": "commissioning",
                    "event": "commissioning.schema.warn",
                    "msg": "commissioning.json missing schema_version",
                    "data": {"path": str(_COMM_FILE)},
                }
            )
        else:
            print(
                "[commissioning] commissioning.json has no schema_version — re-run `iccp commission` "
                "so v2 baselines and health/ramp metadata are not silently absent.",
                file=sys.stderr,
            )
        return
    try:
        gi = int(g)
    except (TypeError, ValueError):
        return
    if gi < exp:
        if output_mode() == "jsonl":
            emit(
                {
                    "level": "warn",
                    "cmd": "commission",
                    "source": "commissioning",
                    "event": "commissioning.schema.warn",
                    "msg": "commissioning.json schema_version too old",
                    "data": {"path": str(_COMM_FILE), "schema_version": gi, "expected": exp},
                }
            )
        else:
            print(
                f"[commissioning] commissioning.json schema_version={gi} < {exp} — re-commission to "
                "refresh baselines and metadata.",
                file=sys.stderr,
            )


def _commission_oc_debug() -> bool:
    """Set ``ICCP_COMMISSION_DEBUG=1`` to print full OC-curve head/tail samples (very noisy)."""
    return (os.environ.get("ICCP_COMMISSION_DEBUG", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _phase2_active_channel_lines() -> list[str]:
    """
    Log lines for Phase 2: which anode row(s) the control loop will use.

    If ``COILSHIELD_ACTIVE_CHANNELS`` is unset, every logical row 0..NUM_CHANNELS-1 is
    included — same duty on A1..A# as in progress lines. A single-physical-anode
    field install should pass ``iccp commission --anode 1`` (or a 0-based env list)
    so unused gates stay at 0% duty and off the ramp path.
    """
    nch = int(cfg.NUM_CHANNELS)
    chs = cfg.active_channel_indices_list()
    ac = cfg.ACTIVE_CHANNEL_INDICES
    a_str = ", ".join(f"A{c + 1}" for c in chs)
    ch_str = ", ".join(str(c) for c in chs)
    lines: list[str] = [
        f"CP includes {len(chs)} anode row(s) this run: {a_str} (0-based ch {ch_str})."
    ]
    if ac is None and nch > 1:
        lines.append(
            "By default all logical rows are in the control set, so the same duty appears "
            "on every A#. If only one physical anode is installed, restrict with e.g. "
            "`iccp commission --anode 1` (1-based) or `COILSHIELD_ACTIVE_CHANNELS=0` "
            "(comma-separated 0-based indices) so other gates stay at 0%."
        )
    return lines


# Skip-hint to stderr while blocking on anode Enter.
_TTY_ANODE_SKIP_HINT_S: float = 75.0
# Passive status line while waiting for Enter (no controller.update — gates must stay off).
_COMMISSION_ANODE_WAIT_STATUS_S: float = 5.0
# Commission-only regulate / settle: time-remaining to instant-off.
_COMMISSION_PUMP_PROGRESS_S: float = 20.0


def _commission_anode_wait_line(
    controller: Any,
    reference: ReferenceElectrode,
    sim_state: Any | None,
) -> tuple[tuple[Any, ...], str]:
    """Build dedup key and one status line — read-only (does not run the control tick)."""
    import sensors

    if sensors.SIM_MODE and sim_state is not None:
        readings = sensors.read_all_sim(sim_state)
    else:
        readings = sensors.read_all_real()
    duties = {
        ch: float(controller.output_duty_pct(ch)) for ch in range(cfg.NUM_CHANNELS)
    }
    temp_f = temp_mod.read_fahrenheit()
    ref_mv = float(reference.read(temp_f=temp_f))
    chs = cfg.active_channel_indices_list()
    ina = commission_ina_compact(
        readings, num_channels=cfg.NUM_CHANNELS, channels=chs
    )
    duty_s = " ".join(
        f"A{ch + 1}={float(duties[ch]):.2f}%" for ch in chs
    )
    ts = time.strftime("%H:%M:%S")
    t_s = f"{temp_f:.1f}°F" if temp_f is not None else "—"
    line = (
        f"[commission] {ts}  (anode wait, read-only)  {ina}  |  {duty_s}  |  "
        f"ref(raw)={ref_mv:.1f} mV  |  T={t_s}"
    )
    key_parts: list[Any] = [round(ref_mv, 1)]
    for ch in chs:
        r = readings.get(ch, {})
        if r.get("ok"):
            key_parts.append(round(float(r.get("current", 0.0) or 0.0), 3))
        else:
            err = (r.get("sensor_error") or r.get("error") or "unknown")[:20]
            key_parts.append(f"!{err}")
    key_parts.extend(round(duties[ch], 1) for ch in chs)
    key_parts.append(
        None if temp_f is None else round(float(temp_f), 1)
    )
    return tuple(key_parts), line


def _print_commission_anode_wait_line(
    controller: Any,
    reference: ReferenceElectrode,
    sim_state: Any | None,
) -> None:
    """INA + PWM% + ref raw — read-only (does not run the ICCP control tick)."""
    _, line = _commission_anode_wait_line(controller, reference, sim_state)
    if output_mode() == "jsonl":
        emit(
            {
                "level": "info",
                "cmd": "commission",
                "source": "commissioning",
                "event": "commission.prompt.status",
                "msg": "anode-wait status",
                "data": {"line": line},
            }
        )
    else:
        print(line)


def _commission_prompts_enabled() -> bool:
    """
    Interactive commissioning prompts (Enter to continue) when stdin is a TTY.

    Disable for unattended runs: ``iccp commission --no-prompts`` or
    ``ICCP_COMMISSION_SKIP_PROMPTS=1``.
    """
    skip = (os.environ.get("ICCP_COMMISSION_SKIP_PROMPTS") or "").strip().lower()
    if skip in ("1", "true", "yes", "on"):
        return False
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


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
    poll = min(0.5, max(0.05, float(getattr(cfg, "SAMPLE_INTERVAL_S", 0.5))))
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
                    "[main] To skip anode pauses: COMMISSIONING_FIELD_MODE / "
                    "ICCP_COMMISSION_FIELD_MODE=1, or ICCP_COMMISSION_NO_ANODE_PROMPTS=1, or "
                    "iccp commission --no-anode-prompts",
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


def _commissioning_field_mode() -> bool:
    """Permanent anodes / no bench 1a+1b split — see ``COMMISSIONING_FIELD_MODE``."""
    raw = (os.environ.get("ICCP_COMMISSION_FIELD_MODE", "") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return bool(getattr(cfg, "COMMISSIONING_FIELD_MODE", False))


def _anode_placement_should_interact(
    anode_placement_prompts: bool | None,
) -> bool:
    """True when we should block on operator Enter (removed / installed anodes)."""
    import sensors

    if not _commission_prompts_enabled():
        return False
    if anode_placement_prompts is False:
        return False
    if _commissioning_field_mode():
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


def _galvanic_1b_wanted() -> bool:
    """Second OCP capture (anodes in bath, gates off) after Phase 1a, same T_RELAX."""
    if _commissioning_field_mode():
        return False
    if (os.environ.get("ICCP_SKIP_GALVANIC_1B", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    return bool(getattr(cfg, "COMMISSIONING_GALVANIC_1B_ENABLED", True))


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
        print_commission_section("Anode placement — Phase 1b then ramp (Phases 2–3)")
        print(
            "[main] Install anodes in the bath, then press Enter for the second open-circuit "
            "reference (Phase 1b, MOSFETs off), then commissioning continues to the current ramp."
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
        print(
            f"[main] While waiting: read-only INA/ref at most every "
            f"{int(_COMMISSION_ANODE_WAIT_STATUS_S)} s when readings change (outputs off; "
            "no control tick; repeated identical lines are skipped)."
        )
    on_timeout: Callable[[], None] | None = None
    if controller is not None and reference is not None:
        _last_status_mono = 0.0
        _last_wait_key: tuple[Any, ...] | None = None

        def _on_timeout() -> None:
            nonlocal _last_status_mono, _last_wait_key
            now = time.monotonic()
            if now - _last_status_mono < float(_COMMISSION_ANODE_WAIT_STATUS_S):
                return
            _last_status_mono = now
            key, line = _commission_anode_wait_line(controller, reference, sim_state)
            if key == _last_wait_key:
                return
            _last_wait_key = key
            if output_mode() == "jsonl":
                emit(
                    {
                        "level": "info",
                        "cmd": "commission",
                        "source": "commissioning",
                        "event": "commission.prompt.status",
                        "msg": "anode-wait status",
                        "data": {"line": line},
                    }
                )
            else:
                print(line)

        on_timeout = _on_timeout
    if output_mode() == "jsonl":
        emit(
            {
                "level": "info",
                "cmd": "commission",
                "source": "commissioning",
                "event": "commission.prompt.waiting",
                "msg": f"waiting for operator ({step})",
                "step": step,
                "data": {"step": step, "next": next_on_enter},
            }
        )
    _readline_wait_enter_for_anode_prompt(on_select_timeout=on_timeout)
    if output_mode() == "jsonl":
        emit(
            {
                "level": "info",
                "cmd": "commission",
                "source": "commissioning",
                "event": "commission.prompt.received",
                "msg": f"operator acknowledged ({step})",
                "step": step,
                "data": {"step": step},
            }
        )


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
        if output_mode() == "jsonl":
            emit(
                {
                    "level": "warn",
                    "cmd": "commission",
                    "source": "commissioning",
                    "event": "commissioning.json.invalid",
                    "msg": "invalid commissioning.json; treating as needs commissioning",
                    "data": {"path": str(_COMM_FILE)},
                    "err": {"type": type(e).__name__, "message": str(e)},
                }
            )
        else:
            print(
                f"[commissioning] invalid commissioning.json (treat as needs commissioning): {e}",
                file=sys.stderr,
            )
        return True
    except OSError as e:
        if output_mode() == "jsonl":
            emit(
                {
                    "level": "warn",
                    "cmd": "commission",
                    "source": "commissioning",
                    "event": "commissioning.json.read_failed",
                    "msg": "cannot read commissioning.json; treating as needs commissioning",
                    "data": {"path": str(_COMM_FILE)},
                    "err": {"type": type(e).__name__, "message": str(e)},
                }
            )
        else:
            print(
                f"[commissioning] cannot read commissioning.json: {e}",
                file=sys.stderr,
            )
        return True
    if "native_mv" not in data:
        return True
    if "commissioned_target_ma" not in data:
        return True
    cc = data.get("commissioning_complete")
    if cc is False:
        return True
    if cc is True:
        return False
    # Legacy files (before commissioning_complete): treat as complete if we have
    # both native and ramp target; warn once on stderr.
    global _legacy_commissioning_complete_flag_warned
    if not _legacy_commissioning_complete_flag_warned:
        if output_mode() == "jsonl":
            emit(
                {
                    "level": "warn",
                    "cmd": "commission",
                    "source": "commissioning",
                    "event": "commissioning.json.legacy_complete_missing",
                    "msg": "commissioning_complete key missing; assuming full commissioning finished",
                    "data": {"path": str(_COMM_FILE)},
                }
            )
        else:
            print(
                "[commissioning] commissioning.json has no commissioning_complete key — "
                "assuming full commissioning finished (add key or re-run `iccp commission`).",
                file=sys.stderr,
            )
        _legacy_commissioning_complete_flag_warned = True
    return False


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
        data = json.loads(_COMM_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(
            f"[commissioning] invalid commissioning.json (commissioned_target_ma fallback): {e}",
            file=sys.stderr,
        )
        return cfg.TARGET_MA
    except OSError as e:
        print(
            f"[commissioning] commissioned_target_ma read error: {e}",
            file=sys.stderr,
        )
        return cfg.TARGET_MA
    _warn_commissioning_json_schema(data)
    try:
        if isinstance(data, dict):
            return float(data.get("commissioned_target_ma", cfg.TARGET_MA))
    except (TypeError, ValueError) as e:
        print(
            f"[commissioning] commissioned_target_ma read error: {e}",
            file=sys.stderr,
        )
        return cfg.TARGET_MA
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
    if output_mode() == "jsonl":
        emit(
            {
                "level": "warn",
                "cmd": "commission",
                "source": "commissioning",
                "event": "commission.oc_confirm.timeout",
                "msg": "INA219 OC confirm timeout",
                "data": {
                    "mode": mode,
                    "reasons": list(last_reasons),
                    "channels": [
                        {
                            "ch": ch,
                            "label": anode_hw_label(ch),
                            "ok": (last_readings.get(ch, {}) or {}).get("ok"),
                            "current_ma": (last_readings.get(ch, {}) or {}).get("current"),
                            "bus_v": (last_readings.get(ch, {}) or {}).get("bus_v"),
                            "error": (last_readings.get(ch, {}) or {}).get("error"),
                        }
                        for ch in range(cfg.NUM_CHANNELS)
                    ],
                    "confirm_i_ma": getattr(cfg, "COMMISSIONING_OC_CONFIRM_I_MA", 0.15),
                    "ocbus_max_delta_v": getattr(cfg, "COMMISSIONING_OCBUS_MAX_DELTA_V", 0.05),
                },
            }
        )
    else:
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


def _phase1_off_check_scope_phrase() -> str:
    """
    Log wording: full install vs. commission anode subset (off-check still scans all hardware).
    """
    nch = int(getattr(cfg, "NUM_CHANNELS", 4))
    active = cfg.active_channel_indices_list()
    if len(active) == nch:
        return f"all {nch} anodes"
    sub = ", ".join(f"A{ch + 1}" for ch in active)
    return f"all {nch} hardware anodes (commission subset: {sub})"


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
        scope = _phase1_off_check_scope_phrase()
        if post_long_settle:
            ok_msg = (
                f"Phase 1 off-check after settle: {scope} PWM 0% and |I| < {i_gate:g} mA "
                "(gates closed, no CP drive through shunts)."
            )
        else:
            ok_msg = (
                f"Phase 1 off-check: {scope} PWM 0% and |I| < {i_gate:g} mA "
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
    bl = reference.baseline_mv_for_shift()
    if bl is not None:
        shift = round(float(raw_inst) - float(bl), 2)
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


def _pump_regulate_anode_snapshot(
    controller: Any,
    readings: dict[int, dict],
) -> str:
    """One-line shunt mA + PWM% per anode for commissioning progress (Phase 2/3)."""
    chs = cfg.active_channel_indices_list()
    ina = commission_ina_compact(
        readings,
        num_channels=cfg.NUM_CHANNELS,
        channels=chs,
        mark_highest_shunt=True,
    )
    duties = controller.duties()
    duty_s = " ".join(
        f"A{ch + 1}={float(duties.get(ch, 0.0)):.2f}%" for ch in chs
    )
    return f"{ina}  |  {duty_s}"


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
    anode_progress_detail: bool = False,
) -> None:
    """Run the normal control loop for duration_s (settle / ramp).

    If ``commission_log`` is set (commissioning UI only), log **time remaining** until
    ``progress_next`` at ``progress_interval_s`` (monotonic wall, not tick drift).
    When ``anode_progress_detail`` is True, each progress line also includes
    ``ref=`` / ``ref(Δ)=… mV`` (ADS1115 single-ended vs differential; when
    ``reference`` is not None) plus per-anode shunt mA and duty (after
    ``controller.update`` for that tick).
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
        _check_comm_wall_deadline(wall_deadline_mono)
        readings = _sensor_readings(sim_state)
        controller.update(readings)
        if sim_state is not None:
            sim_state.duties = controller.duties()
        ref_raw_for_progress: float | None = None
        if reference is not None:
            tf = temp_mod.read_fahrenheit()
            duties = controller.duties()
            st = controller.channel_statuses()
            _raw, ref_shift = reference.read_raw_and_shift(
                duties=duties, statuses=st, temp_f=tf
            )
            ref_raw_for_progress = _raw
            # Do not call advance_shift_fsm() here: it can drive PWM back to 0% every tick
            # (e.g. STATE_V2_OVERPROTECTED duty ramp-down) while Phase 2/3 are trying to
            # regulate toward cfg.TARGET_MA. Runtime iccp start still runs update + shift FSM.
        if need_progress and cl is not None:
            now = time.monotonic()
            if now - last_progress_announce >= float(progress_interval_s):
                rem = max(0.0, t_end - now)
                rsec = int(rem)
                mm, ss = divmod(rsec, 60)
                extra = ""
                if anode_progress_detail:
                    if reference is not None:
                        extra = (
                            f"  |  {ref_instant_legend()}="
                            f"{ref_raw_for_progress:.1f} mV"
                        )
                    extra += f"  |  {_pump_regulate_anode_snapshot(controller, readings)}"
                cl(
                    f"{progress_label} — ~{mm:d}:{ss:02d} until {progress_next}{extra}"
                )
                last_progress_announce = now
        time.sleep(cfg.SAMPLE_INTERVAL_S)


def _phase1_spec_native_capture(
    reference: ReferenceElectrode,
    controller: Any,
    sim_state: Any | None,
    *,
    on_relax_progress: Callable[[float, int, float | None], None] | None = None,
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
            if output_mode() == "jsonl":
                emit(
                    {
                        "level": "warn",
                        "cmd": "commission",
                        "source": "commissioning",
                        "event": "commission.static_gate_low.failed",
                        "msg": "enter_static_gate_off failed",
                        "err": {"type": type(e).__name__, "message": str(e)},
                    }
                )
            else:
                print(f"[commission] static_gate_low: {e}", file=sys.stderr)

    def _restore() -> None:
        try:
            controller.leave_static_gate_off()
        except Exception as e:  # pragma: no cover
            if output_mode() == "jsonl":
                emit(
                    {
                        "level": "warn",
                        "cmd": "commission",
                        "source": "commissioning",
                        "event": "commission.static_gate_restore.failed",
                        "msg": "leave_static_gate_off failed",
                        "err": {"type": type(e).__name__, "message": str(e)},
                    }
                )
            else:
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

    # Phase 1 OCP and static gate expect true 0% command; init may be at DUTY_PROBE from PWMBank.
    controller.all_outputs_off()

    if verbose and _commissioning_field_mode():
        log(
            "Field mode: anodes stay on the coil; no remove/install prompts. "
            "Single OCP baseline only."
        )

    if verbose:
        if output_mode() != "jsonl":
            print()
        if _commissioning_field_mode():
            print_commission_section(
                "Phase 1 — native Ecorr (field: anodes installed, MOSFETs off)"
            )
        else:
            print_commission_section(
                "Phase 1 — native Ecorr (open-circuit, spec capture)"
            )
    with _phase1_static_gate_context(controller):
        _verify_phase1_drive_off(controller, sim_state, log=log if verbose else None)
        _anode_placement_pause(
            "before_phase1",
            anode_placement_prompts=anode_placement_prompts,
            controller=controller,
            reference=reference,
            sim_state=sim_state,
        )

    def on_relax_progress(
        remaining: float, n: int, last_read_mv: float | None = None
    ) -> None:
        if not verbose:
            return
        mpart = (
            f"  ref(raw)={last_read_mv:.1f} mV" if last_read_mv is not None else ""
        )
        commission_log_main(
            f"Reference window: ~{remaining:.0f} s until median; {n} sample(s) so far{mpart}"
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
    if _commissioning_field_mode():
        if pan_tf is not None:
            log(
                f"Phase 1 — field native (single OCP baseline): {native_mv:.1f} mV "
                f"(pan {pan_tf:.1f} °F)"
            )
        else:
            log(
                f"Phase 1 — field native (single OCP baseline): {native_mv:.1f} mV"
            )
    elif pan_tf is not None:
        log(
            f"Phase 1a — true native (anodes out): {native_mv:.1f} mV (pan {pan_tf:.1f} °F)"
        )
    else:
        log(f"Phase 1a — true native (anodes out): {native_mv:.1f} mV")

    if _galvanic_1b_wanted():
        with _phase1_static_gate_context(controller):
            _anode_placement_pause(
                "after_phase1",
                anode_placement_prompts=anode_placement_prompts,
                controller=controller,
                reference=reference,
                sim_state=sim_state,
            )
        if verbose:
            if output_mode() != "jsonl":
                print()
            print_commission_section("Phase 1b — OCP with anodes in bath (MOSFETs off)")
        native_in, cap2 = _phase1_spec_native_capture(
            reference,
            controller,
            sim_state,
            on_relax_progress=on_relax_progress,
        )
        if native_in is None:
            raise RuntimeError(
                f"Phase 1b (anodes in, open-circuit) failed: {cap2}. "
                f"{_native_capture_fail_hint(cap2)}  "
                f"To skip: ICCP_SKIP_GALVANIC_1B=1 or set COMMISSIONING_GALVANIC_1B_ENABLED=False."
            )
        assert reference.native_mv is not None
        reference.save_native_oc_anodes_in(native_in, true_native_mv=float(reference.native_mv))
        off = reference.galvanic_offset_mv
        if verbose and off is not None:
            log(
                f"Phase 1b: OCP with anodes in: {native_in:.1f} mV;  "
                f"galvanic offset (1a−1b) = {off:.1f} mV"
            )
    bl0 = reference.baseline_mv_for_shift()
    if bl0 is not None and verbose:
        eff = reference.effective_shift_target_mv()
        ttot = float(cfg.TARGET_SHIFT_MV)
        off = reference.galvanic_offset_mv
        n1a = reference.native_mv
        if off is not None and n1a is not None:
            log(
                f"Shift baseline: {bl0:.1f} mV (1b OCP). Total goal from true native(1a) = "
                f"{ttot:.0f} mV; galvanic(1a−1b) = {off:.1f} mV → target additional shift from "
                f"1b = {eff:.1f} mV; instant-off ref under CP ≈ {bl0 - eff:.1f} mV "
                f"(≈ {n1a - ttot:.1f} mV vs 1a)"
            )
        else:
            log(
                f"Shift baseline for control: {bl0:.1f} mV;  goal ≥{ttot:.0f} mV shift → "
                f"instant-off ref ≈{bl0 - eff:.1f} mV under CP"
            )

    original_target_ma = float(cfg.TARGET_MA)
    wto = float(getattr(cfg, "COMMISSIONING_WALL_TIMEOUT_S", 0.0) or 0.0)
    comm_deadline: float | None = (time.monotonic() + wto) if wto > 0 else None
    if comm_deadline is not None and verbose:
        log(
            f"Wall limit: {wto:g} s from here (COMMISSIONING_WALL_TIMEOUT_S)"
        )
    if verbose:
        if output_mode() != "jsonl":
            print()
        print_commission_section("Phase 2 — ramp to target shift")
        for _line in _phase2_active_channel_lines():
            log(_line)
        log(
            "Shunt mA: A# = firmware row (A1=ch0 = first INA+GPIO in config)."
        )
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


def _commission_shift_band_mv(reference: ReferenceElectrode) -> tuple[float, float]:
    """Additional shift (mV) window vs 1b baseline: same as runtime ``protection_status`` OK band."""
    thr_lo = float(reference.effective_shift_target_mv())
    thr_hi = float(reference.effective_max_shift_mv())
    if thr_hi < thr_lo:
        thr_hi = thr_lo
    return thr_lo, thr_hi


def _shift_in_commission_window(
    shift: float | None, thr_lo: float, thr_hi: float
) -> bool:
    return shift is not None and thr_lo <= float(shift) <= thr_hi


def _phase2_binary_search_mA(
    reference: ReferenceElectrode,
    controller: Controller,
    sim_state: Any | None,
    log: Callable[[str], None],
    verbose: bool,
    comm_deadline: float | None,
    oc_desc: str,
) -> tuple[float, list[dict[str, Any]], bool]:
    """Bisect mA until some trial lands shift in ``[effective_shift_target, effective_max]``.

    Returns ``(mA, history, reached_in_band)`` where *reached_in_band* is True if some trial
    hit that window (used by hybrid to decide linear fallback vs refine-from-*out*).
    """
    lo = float(
        getattr(
            cfg,
            "COMMISSIONING_BINARY_MA_LO",
            max(1e-4, float(cfg.INA219_CURRENT_LSB_MA)),
        )
    )
    hi = float(min(float(cfg.MAX_MA), float(getattr(cfg, "MAX_MA", 5.0))))
    res = max(1e-4, float(getattr(cfg, "COMMISSIONING_BINARY_RESOLUTION_MA", 0.01)))
    max_iter = max(1, int(getattr(cfg, "COMMISSIONING_BINARY_MAX_ITERATIONS", 12)))
    history: list[dict[str, Any]] = []
    best_ma: float | None = None
    thr_lo, thr_hi = _commission_shift_band_mv(reference)
    for _it in range(max_iter):
        _check_comm_wall_deadline(comm_deadline)
        if hi - lo < res:
            break
        mid = round((lo + hi) / 2.0, 3)
        cfg.TARGET_MA = mid
        _pump_control(
            controller,
            sim_state,
            RAMP_SETTLE_S,
            reference=reference,
            wall_deadline_mono=comm_deadline,
            commission_log=log if verbose else None,
            progress_label="Phase 2 binary",
            progress_next="instant-off",
            progress_interval_s=_COMMISSION_PUMP_PROGRESS_S,
            anode_progress_detail=True,
        )
        if verbose:
            r_settle = _sensor_readings(sim_state)
            log(
                f"Binary try {mid:.3f} mA · {RAMP_SETTLE_S:.0f}s regulate  |  "
                f"{commission_ina_compact(r_settle, num_channels=cfg.NUM_CHANNELS, channels=cfg.active_channel_indices_list(), mark_highest_shunt=True)}"
            )
        raw, shift, depol = _instant_off_ref_mv_and_restore(
            controller,
            reference,
            sim_state,
            log=log if verbose else None,
            wall_deadline_mono=comm_deadline,
        )
        history.append(
            {
                "ma": mid,
                "shift_mv": shift,
                "depol_rate_mv_s": depol,
                "ref_raw_mv": raw,
                "mode": "binary",
            }
        )
        if verbose:
            shift_str = f"{shift:.1f}" if shift is not None else "N/A"
            ttot = float(cfg.TARGET_SHIFT_MV)
            band = f"{thr_lo:.1f}…{thr_hi:.1f} mV additional"
            if reference.galvanic_offset_mv is not None:
                log(
                    f"Instant-off ({oc_desc})  →  ref@off {raw:.1f} mV, "
                    f"shift (ref@off−1b baseline) {shift_str}  (window {band}, "
                    f"{ttot:.0f} mV total from 1a)  [binary]"
                )
            else:
                log(
                    f"Instant-off ({oc_desc})  →  ref@off {raw:.1f} mV, "
                    f"shift (ref@off−native) {shift_str}  (window {band})  [binary]"
                )
        if _shift_in_commission_window(shift, thr_lo, thr_hi):
            best_ma = mid
            hi = mid
        elif shift is not None and float(shift) > thr_hi:
            hi = mid
        else:
            lo = mid
    out = float(best_ma) if best_ma is not None else round((lo + hi) / 2.0, 3)
    if best_ma is None and verbose:
        log(
            "WARNING: binary search did not land shift inside the "
            f"{thr_lo:.1f}…{thr_hi:.1f} mV window — "
            f"using fallback {out:.3f} mA; check bonding and water."
        )
    reached = best_ma is not None
    cfg.TARGET_MA = round(out, 3)
    return out, history, reached


def _phase2_linear_ramp_mA(
    reference: ReferenceElectrode,
    controller: Controller,
    sim_state: Any | None,
    log: Callable[[str], None],
    verbose: bool,
    comm_deadline: float | None,
    oc_desc: str,
    *,
    start_ma: float | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Step mA until shift confirms (or MAX_MA). If *start_mA* is None, start from 0.1×TARGET / 0.05 mA."""
    history: list[dict[str, Any]] = []
    thr_lo, thr_hi = _commission_shift_band_mv(reference)
    ma_floor = float(
        getattr(
            cfg,
            "COMMISSIONING_BINARY_MA_LO",
            max(1e-4, float(cfg.INA219_CURRENT_LSB_MA)),
        )
    )
    ramp_coarse = float(getattr(cfg, "COMMISSIONING_RAMP_STEP_MA", 0.15))
    ramp_fine = float(getattr(cfg, "COMMISSIONING_RAMP_FINE_STEP_MA", 0.05))
    near_frac = float(getattr(cfg, "COMMISSIONING_RAMP_FINE_NEAR_SHIFT_FRAC", 0.5))
    tol = float(getattr(cfg, "COMMISSIONING_SHIFT_CONFIRM_TOLERANCE", 0.9))
    if start_ma is None:
        current_target_ma = max(cfg.TARGET_MA * 0.1, 0.05)
    else:
        current_target_ma = float(start_ma)
    confirm_count = 0
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
            anode_progress_detail=True,
        )
        if verbose:
            r_settle = _sensor_readings(sim_state)
            log(
                f"Setpoint {current_target_ma:.3f} mA · {RAMP_SETTLE_S:.0f}s regulate  |  "
                f"{commission_ina_compact(r_settle, num_channels=cfg.NUM_CHANNELS, channels=cfg.active_channel_indices_list(), mark_highest_shunt=True)}"
            )
        raw, shift, _depol = _instant_off_ref_mv_and_restore(
            controller,
            reference,
            sim_state,
            log=log if verbose else None,
            wall_deadline_mono=comm_deadline,
        )
        history.append(
            {
                "ma": round(current_target_ma, 3),
                "shift_mv": shift,
                "depol_rate_mv_s": _depol,
                "ref_raw_mv": raw,
                "mode": "linear",
            }
        )
        shift_str = f"{shift:.1f}" if shift is not None else "N/A"
        ttot = float(cfg.TARGET_SHIFT_MV)
        band = f"{thr_lo:.1f}…{thr_hi:.1f} mV additional"
        if verbose:
            if reference.galvanic_offset_mv is not None:
                log(
                    f"Instant-off ({oc_desc})  →  ref@off {raw:.1f} mV, "
                    f"shift (ref@off−1b baseline) {shift_str}  (window {band}, "
                    f"{ttot:.0f} mV total from 1a)"
                )
            else:
                log(
                    f"Instant-off ({oc_desc})  →  ref@off {raw:.1f} mV, "
                    f"shift (ref@off−native) {shift_str}  (window {band})"
                )

        if _shift_in_commission_window(shift, thr_lo, thr_hi):
            confirm_count += 1
            if verbose:
                log(f"Shift in window — streak {confirm_count}/{CONFIRM_TICKS}")
            if confirm_count >= CONFIRM_TICKS:
                break
        elif shift is not None and float(shift) > thr_hi:
            confirm_count = max(0, confirm_count - 1)
            excess = float(shift) - thr_hi
            step_down = ramp_fine if excess < 20.0 else ramp_coarse
            if current_target_ma <= ma_floor + 1e-9:
                if verbose:
                    log(
                        "WARNING: shift exceeds window ceiling but setpoint is already at "
                        f"minimum ({ma_floor:g} mA) — cannot reduce further; stopping Phase 2 ramp."
                    )
                break
            current_target_ma = round(max(ma_floor, current_target_ma - step_down), 3)
            if verbose:
                log(
                    f"Shift {float(shift):.1f} mV above ceiling {thr_hi:.1f} mV — "
                    f"reducing setpoint to {current_target_ma:.3f} mA"
                )
        elif (
            shift is not None
            and thr_lo * tol <= float(shift) < thr_lo
        ):
            pass
        else:
            # Decay streak on bad samples (no hard reset) — noisy tap water.
            confirm_count = max(0, confirm_count - 1)
            step = (
                ramp_fine
                if shift is not None and float(shift) > thr_lo * near_frac
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
    return current_target_ma, history


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
    ramp_search_history: list[dict[str, Any]] = []
    mode = str(getattr(cfg, "COMMISSIONING_RAMP_MODE", "linear") or "linear").lower()
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

    if mode == "binary":
        out, h, _reached = _phase2_binary_search_mA(
            reference,
            controller,
            sim_state,
            log,
            verbose,
            comm_deadline,
            oc_desc,
        )
        current_target_ma = out
        ramp_search_history = h
    elif mode == "hybrid":
        out, h_bin, reached = _phase2_binary_search_mA(
            reference,
            controller,
            sim_state,
            log,
            verbose,
            comm_deadline,
            oc_desc,
        )
        ramp_search_history = list(h_bin)
        if verbose and not reached:
            log(
                "Phase 2 (hybrid): binary search did not end in-band — "
                "running full linear ramp from minimum setpoint."
            )
        elif verbose and reached:
            log(
                f"Phase 2 (hybrid): linear confirm streak from {out:.3f} mA "
                "(after binary search)."
            )
        start: float | None = out if reached else None
        cur, h_lin = _phase2_linear_ramp_mA(
            reference,
            controller,
            sim_state,
            log,
            verbose,
            comm_deadline,
            oc_desc,
            start_ma=start,
        )
        current_target_ma = cur
        ramp_search_history.extend(h_lin)
    else:
        current_target_ma, ramp_search_history = _phase2_linear_ramp_mA(
            reference,
            controller,
            sim_state,
            log,
            verbose,
            comm_deadline,
            oc_desc,
            start_ma=None,
        )

    _check_comm_wall_deadline(comm_deadline)
    phase3_s = max(
        float(RAMP_SETTLE_S),
        float(getattr(cfg, "COMMISSIONING_PHASE3_LOCK_SETTLE_S", 30.0)),
    )
    if verbose:
        if output_mode() != "jsonl":
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
            f"{commission_ina_compact(r_lock, num_channels=cfg.NUM_CHANNELS, channels=cfg.active_channel_indices_list(), mark_highest_shunt=True)}"
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
    r_z = _sensor_readings(sim_state)
    channels_hints: dict[str, dict[str, Any]] = {}
    for ch in range(cfg.NUM_CHANNELS):
        per_ch_target = float(
            getattr(cfg, "CHANNEL_TARGET_MA", {}).get(ch, current_target_ma)
        )
        ent: dict[str, Any] = {
            "commissioned_target_ma": round(per_ch_target, 3),
            "system_final_shift_mv": final_shift,
            "final_shift_mv": final_shift,
        }
        row = r_z.get(ch) or {}
        if row.get("ok"):
            try:
                ent["z_baseline_ohm"] = cell_impedance_ohm(
                    float(row["bus_v"]), float(row["current"])
                )
            except (TypeError, ValueError, KeyError):
                pass
        channels_hints[str(ch)] = ent
    comm_payload: dict[str, Any] = {
        "schema_version": int(
            getattr(cfg, "COMMISSIONING_JSON_SCHEMA_VERSION", 2)
        ),
        "commissioning_complete": True,
        "commissioned_target_ma": current_target_ma,
        "commissioned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "final_shift_mv": final_shift,
        "channels": channels_hints,
        "depol_baseline_mv_s": (
            None
            if _f_depol is None
            else round(float(_f_depol), 6)
        ),
    }
    if ramp_search_history:
        comm_payload["ramp_search_history"] = ramp_search_history
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
    """Phase 1 only: re-capture baselines without ramp/lock phases.

    Runs one ``capture_native`` (Phase 1a on bench, or the single field baseline when
    :func:`_commissioning_field_mode` is True) and, when :func:`_galvanic_1b_wanted` is
    True, Phase 1b (second OCP, anodes in bath). Persists ``native_mv``, optional
    ``native_oc_anodes_in_mv`` / ``galvanic_offset_mv`` / ``galvanic_offset_baseline_mv``.
    Returns ``(native_mv, reason)``; reason is ``"ok"`` when the selected phases succeed.
    """

    def log(msg: str) -> None:
        if verbose:
            commission_log_main(msg)

    controller.all_outputs_off()

    if verbose:
        if output_mode() != "jsonl":
            print()
        if _commissioning_field_mode():
            print_commission_section(
                "Phase 1 — native only (field: single OCP baseline)"
            )
        else:
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

    def on_relax_progress(
        remaining: float, n: int, last_read_mv: float | None = None
    ) -> None:
        if not verbose:
            return
        mpart = (
            f"  ref(raw)={last_read_mv:.1f} mV" if last_read_mv is not None else ""
        )
        commission_log_main(
            f"Reference window: ~{remaining:.0f} s until median; {n} sample(s) so far{mpart}"
        )

    native_mv, reason = _phase1_spec_native_capture(
        reference, controller, sim_state, on_relax_progress=on_relax_progress
    )

    if native_mv is None:
        log(f"native capture failed: {reason}")
        return None, reason

    pan_tf = temp_mod.read_fahrenheit()
    reference.save_native(native_mv, native_temp_f=pan_tf)
    if _commissioning_field_mode():
        log(f"Phase 1 field native_mv = {native_mv:.2f} mV (reason={reason})")
    else:
        log(f"Phase 1a native_mv = {native_mv:.2f} mV (reason={reason})")

    if _galvanic_1b_wanted():
        with _phase1_static_gate_context(controller):
            _anode_placement_pause(
                "after_phase1",
                anode_placement_prompts=anode_placement_prompts,
                controller=controller,
                reference=reference,
                sim_state=sim_state,
            )
        if verbose:
            if output_mode() != "jsonl":
                print()
            print_commission_section("Phase 1b — OCP with anodes in bath (MOSFETs off)")
        native_in, r2 = _phase1_spec_native_capture(
            reference, controller, sim_state, on_relax_progress=on_relax_progress
        )
        if native_in is None:
            return None, f"phase1b failed: {r2}"
        assert reference.native_mv is not None
        reference.save_native_oc_anodes_in(
            native_in, true_native_mv=float(reference.native_mv)
        )
        if verbose and reference.galvanic_offset_mv is not None:
            log(
                f"Phase 1b: {native_in:.2f} mV; galvanic offset = {reference.galvanic_offset_mv:.2f} mV"
            )
    if not _galvanic_1b_wanted():
        return native_mv, reason
    if reference.native_oc_anodes_in_mv is None:  # pragma: no cover
        return None, "phase1b_not_saved"
    return native_mv, "ok"
