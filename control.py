"""
CoilShield ICCP — per-channel control loop.

Design principles:
  - No master wet switch. Each channel classifies path quality (DRY / WEAK_WET /
    CONDUCTIVE / PROTECTING) from current and effective impedance (V/I).
  - DRY: open path → duty 0. WEAK_WET / CONDUCTIVE: ramp PWM from DUTY_PROBE up to
    PWM_MAX_DUTY (no intermediate % cap); PROTECTING regulates toward TARGET_MA.
    CONDUCTIVE: stable path → ramp toward target. PROTECTING: regulate to TARGET_MA.
  - DRY hysteresis requires measurable I (> noise floor) so Z is finite; at I≈0 with no
    drive, classify WEAK_WET so probes run (avoids false DRY when submerged).
  - Hysteresis counters reset every STATE_RECHECK_INTERVAL_S for fresh classification.
  - Low-Z guard (Z < MIN_EFFECTIVE_OHMS): hold non-DRY status to avoid thrash; from DRY
    return WEAK_WET so probe duty can run (avoids DRY+finite-I short latch at 0% PWM).
  - FAULT is orthogonal (over/under-voltage, overcurrent, read errors).
  - Fault auto-recovery: channel retries after FAULT_RETRY_INTERVAL_S.
    After FAULT_RETRY_MAX failures → permanent latch (manual clear needed).
  - Incremental PWM only — no PID. Slow steps prevent current spikes into
    overprotection territory on aluminum-containing coils.
"""

from __future__ import annotations

import os
import statistics
import time
from collections import deque

import config.settings as cfg

_SIM = os.environ.get("COILSHIELD_SIM", "0") == "1"

if not _SIM:
    import RPi.GPIO as GPIO  # noqa: N814


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def classify_channel(ch: "ChannelState", i_ma: float, v_bus: float, cfg) -> str:
    """
    Classify conduction path once per tick (before actuation).
    Does not return PROTECTING — promotion happens in Controller.update.

    DRY evidence needs finite Z (I above the same 0.01 mA floor as z_ohm); at the
    noise floor we do not accumulate dry_count so WEAK_WET / probe can run in water.

    If Z is below MIN_EFFECTIVE_OHMS (suspiciously low), hold prior status for non-DRY
    channels to avoid thrash; from DRY, promote to WEAK_WET so probe duty can clarify.
    """
    z_ohm = (v_bus / (i_ma / 1000.0)) if i_ma > 0.01 else float("inf")

    if z_ohm < float(cfg.MIN_EFFECTIVE_OHMS):
        if ch.status == ChannelState.DRY:
            ch.dry_count = 0
            ch.conductive_count = 0
            return ChannelState.WEAK_WET
        return ch.status

    dry_ma = float(cfg.CHANNEL_DRY_MA)
    if i_ma < dry_ma and i_ma > 0.01:
        ch.dry_count += 1
        ch.conductive_count = 0
        if ch.dry_count >= int(cfg.DRY_HOLD_TICKS):
            return ChannelState.DRY
        return ch.status

    if i_ma >= dry_ma:
        ch.dry_count = 0
    else:
        ch.dry_count = 0

    weak_by_impedance = z_ohm > float(cfg.MAX_EFFECTIVE_OHMS)
    weak_by_current = i_ma < float(cfg.CHANNEL_CONDUCTIVE_MA)

    if weak_by_impedance or weak_by_current:
        ch.conductive_count = 0
        return ChannelState.WEAK_WET

    ch.conductive_count += 1
    if ch.conductive_count >= int(cfg.CONDUCTIVE_HOLD_TICKS):
        return ChannelState.CONDUCTIVE
    return ChannelState.WEAK_WET


