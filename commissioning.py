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
TARGET_RAMP_STEP_MA: float = 0.05
RAMP_SETTLE_S: float = 10.0
CONFIRM_TICKS: int = 5


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
        f"Target shift: +{cfg.TARGET_SHIFT_MV} mV  →  lock at "
        f"{native_mv + cfg.TARGET_SHIFT_MV:.1f} mV (reference reading)"
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

        shift = reference.shift_mv(
            duties=controller.duties(),
            statuses=controller.channel_statuses(),
        )
        raw = reference.last_raw_mv
        shift_str = f"{shift:.1f}" if shift is not None else "N/A"
        log(
            f"  ref: {raw:.1f} mV  shift: {shift_str} / "
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
    _update_comm_file(
        {
            "commissioned_target_ma": current_target_ma,
            "commissioned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "final_shift_mv": reference.shift_mv(
                duties=controller.duties(),
                statuses=controller.channel_statuses(),
            ),
        }
    )
    log("Done.")
    return current_target_ma
