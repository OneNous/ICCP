"""
CoilShield — self-commissioning (native potential + current ramp).

Reset: delete commissioning.json or call commissioning.reset().
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import config.settings as cfg
from reference import ReferenceElectrode, _update_comm_file

if TYPE_CHECKING:
    from control import Controller

_COMM_FILE = cfg.PROJECT_ROOT / "commissioning.json"

COMMISSIONING_SETTLE_S: int = getattr(cfg, "COMMISSIONING_SETTLE_S", 60)
TARGET_RAMP_STEP_MA: float = float(getattr(cfg, "COMMISSIONING_RAMP_STEP_MA", 0.15))
RAMP_SETTLE_S: float = float(getattr(cfg, "COMMISSIONING_RAMP_SETTLE_S", 60.0))
CONFIRM_TICKS: int = 5
# Seconds at 0% PWM before reference ADC read (open-circuit / IR decay); tune in settings.
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


def _instant_off_ref_mv_and_restore(
    controller: Controller,
    reference: ReferenceElectrode,
    sim_state: Any | None,
) -> tuple[float, float | None]:
    """
    Snapshot PWM duties, all channels off, dwell at OC, read reference, restore exact
    duties via set_duty, then one controller.update() tick (sim duties synced first).

    The next FSM step still uses restored _pwm.duty(ch) as current_duty (control.py);
    OPEN channels already had saved duty 0.
    Returns (raw_mv_at_instant, shift_mv) where shift = native_mv − raw.
    """
    saved_duties = {
        ch: float(controller._pwm.duty(ch)) for ch in range(cfg.NUM_CHANNELS)
    }
    controller._pwm.all_off()
    time.sleep(INSTANT_OFF_WINDOW_S)
    raw_inst = float(reference.read())
    shift: float | None = None
    if reference.native_mv is not None:
        shift = round(float(reference.native_mv) - raw_inst, 2)
    for ch, duty in saved_duties.items():
        controller._pwm.set_duty(ch, duty)
    if sim_state is not None:
        sim_state.duties = dict(saved_duties)
    readings = _sensor_readings(sim_state)
    controller.update(readings)
    if sim_state is not None:
        sim_state.duties = controller.duties()
    return raw_inst, shift


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

    # Phase 1 — native potential
    log("Phase 1 — measuring native corrosion potential")
    controller._pwm.all_off()
    log(f"Channels off. Settling {COMMISSIONING_SETTLE_S}s ...")
    _pump_control(controller, sim_state, float(COMMISSIONING_SETTLE_S))

    log("Averaging 30 reference (INA219) samples ...")
    samples: list[float] = []
    for _ in range(30):
        readings = _sensor_readings(sim_state)
        controller.update(readings)
        if sim_state is not None:
            sim_state.duties = controller.duties()
        samples.append(
            reference.read(
                duties=controller.duties(),
                statuses=controller.channel_statuses(),
            )
        )
        time.sleep(0.1)

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

    while current_target_ma <= cfg.MAX_MA:
        cfg.TARGET_MA = round(current_target_ma, 3)
        log(
            f"  TARGET_MA = {current_target_ma:.3f} mA, "
            f"regulating {RAMP_SETTLE_S:.0f}s ..."
        )
        _pump_control(controller, sim_state, RAMP_SETTLE_S)

        log(
            f"  OC ref sample (0% PWM {INSTANT_OFF_WINDOW_S:.1f}s, read, restore duties) …"
        )
        raw, shift = _instant_off_ref_mv_and_restore(
            controller, reference, sim_state
        )
        shift_str = f"{shift:.1f}" if shift is not None else "N/A"
        log(
            f"  ref@off: {raw:.1f} mV  shift(native−off): {shift_str} / "
            f"{cfg.TARGET_SHIFT_MV} mV"
        )

        if shift is not None and shift >= cfg.TARGET_SHIFT_MV:
            confirm_count += 1
            log(f"  target reached ({confirm_count}/{CONFIRM_TICKS})")
            if confirm_count >= CONFIRM_TICKS:
                break
        else:
            confirm_count = 0
            current_target_ma = round(current_target_ma + TARGET_RAMP_STEP_MA, 3)
    else:
        log(
            "WARNING: reached MAX_MA without achieving target shift — "
            "check bonding, anode contact, and water conductivity."
        )

    log(f"Phase 3 — locking in at {current_target_ma:.3f} mA/ch")
    _pump_control(controller, sim_state, min(RAMP_SETTLE_S, 2.0))
    _final_raw, final_shift = _instant_off_ref_mv_and_restore(
        controller, reference, sim_state
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