class PWMBank:
    """Owns all MOSFET PWM channels. Sim-safe (no GPIO)."""

    def __init__(self) -> None:
        self._duties: dict[int, float] = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
        self._pwm: list = []

        if not _SIM:
            GPIO.setmode(GPIO.BCM)
            for pin in cfg.PWM_GPIO_PINS:
                GPIO.setup(pin, GPIO.OUT)
                p = GPIO.PWM(pin, cfg.PWM_FREQUENCY_HZ)
                p.start(0.0)
                self._pwm.append(p)

    def set_duty(self, ch: int, duty: float) -> None:
        duty = float(duty)
        if duty <= 0.0:
            duty = 0.0
        else:
            duty = float(max(cfg.PWM_MIN_DUTY, min(cfg.PWM_MAX_DUTY, duty)))
        self._duties[ch] = duty
        if not _SIM:
            self._pwm[ch].ChangeDutyCycle(int(round(duty)))

    def duty(self, ch: int) -> float:
        return self._duties[ch]

    def all_off(self) -> None:
        for ch in range(cfg.NUM_CHANNELS):
            self.set_duty(ch, 0.0)

    def cleanup(self) -> None:
        self.all_off()
        if not _SIM:
            for p in self._pwm:
                try:
                    p.stop()
                except Exception:
                    pass
            self._pwm.clear()


class ChannelState:
    """Runtime state for one anode channel."""

    DRY = "DRY"
    WEAK_WET = "WEAK_WET"
    CONDUCTIVE = "CONDUCTIVE"
    PROTECTING = "PROTECTING"
    FAULT = "FAULT"

    def __init__(self, ch: int) -> None:
        self.ch = ch
        self.status = self.DRY
        self.conductive_count: int = 0
        self.dry_count: int = 0
        self.latch_message: str = ""
        self.fault_time: float = 0.0  # when the fault was latched
        self.fault_retry_count: int = 0  # consecutive failed retries
        self.last_state_recheck_monotonic: float = time.monotonic()
        wlen = max(4, int(getattr(cfg, "IMPEDANCE_MEDIAN_WINDOW", 32)))
        self._z_window: deque[float] = deque(maxlen=wlen)


