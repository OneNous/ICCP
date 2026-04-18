"""
CoilShield ICCP — per-channel control loop.

Design principles:
  - No master wet switch. Each channel self-detects wet/dry via current reading.
  - Anodes touch the coil surface directly. Dry = no ionic path = no current.
    Wet = condensate film = ionic path established = regulate to target.
  - Same TARGET_MA on every channel — longer wet dwell at drain-prone spots
    delivers more coulombs over time; no install-time position weighting.
  - Dormant channels send a brief probe pulse every PROBE_INTERVAL_S to detect
    re-wetting without waiting for the next commissioning cycle.
  - Fault auto-recovery: channel retries after FAULT_RETRY_INTERVAL_S.
    After FAULT_RETRY_MAX failures → permanent latch (manual clear needed).
  - Incremental PWM only — no PID. Slow steps prevent current spikes into
    overprotection territory on aluminum-containing coils.
"""

from __future__ import annotations

import os
import time

import config.settings as cfg

_SIM = os.environ.get("COILSHIELD_SIM", "0") == "1"

if not _SIM:
    import RPi.GPIO as GPIO  # noqa: N814


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

    DORMANT    = "DORMANT"
    PROBING    = "PROBING"
    PROTECTING = "PROTECTING"
    FAULT      = "FAULT"

    def __init__(self, ch: int) -> None:
        self.ch = ch
        self.status = self.DORMANT
        self.last_probe_time: float = 0.0
        self.probe_since: float | None = None
        self.latch_message: str = ""
        self.fault_time: float = 0.0       # when the fault was latched
        self.fault_retry_count: int = 0    # consecutive failed retries


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

        for ch, state in enumerate(self._states):
            r = readings.get(ch, {})

            if state.status == ChannelState.FAULT:
                self._pwm.set_duty(ch, 0.0)
                if state.latch_message:
                    self._faults.append(state.latch_message)
                # Auto-recovery check
                self._maybe_auto_clear_fault(ch, state, r)
                continue

            if not r.get("ok"):
                self._pwm.set_duty(ch, 0.0)
                self._faults.append(
                    f"CH{ch + 1} READ ERROR: {r.get('error', 'unknown')}"
                )
                state.probe_since = None
                if state.status in (ChannelState.PROBING, ChannelState.PROTECTING):
                    state.status = ChannelState.DORMANT
                    state.last_probe_time = time.monotonic()
                continue

            current_ma = float(r["current"])
            bus_v = float(r["bus_v"])

            # Probe early-exit: high current during a probe pulse means the channel
            # is wet with low impedance. This is not a fault — abort the probe and
            # hand off to the protection loop rather than hard-faulting.
            # PROBE_MAX_MA defaults to 50% of MAX_MA, leaving plenty of headroom
            # below the fault threshold while still being well above the wet threshold.
            if state.status == ChannelState.PROBING:
                probe_max_ma = getattr(cfg, "PROBE_MAX_MA", cfg.MAX_MA * 0.5)
                if current_ma >= probe_max_ma:
                    state.probe_since = None
                    state.status = ChannelState.PROTECTING
                    self._pwm.set_duty(ch, 0.0)
                    continue  # protection loop takes over next tick from duty=0

            if current_ma > cfg.MAX_MA:
                self._latch_fault(
                    ch,
                    f"CH{ch + 1} OVERCURRENT: {current_ma:.4f} mA (max {cfg.MAX_MA} mA)",
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

            if state.status == ChannelState.PROBING:
                if state.probe_since is None:
                    state.probe_since = time.monotonic()
                if time.monotonic() - state.probe_since < cfg.PROBE_DURATION_S:
                    self._pwm.set_duty(ch, float(cfg.PROBE_DUTY_PCT))
                    continue
                state.probe_since = None
                if current_ma >= cfg.CHANNEL_WET_THRESHOLD_MA:
                    state.status = ChannelState.PROTECTING
                else:
                    state.status = ChannelState.DORMANT
                    state.last_probe_time = time.monotonic()
                    self._pwm.set_duty(ch, 0.0)
                    continue

            channel_is_wet = current_ma >= cfg.CHANNEL_WET_THRESHOLD_MA

            if not channel_is_wet:
                self._pwm.set_duty(ch, 0.0)
                if state.status == ChannelState.PROBING:
                    state.status = ChannelState.DORMANT
                    state.last_probe_time = time.monotonic()
                    state.probe_since = None
                elif state.status == ChannelState.PROTECTING:
                    state.status = ChannelState.DORMANT
                    state.last_probe_time = time.monotonic()
                elif state.status == ChannelState.DORMANT:
                    elapsed = time.monotonic() - state.last_probe_time
                    if elapsed >= cfg.PROBE_INTERVAL_S:
                        state.status = ChannelState.PROBING
                        state.probe_since = time.monotonic()
                        self._pwm.set_duty(ch, float(cfg.PROBE_DUTY_PCT))
                continue

            state.status = ChannelState.PROTECTING
            target_ma = self._channel_target(ch)
            current_duty = self._pwm.duty(ch)

            if current_ma < target_ma:
                new_duty = current_duty + cfg.PWM_STEP
            elif current_ma > target_ma * 1.05:
                new_duty = current_duty - cfg.PWM_STEP
            else:
                new_duty = current_duty

            self._pwm.set_duty(ch, new_duty)

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
                cfg, "OVERCURRENT_RECOVERY_THRESHOLD", cfg.MAX_MA * 0.90
            )
            if current_ma < recovery_threshold:
                print(
                    f"[control] CH{ch + 1} OVERCURRENT recovered "
                    f"({current_ma:.4f} mA < {recovery_threshold:.2f} mA): "
                    f"clearing fault"
                )
                state.status = ChannelState.DORMANT
                state.latch_message = ""
                state.last_probe_time = time.monotonic()
                state.probe_since = None
                state.fault_retry_count += 1
                return
            # Current still elevated — don't fall through to timed retry
            return

        retry_interval = getattr(cfg, "FAULT_RETRY_INTERVAL_S", 60.0)
        elapsed = time.monotonic() - state.fault_time
        if elapsed < retry_interval:
            return

        # Time to retry — clear fault and return to DORMANT for re-probe
        print(
            f"[control] CH{ch + 1} auto-retry "
            f"({state.fault_retry_count + 1}/{max_retries}): clearing fault"
        )
        state.status = ChannelState.DORMANT
        state.latch_message = ""
        state.last_probe_time = time.monotonic()
        state.probe_since = None
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

    @property
    def fault_latched(self) -> bool:
        return self._fault_latched

    def any_wet(self) -> bool:
        return any(s.status == ChannelState.PROTECTING for s in self._states)

    def cleanup(self) -> None:
        self._pwm.cleanup()

    def _channel_target(self, _ch: int) -> float:
        return float(cfg.TARGET_MA)

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
                state.status = ChannelState.DORMANT
                state.latch_message = ""
                state.last_probe_time = 0.0
                state.probe_since = None
                state.fault_retry_count = 0  # manual clear resets retry count
                state.fault_time = 0.0
        self._fault_latched = False
        print("[control] Fault latch cleared.")