class Controller:
    """
    Main ICCP control loop.

    Call update(readings) each SAMPLE_INTERVAL_S.
    Returns (fault_strings_for_log, fault_latched_globally).
    """

    def __init__(self) -> None:
        self._pwm = PWMBank()
        self._states = [ChannelState(i) for i in range(cfg.NUM_CHANNELS)]
        self._fault_latched = False
        self._faults: list[str] = []

    def update(self, readings: dict[int, dict]) -> tuple[list[str], bool]:
        self._faults = []
        self._check_clear_fault()

        protect_ceiling = min(
            float(cfg.DUTY_PROTECT_MAX), float(cfg.PWM_MAX_DUTY)
        )
        staging_ceiling = float(cfg.PWM_MAX_DUTY)
        probe_duty = min(float(cfg.DUTY_PROBE), staging_ceiling)

        for ch, state in enumerate(self._states):
            r = readings.get(ch, {})

            if state.status == ChannelState.FAULT:
                self._pwm.set_duty(ch, 0.0)
                if state.latch_message:
                    self._faults.append(state.latch_message)
                self._maybe_auto_clear_fault(ch, state, r)
                continue

            if not r.get("ok"):
                self._pwm.set_duty(ch, 0.0)
                self._faults.append(
                    f"CH{ch + 1} READ ERROR: {r.get('error', 'unknown')}"
                )
                if state.status != ChannelState.FAULT:
                    state.status = ChannelState.DRY
                continue

            current_ma = float(r["current"])
            bus_v = float(r["bus_v"])

            if current_ma > self._channel_max_ma(ch):
                self._latch_fault(
                    ch,
                    f"CH{ch + 1} OVERCURRENT: {current_ma:.4f} mA (max {self._channel_max_ma(ch)} mA)",
                )
                continue
            if bus_v < cfg.MIN_BUS_V:
                self._latch_fault(
                    ch,
                    f"CH{ch + 1} UNDERVOLTAGE: {bus_v:.2f} V (min {cfg.MIN_BUS_V} V)",
                )
                continue
            if bus_v > cfg.MAX_BUS_V:
                self._latch_fault(
                    ch,
                    f"CH{ch + 1} OVERVOLTAGE: {bus_v:.2f} V (max {cfg.MAX_BUS_V} V)",
                )
                continue

            now = time.monotonic()
            recheck_s = float(getattr(cfg, "STATE_RECHECK_INTERVAL_S", 10.0))
            if now - state.last_state_recheck_monotonic >= recheck_s:
                state.last_state_recheck_monotonic = now
                state.dry_count = 0
                state.conductive_count = 0

            i_floor = float(getattr(cfg, "Z_COMPUTE_I_A_MIN", 1e-6))
            z_log = bus_v / max(current_ma / 1000.0, i_floor)
            state._z_window.append(z_log)

            classified = classify_channel(state, current_ma, bus_v, cfg)
            target_ma = self._channel_target(ch)
            status = classified
            if (
                classified == ChannelState.CONDUCTIVE
                and abs(current_ma - target_ma) < 0.2
            ):
                status = ChannelState.PROTECTING
            state.status = status

            current_duty = self._pwm.duty(ch)

            if status == ChannelState.DRY:
                self._pwm.set_duty(ch, 0.0)
            elif status == ChannelState.WEAK_WET:
                # Ramp from probe floor toward hardware PWM max (no extra staging % cap).
                lo, hi = probe_duty, staging_ceiling
                step = float(cfg.PWM_STEP)
                if current_duty > hi:
                    new_duty = max(hi, current_duty - step)
                elif current_duty < lo:
                    new_duty = lo
                else:
                    new_duty = min(current_duty + step, hi)
                self._pwm.set_duty(ch, new_duty)
            elif status == ChannelState.CONDUCTIVE:
                err = target_ma - current_ma
                step = 0.5 if err > 0 else (-0.5 if err < 0 else 0.0)
                self._pwm.set_duty(
                    ch, clamp(current_duty + step, 0.0, staging_ceiling)
                )
            elif status == ChannelState.PROTECTING:
                if current_ma < target_ma:
                    new_duty = current_duty + cfg.PWM_STEP
                elif current_ma > target_ma * 1.05:
                    new_duty = current_duty - cfg.PWM_STEP
                else:
                    new_duty = current_duty
                self._pwm.set_duty(ch, clamp(new_duty, 0.0, protect_ceiling))

        self._fault_latched = any(s.status == ChannelState.FAULT for s in self._states)
        return self._faults, self._fault_latched

    def _maybe_auto_clear_fault(
        self, ch: int, state: ChannelState, r: dict
    ) -> None:
        """
        Auto-recovery logic. Runs every tick while a channel is in FAULT.

        - If FAULT_AUTO_CLEAR is False → never auto-clear (legacy behavior).
        - If retry count >= FAULT_RETRY_MAX → permanent latch, don't retry.
        - OVERCURRENT: clears immediately once current drops below
          OVERCURRENT_RECOVERY_THRESHOLD (75% of MAX_MA by default).
          Hysteresis prevents chattering when current hovers at the limit.
        - Other faults: cleared after FAULT_RETRY_INTERVAL_S for re-probe.
        """
        if not getattr(cfg, "FAULT_AUTO_CLEAR", False):
            return

        max_retries = getattr(cfg, "FAULT_RETRY_MAX", 1000)
        if state.fault_retry_count >= max_retries:
            # Permanent latch — update message to indicate manual clear needed
            if "PERMANENT" not in state.latch_message:
                state.latch_message = (
                    f"{state.latch_message} [PERMANENT — touch clear_fault to reset]"
                )
            return

        # Immediate hysteresis recovery for overcurrent faults.
        # Only attempt if the sensor read succeeded this tick.
        if "OVERCURRENT" in state.latch_message and r.get("ok"):
            current_ma = float(r["current"])
            recovery_threshold = getattr(
                cfg, "OVERCURRENT_RECOVERY_THRESHOLD", self._channel_max_ma(ch) * 0.90
            )
            if current_ma < recovery_threshold:
                print(
                    f"[control] CH{ch + 1} OVERCURRENT recovered "
                    f"({current_ma:.4f} mA < {recovery_threshold:.2f} mA): "
                    f"clearing fault"
                )
                state.status = ChannelState.DRY
                state.latch_message = ""
                state.fault_retry_count += 1
                return
            # Current still elevated — don't fall through to timed retry
            return

        retry_interval = getattr(cfg, "FAULT_RETRY_INTERVAL_S", 60.0)
        elapsed = time.monotonic() - state.fault_time
        if elapsed < retry_interval:
            return

        # Time to retry — clear fault and return to DRY for re-classification
        print(
            f"[control] CH{ch + 1} auto-retry "
            f"({state.fault_retry_count + 1}/{max_retries}): clearing fault"
        )
        state.status = ChannelState.DRY
        state.latch_message = ""
        state.fault_retry_count += 1

    def update_potential_target(self, shift_mv: float | None) -> None:
        """
        Outer loop: nudge TARGET_MA to keep polarization in the safe window.
        Call once per LOG_INTERVAL_S tick, not every SAMPLE_INTERVAL_S.
        """
        if shift_mv is None:
            return

        lo = float(cfg.TARGET_SHIFT_MV) * 0.8
        hi = float(cfg.MAX_SHIFT_MV)
        step = float(cfg.TARGET_MA_STEP)
        max_target = float(cfg.MAX_MA) * 0.8

        if shift_mv < lo:
            cfg.TARGET_MA = round(min(cfg.TARGET_MA + step, max_target), 3)
        elif shift_mv > hi:
            cfg.TARGET_MA = round(max(cfg.TARGET_MA - step, 0.05), 3)

    def duties(self) -> dict[int, float]:
        return {i: self._pwm.duty(i) for i in range(cfg.NUM_CHANNELS)}

    def channel_statuses(self) -> dict[int, str]:
        return {i: self._states[i].status for i in range(cfg.NUM_CHANNELS)}

    def median_impedance_ohm(self, ch: int) -> float | None:
        """Median effective Ω over the rolling window (None until enough samples)."""
        w = self._states[ch]._z_window
        if len(w) < 3:
            return None
        return float(statistics.median(w))

    @property
    def fault_latched(self) -> bool:
        return self._fault_latched

    def any_wet(self) -> bool:
        return any(s.status == ChannelState.PROTECTING for s in self._states)

    def thermal_off(self) -> None:
        """Zero all PWM outputs without touching channel states (temp-range pause)."""
        self._pwm.all_off()

    def cleanup(self) -> None:
        self._pwm.cleanup()

    def channel_target_ma(self, ch: int) -> float:
        """Per-channel protection current setpoint (mA); same rule as internal classify."""
        return self._channel_target(ch)

    def _channel_target(self, ch: int) -> float:
        return float(getattr(cfg, "CHANNEL_TARGET_MA", {}).get(ch, cfg.TARGET_MA))

    def _channel_max_ma(self, ch: int) -> float:
        return float(getattr(cfg, "CHANNEL_MAX_MA", {}).get(ch, cfg.MAX_MA))

    def _latch_fault(self, ch: int, msg: str) -> None:
        self._pwm.set_duty(ch, 0.0)
        self._states[ch].status = ChannelState.FAULT
        self._states[ch].latch_message = msg
        self._states[ch].fault_time = time.monotonic()
        # Don't reset retry count here — it accumulates across latch cycles
        self._faults.append(msg)

    def _check_clear_fault(self) -> None:
        if not cfg.CLEAR_FAULT_FILE.exists():
            return
        try:
            cfg.CLEAR_FAULT_FILE.unlink()
        except OSError:
            return
        for state in self._states:
            if state.status == ChannelState.FAULT:
                state.status = ChannelState.DRY
                state.latch_message = ""
                state.fault_retry_count = 0  # manual clear resets retry count
                state.fault_time = 0.0
        self._fault_latched = False
        print("[control] Fault latch cleared.")