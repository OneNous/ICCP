"""
CoilShield ICCP — per-channel sense + path FSM; optional unified anode PWM bank.

Design principles:
  - Path FSM: OPEN / REGULATE / PROTECTING (+ FAULT), from measured I and Z = V/I.
  - OPEN: no reliable path → 0% duty. REGULATE: approach current toward target
    with per-mode PWM_STEP_* ramps (optional per-channel CHANNEL_PWM_STEP_* dicts when
    `SHARED_RETURN_PWM` is off) under Vcell duty cap. PROTECTING: fine servo.
  - When `SHARED_RETURN_PWM` is True: all MOSFET gates share the same
    duty; ramps use **aggregate** I vs **sum of per-channel targets** and global
    `PWM_STEP_*` only. Default in settings is **False** (independent per-channel duty);
    set `SHARED_RETURN_PWM = True` in `config/settings.py` for shared bank. Identical
    software duty does not phase-align separate RPi.GPIO
    soft-PWM instances; use one GPIO fanout to gates if you need a single edge-aligned wave.
  - Internal path class (PATH_OPEN / PATH_WEAK / PATH_STRONG) drives transitions;
    PROTECTING requires PATH_STRONG plus near-target hysteresis.
  - OPEN hysteresis needs measurable I for finite Z; at I≈0 without drive, classify
    weak path so REGULATE probe can run (avoids false OPEN when submerged).
  - Hysteresis counters: when ``STATE_RECHECK_RESET_PROTECT_STREAKS`` is True, protecting
    enter/exit streaks reset every ``STATE_RECHECK_INTERVAL_S`` (default: streaks are not reset).
  - Low-Z guard (Z < MIN_EFFECTIVE_OHMS): hold non-OPEN path class; from OPEN return
    weak path so probe duty can clarify.
  - FAULT is orthogonal (over/under-voltage, overcurrent, read errors).
  - Fault auto-recovery: channel retries after FAULT_RETRY_INTERVAL_S.
  - Incremental PWM only — no PID. Slow steps limit current spikes on sensitive coils.
  - Gate duty is quantized to ``PWM_DUTY_QUANTUM`` in ``_quantize_duty_for_gpio`` (default **0.01%**
    in ``config.settings``; ``0`` disables rounding). ``PWM_STEP*`` is ramp % per tick (defaults
    are multiples of that quantum so the inner loop can use the full hardware resolution).
  - Optional high-side anode relays (`ANODE_RELAY_GPIO_PINS`): de-energize on
    :meth:`Controller.all_outputs_off` for true +5V disconnect when wired.
"""

from __future__ import annotations

import os
import statistics
import time
from collections import deque

import config.settings as cfg
from channel_labels import anode_hw_label, anode_label
from iccp_electrolyte import cell_impedance_ohm
from sensors import ina219_read_failure_expected_idle

_SIM = os.environ.get("COILSHIELD_SIM", "0") == "1"

if not _SIM:
    import RPi.GPIO as GPIO  # noqa: N814

# Path quality for classify_path (not persisted — ChannelState uses OPEN/REGULATE/PROTECTING).
PATH_OPEN = "PATH_OPEN"
PATH_WEAK = "PATH_WEAK"
PATH_STRONG = "PATH_STRONG"

# state_v2 labels (docs/iccp-requirements.md §2.2). Legacy ChannelState.status is mapped
# from these where possible, but the spec FSM lives in ChannelState.state_v2 and is what
# `latest.json.state_v2` / `all_protected` / `any_active` read from.
STATE_V2_OFF = "Off"
STATE_V2_PROBING = "Probing"
STATE_V2_POLARIZING = "Polarizing"
STATE_V2_PROTECTED = "Protected"
STATE_V2_OVERPROTECTED = "Overprotected"
STATE_V2_FAULT = "Fault"


def _bus_level_read_failure(r: dict) -> bool:
    """Classify an INA219 read failure as a bus-level event (I²C bus unhealthy).

    Per docs/iccp-requirements.md §4.3 / Decision Q8, the `all_off` fail-safe should fire
    only when the bus itself is flaky — not on a single-channel read glitch. Linux reports
    errno 5 (EIO) / 121 (EREMOTEIO) for SMBus NACKs on the shared bus; those are the only
    cases we escalate bus-wide. Non-ok reads without that errno (e.g. a one-off NACK with a
    different errno, or a decode error) stay per-channel.
    """
    if r.get("ok"):
        return False
    err = str(r.get("error", ""))
    errno_field = r.get("errno")
    bus_errnos = tuple(getattr(cfg, "INA219_BUS_LEVEL_ERRNOS", (5, 121)))
    if isinstance(errno_field, int) and errno_field in bus_errnos:
        return True
    for en in bus_errnos:
        if f"Errno {en}" in err or f"[Errno {en}]" in err:
            return True
    return False


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _shared_return_pwm() -> bool:
    return bool(getattr(cfg, "SHARED_RETURN_PWM", False))


def _session_start_duty_pct() -> float:
    """
    Duty to apply when a PWM session begins (``PWMBank`` init, ``leave_static_gate_off``,
    :meth:`Controller.seed_session_start_duty` after 0% soak windows). Matches ``DUTY_PROBE`` /
    ``PWM_MIN_DUTY`` (default 0.01% quantum).
    """
    p = float(getattr(cfg, "DUTY_PROBE", 0.01) or 0.0)
    if p <= 0.0:
        p = float(getattr(cfg, "PWM_MIN_DUTY", 0.01) or 0.01)
    p = min(p, float(getattr(cfg, "PWM_MAX_DUTY", 100.0)))
    return max(0.0, p)


def pwm_ramp_step(ch: int, *, regulating: bool, increasing: bool) -> float:
    """
    PWM duty delta (% per control tick) for this channel, mode, and direction.

    Per-channel dicts (CHANNEL_PWM_STEP_*_REGULATE / _PROTECTING) override the global
    PWM_STEP_* scalars for that index only, unless `SHARED_RETURN_PWM` is True (bank
    mode: globals only, dicts ignored).
    """
    base = float(cfg.PWM_STEP)
    if regulating:
        global_key = (
            "PWM_STEP_UP_REGULATE" if increasing else "PWM_STEP_DOWN_REGULATE"
        )
        channel_key = (
            "CHANNEL_PWM_STEP_UP_REGULATE"
            if increasing
            else "CHANNEL_PWM_STEP_DOWN_REGULATE"
        )
    else:
        global_key = (
            "PWM_STEP_UP_PROTECTING"
            if increasing
            else "PWM_STEP_DOWN_PROTECTING"
        )
        channel_key = (
            "CHANNEL_PWM_STEP_UP_PROTECTING"
            if increasing
            else "CHANNEL_PWM_STEP_DOWN_PROTECTING"
        )
    m = None if _shared_return_pwm() else getattr(cfg, channel_key, None)
    if isinstance(m, dict) and ch in m:
        return float(m[ch])
    return float(getattr(cfg, global_key, base))


def duty_pct_cap_for_vcell(bus_v: float, cfg) -> float:
    """
    Max PWM duty (%) so Vc ≈ bus_v * duty/100 does not exceed VCELL_HARD_MAX_V.
    If VCELL_HARD_MAX_V <= 0, only PWM_MAX_DUTY applies.
    """
    vlim = float(getattr(cfg, "VCELL_HARD_MAX_V", 0.0) or 0.0)
    if vlim <= 0.0 or bus_v < 1e-6:
        return float(cfg.PWM_MAX_DUTY)
    return min(float(cfg.PWM_MAX_DUTY), 100.0 * vlim / bus_v)


def _quantize_duty_for_gpio(duty: float) -> float:
    """Match ``PWM_DUTY_QUANTUM`` (e.g. 0.01 for 0.01% steps). ``0`` disables extra rounding."""
    q = float(getattr(cfg, "PWM_DUTY_QUANTUM", 0.01) or 0.0)
    d = max(0.0, min(100.0, float(duty)))
    if q > 0.0:
        d = round(d / q) * q
        d = min(100.0, max(0.0, d))
    return d


def _rpi_change_duty_cycle_arg(duty: float) -> float:
    """
    Duty % to pass to RPi.GPIO ``PWM.ChangeDutyCycle``.

    RPi.GPIO uses floats internally; **do not** cast to ``int`` (that would make e.g. 0.01% → 0
    and erase sub-1% drive). Rounding to 2 decimal places matches ``PWM_DUTY_QUANTUM``=0.01% and
    tames binary float noise before the library call.
    """
    d = float(duty)
    if d <= 0.0:
        return 0.0
    return round(max(0.0, min(100.0, d)), 2)


def classify_path(ch: "ChannelState", i_ma: float, v_bus: float, cfg) -> str:
    """
    Classify conduction path once per tick (before FSM transitions).
    Returns PATH_OPEN, PATH_WEAK, or PATH_STRONG — not the persisted channel status.

    PATH_OPEN evidence needs finite Z (I above the same 0.01 mA floor as z_ohm); at the
    noise floor we do not accumulate dry_count so weak path can run in water.

    If Z is below MIN_EFFECTIVE_OHMS, hold prior path class for non-OPEN channels;
    from OPEN, promote to PATH_WEAK so probe duty can clarify.
    """
    z_ohm = (v_bus / (i_ma / 1000.0)) if i_ma > 0.01 else float("inf")

    if z_ohm < float(cfg.MIN_EFFECTIVE_OHMS):
        if ch.status == ChannelState.OPEN:
            ch.dry_count = 0
            ch.conductive_count = 0
            return PATH_WEAK
        return ch._last_path_class

    dry_ma = float(cfg.CHANNEL_DRY_MA)
    if i_ma < dry_ma and i_ma > 0.01:
        ch.dry_count += 1
        ch.conductive_count = 0
        if ch.dry_count >= int(cfg.DRY_HOLD_TICKS):
            return PATH_OPEN
        return ch._last_path_class

    if i_ma >= dry_ma:
        ch.dry_count = 0
    else:
        ch.dry_count = 0

    weak_by_impedance = z_ohm > float(cfg.MAX_EFFECTIVE_OHMS)
    weak_by_current = i_ma < float(cfg.CHANNEL_CONDUCTIVE_MA)

    if weak_by_impedance or weak_by_current:
        ch.conductive_count = 0
        return PATH_WEAK

    ch.conductive_count += 1
    if ch.conductive_count >= int(cfg.CONDUCTIVE_HOLD_TICKS):
        return PATH_STRONG
    return PATH_WEAK


def classify_channel(ch: "ChannelState", i_ma: float, v_bus: float, cfg) -> str:
    """Deprecated name for classify_path; returns PATH_OPEN / PATH_WEAK / PATH_STRONG."""
    return classify_path(ch, i_ma, v_bus, cfg)


class PWMBank:
    """Owns all MOSFET PWM channels. Sim-safe (no GPIO)."""

    def __init__(self) -> None:
        self._duties: dict[int, float] = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
        self._pwm: list = []
        self._static_mode: bool = False
        self._pwm_carrier_hz: int = int(cfg.PWM_FREQUENCY_HZ)

        if not _SIM:
            GPIO.setmode(GPIO.BCM)
            for pin in cfg.PWM_GPIO_PINS:
                GPIO.setup(pin, GPIO.OUT)
                p = GPIO.PWM(pin, self._pwm_carrier_hz)
                p.start(0.0)
                self._pwm.append(p)
        # Stay at 0% until the control FSM (REGULATE) or an explicit :meth:`set_duty` /
        # :meth:`set_duty_unified` / :meth:`leave_static_gate_off` / :meth:`seed_session_start_duty` applies drive.

    @property
    def static_gate_off_active(self) -> bool:
        """True while commissioning (or similar) holds gates at static LOW instead of soft-PWM."""
        return self._static_mode

    def enter_static_gate_off(self) -> None:
        """
        Stop soft-PWM and drive each gate BCM pin LOW (matches cleanup teardown semantics).

        Used during commissioning Phase 1 so shunt “at rest” matches true gate-off. No-op in
        sim. Raising duty while in this mode raises RuntimeError (call leave_static_gate_off first).
        """
        if _SIM or self._static_mode:
            return
        self._static_mode = True
        for i, p in enumerate(self._pwm):
            try:
                p.stop()
            except Exception:
                pass
            try:
                pin = cfg.PWM_GPIO_PINS[i]
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            except Exception:
                pass
        self._pwm = []

    def leave_static_gate_off(self) -> None:
        """Recreate soft-PWM at ``_pwm_carrier_hz`` with session start duty (``DUTY_PROBE``). No-op in sim or if idle."""
        if _SIM or not self._static_mode:
            return
        self._static_mode = False
        self._pwm = []
        for pin in cfg.PWM_GPIO_PINS:
            GPIO.setup(pin, GPIO.OUT)
            p = GPIO.PWM(pin, self._pwm_carrier_hz)
            p.start(0.0)
            self._pwm.append(p)
        s0 = _session_start_duty_pct()
        if s0 > 0.0:
            self.set_duty_unified(s0)
        else:
            for ch in range(cfg.NUM_CHANNELS):
                self._duties[ch] = 0.0

    def set_duty(self, ch: int, duty: float) -> None:
        duty = float(duty)
        if duty <= 0.0:
            duty = 0.0
        else:
            duty = float(max(cfg.PWM_MIN_DUTY, min(cfg.PWM_MAX_DUTY, duty)))
            duty = _quantize_duty_for_gpio(duty)
            duty = float(
                max(cfg.PWM_MIN_DUTY, min(cfg.PWM_MAX_DUTY, duty))
            )
        self._duties[ch] = duty
        if _SIM:
            return
        if self._static_mode:
            if duty > 0.0:
                raise RuntimeError(
                    "PWMBank: cannot raise PWM duty while static_gate_off mode is active; "
                    "call leave_static_gate_off() first."
                )
            pin = cfg.PWM_GPIO_PINS[ch]
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
            return
        self._pwm[ch].ChangeDutyCycle(_rpi_change_duty_cycle_arg(duty))

    def set_duty_unified(self, duty: float) -> None:
        """Set the same PWM duty (%) on every MOSFET gate. Sim-safe. Honors static mode."""
        duty = float(duty)
        if duty <= 0.0:
            duty = 0.0
        else:
            duty = float(max(cfg.PWM_MIN_DUTY, min(cfg.PWM_MAX_DUTY, duty)))
            duty = _quantize_duty_for_gpio(duty)
            duty = float(
                max(cfg.PWM_MIN_DUTY, min(cfg.PWM_MAX_DUTY, duty))
            )
        for ch in range(cfg.NUM_CHANNELS):
            self._duties[ch] = duty
        if _SIM:
            return
        if self._static_mode:
            if duty > 0.0:
                raise RuntimeError(
                    "PWMBank: cannot raise PWM duty while static_gate_off mode is active; "
                    "call leave_static_gate_off() first."
                )
            for ch in range(cfg.NUM_CHANNELS):
                pin = cfg.PWM_GPIO_PINS[ch]
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            return
        d_hw = _rpi_change_duty_cycle_arg(duty)
        for ch in range(cfg.NUM_CHANNELS):
            self._pwm[ch].ChangeDutyCycle(d_hw)

    def duty(self, ch: int) -> float:
        return self._duties[ch]

    def all_off(self) -> None:
        self.set_duty_unified(0.0)

    def set_pwm_frequency_hz(self, hz: int) -> None:
        """Change PWM carrier on all channels (RPi.GPIO 0.7+). No-op in sim or if unset."""
        hz = int(max(1, hz))
        self._pwm_carrier_hz = hz
        if _SIM or not self._pwm:
            return
        for p in self._pwm:
            try:
                p.ChangeFrequency(hz)
            except Exception:
                pass

    def cleanup(self) -> None:
        if not _SIM and self._static_mode:
            self.leave_static_gate_off()
        self.all_off()
        if not _SIM:
            for i, p in enumerate(self._pwm):
                try:
                    p.stop()
                except Exception:
                    pass
                # Drive pin explicitly LOW after stopping PWM so the MOSFET gate
                # is grounded before GPIO.cleanup() releases the pin to INPUT/floating.
                try:
                    GPIO.output(cfg.PWM_GPIO_PINS[i], GPIO.LOW)
                except Exception:
                    pass
            self._pwm.clear()


class ChannelState:
    """Runtime state for one anode channel."""

    OPEN = "OPEN"
    REGULATE = "REGULATE"
    PROTECTING = "PROTECTING"
    FAULT = "FAULT"

    def __init__(self, ch: int) -> None:
        self.ch = ch
        self.status = self.OPEN
        self.conductive_count: int = 0
        self.dry_count: int = 0
        self.protecting_enter_streak: int = 0
        self.protecting_exit_streak: int = 0
        self._last_path_class: str = PATH_WEAK
        self.latch_message: str = ""
        self.fault_time: float = 0.0  # when the fault was latched
        self.fault_retry_count: int = 0  # consecutive failed retries
        self.overcurrent_streak: int = 0  # consecutive ticks with I > MAX before latch
        self.last_state_recheck_monotonic: float = time.monotonic()
        wlen = max(4, int(getattr(cfg, "IMPEDANCE_MEDIAN_WINDOW", 32)))
        self._z_window: deque[float] = deque(maxlen=wlen)
        self._feedforward_done: bool = False
        # --- Shift-based FSM (docs/iccp-requirements.md §2.2, §6) ---
        self.state_v2: str = STATE_V2_OFF
        self.state_v2_enter_monotonic: float = time.monotonic()
        self.shift_above_target_since: float | None = None  # for T_POL_STABLE
        self.shift_below_target_since: float | None = None  # for T_SLIP (exit Protected)
        self.shift_over_max_since: float | None = None      # for T_OVER_FAULT
        self.shift_under_max_since: float | None = None     # for T_OVER_EXIT
        self.polarizing_since: float | None = None          # for T_POLARIZE_MAX
        self.probing_since: float | None = None             # for T_PROBE_MAX
        self.polarize_retry_count: int = 0
        self.polarize_retry_next_unix: float | None = None
        self.polarize_backoff_until_mono: float | None = None
        self.fault_reason: str = ""  # e.g. CANNOT_POLARIZE, OVERPROTECTION, REFERENCE_INVALID
        self.last_shift_mv: float | None = None


class Controller:
    """
    Main ICCP control loop.

    Call update(readings) each SAMPLE_INTERVAL_S.
    Returns (fault_strings_for_log, fault_latched_globally).
    """

    def __init__(self) -> None:
        cfg.validate_channel_config()
        self._pwm = PWMBank()
        self._states = [ChannelState(i) for i in range(cfg.NUM_CHANNELS)]
        self._fault_latched = False
        self._faults: list[str] = []
        self._thermal_pause: bool = False
        # True during optional post-start window: same outputs-off path as thermal pause, but for
        # ref-electrode OCP depolarization (not temperature). See ``REFERENCE_STARTUP_STABILIZE_S``.
        self._reference_startup_soak: bool = False
        # System-level all_protected (§2.2): all channels Protected for T_SYSTEM_STABLE.
        self._boot_wall_time: float = time.time()
        self._all_protected_streak_mono: float | None = None
        self._first_all_protected_wall: float | None = None
        self._anode_relay_gpio_ready: bool = False
        # Throttle for :meth:`update_potential_target` (live ref shift) vs ``force=True`` (LOG+instant off).
        self._last_outer_potential_nudge_s: float | None = None

    def set_thermal_pause(self, active: bool) -> None:
        """When True, `update()` keeps all PWM off but still evaluates read errors and FAULT recovery."""
        self._thermal_pause = bool(active)

    def set_reference_startup_soak(self, active: bool) -> None:
        """When True, `update()` holds all channels at 0%% (ref startup stabilize)."""
        self._reference_startup_soak = bool(active)

    def seed_session_start_duty(self) -> None:
        """Set all outputs to the session start floor (``DUTY_PROBE``) after 0%% windows (e.g. ref soak)."""
        d = _session_start_duty_pct()
        if d <= 0.0:
            return
        self._pwm.set_duty_unified(d)

    def all_outputs_off(self) -> None:
        """Drive every anode channel to 0% PWM; de-energize anode supply relays if configured."""
        self._pwm.all_off()
        self._relays_deenergize()

    def _relays_deenergize(self) -> None:
        """Open high-side anode path (relay off) when :data:`ANODE_RELAY_GPIO_PINS` is set. No-op in sim."""
        pins = getattr(cfg, "ANODE_RELAY_GPIO_PINS", None)
        if not pins or _SIM:
            return
        energize_high = bool(getattr(cfg, "ANODE_RELAY_ENERGIZE_HIGH", True))
        de_energize = GPIO.LOW if energize_high else GPIO.HIGH
        for pin in pins:
            if not self._anode_relay_gpio_ready:
                GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, de_energize)
        self._anode_relay_gpio_ready = True

    def _relays_energize(self) -> None:
        """Energize anode supply relays (future use when power-on sequencing is needed). No-op if unset."""
        pins = getattr(cfg, "ANODE_RELAY_GPIO_PINS", None)
        if not pins or _SIM:
            return
        energize_high = bool(getattr(cfg, "ANODE_RELAY_ENERGIZE_HIGH", True))
        on_level = GPIO.HIGH if energize_high else GPIO.LOW
        for pin in pins:
            if not self._anode_relay_gpio_ready:
                GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, on_level)
        self._anode_relay_gpio_ready = True

    def output_duty_pct(self, ch: int) -> float:
        """Current PWM duty (%) for channel ``ch`` (same as ch 0 in shared return bank mode)."""
        if _shared_return_pwm():
            return float(self._pwm.duty(0))
        return float(self._pwm.duty(ch))

    def set_output_duty_pct(self, ch: int, duty: float) -> None:
        """Set PWM duty. In shared return mode all gates get the same duty (``ch`` ignored for drive)."""
        if _shared_return_pwm():
            self._pwm.set_duty_unified(duty)
        else:
            self._pwm.set_duty(ch, duty)

    def set_pwm_carrier_hz(self, hz: int) -> None:
        """Set PWM carrier frequency on all channels (hardware only)."""
        self._pwm.set_pwm_frequency_hz(hz)

    def enter_static_gate_off(self) -> None:
        """Hold MOSFET gates at static LOW (commissioning Phase 1); see :meth:`PWMBank.enter_static_gate_off`."""
        self._pwm.enter_static_gate_off()

    def leave_static_gate_off(self) -> None:
        """Restore soft-PWM after static gate-off; see :meth:`PWMBank.leave_static_gate_off`."""
        self._pwm.leave_static_gate_off()

    def _any_drive_intent(self, readings: dict[int, dict]) -> bool:
        """True if any channel is regulating, has ramped above the probe floor, or shows wet I."""
        wet_ma = float(getattr(cfg, "CHANNEL_WET_THRESHOLD_MA", 0.15))
        probe_session = min(
            _session_start_duty_pct(),
            float(getattr(cfg, "PWM_MAX_DUTY", 100.0)),
        )
        for ch in range(cfg.NUM_CHANNELS):
            st = self._states[ch].status
            if st in (ChannelState.REGULATE, ChannelState.PROTECTING):
                return True
            d = self._pwm.duty(0) if _shared_return_pwm() else self._pwm.duty(ch)
            if d > probe_session + 1e-9:
                return True
            r = readings.get(ch, {})
            if r.get("ok") and float(r.get("current", 0) or 0) > wet_ma:
                return True
        return False

    def _ina219_idle_benign_ch(self, ch: int, readings: dict[int, dict]) -> bool:
        """Transient I2C while PWM idle and FSM not conducting — not a live CP fault."""
        r = readings.get(ch, {})
        if r.get("ok"):
            return False
        d = self._pwm.duty(0) if _shared_return_pwm() else self._pwm.duty(ch)
        return ina219_read_failure_expected_idle(
            ok=False,
            error=r.get("error"),
            duty_pct=d,
            fsm_state=self._states[ch].status,
            current_ma=float(r.get("current", 0) or 0),
            bus_v=float(r.get("bus_v", 0) or 0),
        )

    def _pwm_zero_for_channel(self, ch: int) -> None:
        """Zero PWM: unified bank in shared return mode, else single channel only."""
        if _shared_return_pwm():
            self._pwm.set_duty_unified(0.0)
        else:
            self._pwm.set_duty(ch, 0.0)

    def _apply_unified_bank_pwm(
        self,
        rows: list[dict],
        *,
        protect_ceiling: float,
        staging_ceiling: float,
        probe_floor: float,
    ) -> None:
        """Set one duty from aggregate I vs sum of per-channel targets (all gates identical)."""
        if not rows:
            self._pwm.set_duty_unified(0.0)
            return
        t_tot = sum(
            self._channel_target(c)
            for c in range(cfg.NUM_CHANNELS)
            if cfg.is_channel_active(c)
        )
        i_tot = float(sum(r["current_ma"] for r in rows))
        min_bus = min(float(r["bus_v"]) for r in rows)
        min_duty_cap = duty_pct_cap_for_vcell(min_bus, cfg)
        probe_d = min(probe_floor, min_duty_cap)
        hi_ramp = min(staging_ceiling, min_duty_cap)
        hi_protect = min(protect_ceiling, min_duty_cap)
        stats = {r["status"] for r in rows}
        if not stats.intersection(
            {ChannelState.REGULATE, ChannelState.PROTECTING}
        ):
            self._pwm.set_duty_unified(0.0)
            return
        any_prot = any(r["status"] == ChannelState.PROTECTING for r in rows)
        cur = float(self._pwm.duty(0))
        if any_prot:
            if i_tot < t_tot:
                new = cur + pwm_ramp_step(
                    0, regulating=False, increasing=True
                )
            elif i_tot > t_tot * 1.05:
                new = cur - pwm_ramp_step(
                    0, regulating=False, increasing=False
                )
            else:
                new = cur
            self._pwm.set_duty_unified(clamp(new, 0.0, hi_protect))
            return
        lo, hi = probe_d, hi_ramp
        # REGULATE: aggregate idle hold: |I| below noise floor but total current already
        # at/above the summed setpoint (e.g. t_tot in the 0.04 mA range with REGULATE_IDLE
        # 0.05 mA). Uncommon at nominal per-channel targets (t_tot >> idle band) but valid.
        # If i_tot < t_tot, keep ramping (do not force 0%% — unblocks probe floor / avoids the
        # per-channel 0%% deadlock with small targets).
        idle_off = float(getattr(cfg, "REGULATE_IDLE_OFF_BELOW_MA", 0.05))
        if (
            idle_off > 0.0
            and abs(i_tot) < idle_off
            and i_tot >= t_tot
        ):
            self._pwm.set_duty_unified(0.0)
            return
        if cur > hi:
            step = pwm_ramp_step(0, regulating=True, increasing=False)
            new = max(hi, cur - step)
        elif cur < lo:
            new = lo
        elif i_tot < t_tot:
            step = pwm_ramp_step(0, regulating=True, increasing=True)
            new = min(cur + step, hi)
        elif i_tot > t_tot * 1.05:
            step = pwm_ramp_step(0, regulating=True, increasing=False)
            new = max(lo, cur - step)
        else:
            new = cur
        self._pwm.set_duty_unified(new)

    def update(
        self,
        readings: dict[int, dict],
    ) -> tuple[list[str], bool]:
        self._faults = []
        self._check_clear_fault()
        self._check_clear_fault_channel()

        if self._thermal_pause or self._reference_startup_soak:
            self.all_outputs_off()
            for ch, state in enumerate(self._states):
                r = readings.get(ch, {})
                if state.status == ChannelState.FAULT:
                    self._pwm_zero_for_channel(ch)
                    if state.latch_message:
                        self._faults.append(state.latch_message)
                    self._maybe_auto_clear_fault(ch, state, r)
                elif not r.get("ok"):
                    self._pwm_zero_for_channel(ch)
                    if not self._ina219_idle_benign_ch(ch, readings):
                        extra = ""
                        if "bus_v" in r or "shunt_mv" in r:
                            extra = (
                                f"  last bus_v={r.get('bus_v', '—')}  "
                                f"shunt_mv={r.get('shunt_mv', '—')}"
                            )
                        self._faults.append(
                            f"{anode_hw_label(ch)} READ ERROR: {r.get('error', 'unknown')}{extra}"
                        )
                    if state.status != ChannelState.FAULT:
                        state.status = ChannelState.OPEN
                else:
                    self._pwm_zero_for_channel(ch)
            self._fault_latched = any(
                s.status == ChannelState.FAULT for s in self._states
            )
            return self._faults, self._fault_latched

        protect_ceiling = min(
            float(cfg.DUTY_PROTECT_MAX), float(cfg.PWM_MAX_DUTY)
        )
        staging_ceiling = float(cfg.PWM_MAX_DUTY)
        probe_floor = min(float(cfg.DUTY_PROBE), staging_ceiling)

        failed_reads = [
            ch
            for ch in range(cfg.NUM_CHANNELS)
            if not readings.get(ch, {}).get("ok")
        ]
        suppress_read_faults = (
            bool(failed_reads)
            and not self._any_drive_intent(readings)
            and all(
                readings.get(ch, {}).get("ok")
                or self._ina219_idle_benign_ch(ch, readings)
                for ch in range(cfg.NUM_CHANNELS)
            )
        )
        # Bus-level classification (docs/iccp-requirements.md §4.3 / Q8): aggregate fail-safe
        # fires only when ≥ INA219_FAILSAFE_MIN_BUS_CHANNELS channels report errno-5/121
        # style failures in the same tick. Below the threshold — e.g. one flaky INA219 —
        # the channel falls through to the per-channel branch and siblings keep regulating.
        bus_level_failed = [
            ch for ch in failed_reads if _bus_level_read_failure(readings.get(ch, {}))
        ]
        bus_threshold = max(1, int(getattr(cfg, "INA219_FAILSAFE_MIN_BUS_CHANNELS", 2)))
        if (
            failed_reads
            and bool(getattr(cfg, "INA219_FAILSAFE_ALL_OFF", True))
            and not suppress_read_faults
            and len(bus_level_failed) >= bus_threshold
        ):
            # Do not regulate a subset of channels while others are unreadable (unsafe).
            self._pwm.all_off()
            for ch, state in enumerate(self._states):
                r = readings.get(ch, {})
                if state.status == ChannelState.FAULT:
                    self._pwm_zero_for_channel(ch)
                    if state.latch_message:
                        self._faults.append(state.latch_message)
                    self._maybe_auto_clear_fault(ch, state, r)
                    continue
                state.overcurrent_streak = 0
                if not r.get("ok"):
                    extra = ""
                    if "bus_v" in r or "shunt_mv" in r:
                        extra = (
                            f"  last bus_v={r.get('bus_v', '—')}  "
                            f"shunt_mv={r.get('shunt_mv', '—')}"
                        )
                    self._faults.append(
                        f"{anode_hw_label(ch)} READ ERROR: {r.get('error', 'unknown')}{extra}"
                    )
                if state.status != ChannelState.FAULT:
                    state.status = ChannelState.OPEN
            chs = ", ".join(anode_label(c) for c in failed_reads)
            self._faults.append(
                "Outputs: all channels forced OPEN (0% PWM) — "
                f"sensor read failed on {chs}; fix I2C before regulating."
            )
            self._fault_latched = any(
                s.status == ChannelState.FAULT for s in self._states
            )
            return self._faults, self._fault_latched

        use_bank = _shared_return_pwm()
        bank_rows: list[dict] = [] if use_bank else []

        for ch, state in enumerate(self._states):
            r = readings.get(ch, {})

            if state.status == ChannelState.FAULT:
                self._pwm_zero_for_channel(ch)
                if state.latch_message:
                    self._faults.append(state.latch_message)
                self._maybe_auto_clear_fault(ch, state, r)
                continue

            if not cfg.is_channel_active(ch):
                state.overcurrent_streak = 0
                if state.status != ChannelState.FAULT:
                    state.status = ChannelState.OPEN
                self._pwm.set_duty(ch, 0.0)
                if use_bank:
                    continue
                continue

            if not r.get("ok"):
                state.overcurrent_streak = 0
                self._pwm_zero_for_channel(ch)
                if not self._ina219_idle_benign_ch(ch, readings):
                    extra = ""
                    if "bus_v" in r or "shunt_mv" in r:
                        extra = (
                            f"  last bus_v={r.get('bus_v', '—')}  "
                            f"shunt_mv={r.get('shunt_mv', '—')}"
                        )
                    self._faults.append(
                        f"{anode_hw_label(ch)} READ ERROR: {r.get('error', 'unknown')}{extra}"
                    )
                if state.status != ChannelState.FAULT:
                    state.status = ChannelState.OPEN
                continue

            current_ma = float(r["current"])
            bus_v = float(r["bus_v"])
            duty_cap = duty_pct_cap_for_vcell(bus_v, cfg)
            probe_duty = min(probe_floor, duty_cap)

            if current_ma > self._channel_max_ma(ch):
                state.overcurrent_streak += 1
                need = max(1, int(getattr(cfg, "OVERCURRENT_LATCH_TICKS", 1)))
                if state.overcurrent_streak >= need:
                    self._latch_fault(
                        ch,
                        f"{anode_hw_label(ch)} OVERCURRENT: {current_ma:.4f} mA (max {self._channel_max_ma(ch)} mA)",
                    )
                    state.overcurrent_streak = 0
                continue
            state.overcurrent_streak = 0
            if bus_v < cfg.MIN_BUS_V:
                self._latch_fault(
                    ch,
                    f"{anode_hw_label(ch)} UNDERVOLTAGE: {bus_v:.2f} V (min {cfg.MIN_BUS_V} V)",
                )
                continue
            if bus_v > cfg.MAX_BUS_V:
                self._latch_fault(
                    ch,
                    f"{anode_hw_label(ch)} OVERVOLTAGE: {bus_v:.2f} V (max {cfg.MAX_BUS_V} V)",
                )
                continue

            now = time.monotonic()
            recheck_s = float(getattr(cfg, "STATE_RECHECK_INTERVAL_S", 10.0))
            if now - state.last_state_recheck_monotonic >= recheck_s:
                state.last_state_recheck_monotonic = now
                state.dry_count = 0
                state.conductive_count = 0
                if bool(getattr(cfg, "STATE_RECHECK_RESET_PROTECT_STREAKS", False)):
                    state.protecting_enter_streak = 0
                    state.protecting_exit_streak = 0

            z_log = cell_impedance_ohm(bus_v, current_ma)
            state._z_window.append(z_log)

            path = classify_path(state, current_ma, bus_v, cfg)
            state._last_path_class = path
            target_ma = self._channel_target(ch)

            enter_d = float(getattr(cfg, "PROTECTING_ENTER_DELTA_MA", 0.2))
            enter_n = int(getattr(cfg, "PROTECTING_ENTER_HOLD_TICKS", 3))
            exit_d = float(getattr(cfg, "PROTECTING_EXIT_DELTA_MA", 0.35))
            exit_n = int(getattr(cfg, "PROTECTING_EXIT_HOLD_TICKS", 3))

            if state.status == ChannelState.OPEN:
                if path != PATH_OPEN:
                    state.status = ChannelState.REGULATE
                    state.protecting_enter_streak = 0
                    state.protecting_exit_streak = 0
            elif state.status == ChannelState.REGULATE:
                if path == PATH_OPEN:
                    state.status = ChannelState.OPEN
                    state.protecting_enter_streak = 0
                    state.protecting_exit_streak = 0
                elif path == PATH_STRONG:
                    if abs(current_ma - target_ma) < enter_d:
                        state.protecting_enter_streak += 1
                    else:
                        state.protecting_enter_streak = 0
                    if state.protecting_enter_streak >= enter_n:
                        state.status = ChannelState.PROTECTING
                        state.protecting_enter_streak = 0
                        state.protecting_exit_streak = 0
                else:
                    state.protecting_enter_streak = 0
            elif state.status == ChannelState.PROTECTING:
                if path == PATH_OPEN:
                    state.status = ChannelState.OPEN
                    state.protecting_enter_streak = 0
                    state.protecting_exit_streak = 0
                else:
                    # Exit PROTECTING only on true OPEN, or weak path *and* current off-target
                    # (weak alone can glitch during PWM edges while I is still fine).
                    bad = path == PATH_OPEN or (
                        path == PATH_WEAK and abs(current_ma - target_ma) > exit_d
                    )
                    if bad:
                        state.protecting_exit_streak += 1
                    else:
                        state.protecting_exit_streak = 0
                    if state.protecting_exit_streak >= exit_n:
                        state.status = ChannelState.REGULATE
                        state.protecting_exit_streak = 0
                        state.protecting_enter_streak = 0

            status = state.status
            hi_ramp = min(staging_ceiling, duty_cap)
            hi_protect = min(protect_ceiling, duty_cap)

            if use_bank:
                bank_rows.append(
                    {
                        "ch": ch,
                        "status": status,
                        "current_ma": current_ma,
                        "bus_v": bus_v,
                    }
                )
                continue

            current_duty = self._pwm.duty(ch)
            if status == ChannelState.OPEN:
                state._feedforward_done = False
                self._pwm.set_duty(ch, 0.0)
            elif status == ChannelState.REGULATE:
                lo, hi = probe_duty, hi_ramp
                if current_duty < lo:
                    applied_ff = False
                    if bool(getattr(cfg, "FEEDFORWARD_ENABLED", False)) and (
                        not state._feedforward_done
                    ):
                        # Use rolling median Z when the window is filled; use this-tick
                        # branch Z at DUTY_PROBE when fewer than 3 samples (Algorithm A:
                        # path opens with probe current, Z = V/I exists before 3-tick median).
                        zm = self.median_impedance_ohm(ch)
                        if zm is None and float(z_log) > 0.0:
                            zm = float(z_log)
                        if zm is not None and float(zm) > 0.0:
                            from iccp_electrolyte import predict_duty_feedforward

                            ff = float(
                                predict_duty_feedforward(
                                    target_ma, bus_v, float(zm)
                                )
                            )
                            _jump = float(
                                getattr(cfg, "FEEDFORWARD_MAX_DUTY_JUMP_PCT", 0.0) or 0.0
                            )
                            if _jump > 0.0:
                                ff = min(ff, float(current_duty) + _jump)
                            ff = max(float(lo), min(ff, float(hi)))
                            if ff > float(lo) + 1e-6:
                                self._pwm.set_duty(
                                    ch, _quantize_duty_for_gpio(ff)
                                )
                                applied_ff = True
                    if not applied_ff:
                        # Reach DUTY_PROBE first; a prior idle check here could keep 0% with I=0.
                        self._pwm.set_duty(ch, lo)
                    state._feedforward_done = True
                    continue
                idle_off = float(getattr(cfg, "REGULATE_IDLE_OFF_BELOW_MA", 0.05))
                if (
                    idle_off > 0.0
                    and abs(current_ma) < idle_off
                    and current_ma >= target_ma
                ):
                    # |I| is noise and we are at/above setpoint — hold off (open-path guard).
                    # If current_ma < target_ma, do not block ramp that establishes conduction.
                    self._pwm.set_duty(ch, 0.0)
                    continue
                if current_duty > hi:
                    step = pwm_ramp_step(ch, regulating=True, increasing=False)
                    new_duty = max(hi, current_duty - step)
                elif current_ma < target_ma:
                    step = pwm_ramp_step(ch, regulating=True, increasing=True)
                    new_duty = min(current_duty + step, hi)
                elif current_ma > target_ma * 1.05:
                    step = pwm_ramp_step(ch, regulating=True, increasing=False)
                    new_duty = max(lo, current_duty - step)
                else:
                    new_duty = current_duty
                _kp = float(getattr(cfg, "FEEDBACK_KP", 0.0) or 0.0)
                if _kp > 0.0:
                    new_duty = new_duty + _kp * (target_ma - current_ma)
                self._pwm.set_duty(ch, _quantize_duty_for_gpio(new_duty))
            elif status == ChannelState.PROTECTING:
                if current_ma < target_ma:
                    new_duty = current_duty + pwm_ramp_step(
                        ch, regulating=False, increasing=True
                    )
                elif current_ma > target_ma * 1.05:
                    new_duty = current_duty - pwm_ramp_step(
                        ch, regulating=False, increasing=False
                    )
                else:
                    new_duty = current_duty
                _kp = float(getattr(cfg, "FEEDBACK_KP", 0.0) or 0.0)
                if _kp > 0.0:
                    new_duty = new_duty + _kp * (target_ma - current_ma)
                self._pwm.set_duty(ch, clamp(new_duty, 0.0, hi_protect))

        if use_bank:
            if any(s.status == ChannelState.FAULT for s in self._states):
                self._pwm.set_duty_unified(0.0)
            else:
                self._apply_unified_bank_pwm(
                    bank_rows,
                    protect_ceiling=protect_ceiling,
                    staging_ceiling=staging_ceiling,
                    probe_floor=probe_floor,
                )

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
          OVERCURRENT_RECOVERY_THRESHOLD (90% of MAX_MA by default).
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
                    f"[control] {anode_label(ch)} OVERCURRENT recovered "
                    f"({current_ma:.4f} mA < {recovery_threshold:.2f} mA): "
                    f"clearing fault"
                )
                state.status = ChannelState.OPEN
                state.latch_message = ""
                state.overcurrent_streak = 0
                state.fault_retry_count += 1
                return
            # Current still elevated — don't fall through to timed retry
            return

        retry_interval = getattr(cfg, "FAULT_RETRY_INTERVAL_S", 60.0)
        elapsed = time.monotonic() - state.fault_time
        if elapsed < retry_interval:
            return

        # Time to retry — clear fault and return to OPEN for re-classification
        print(
            f"[control] {anode_label(ch)} auto-retry "
            f"({state.fault_retry_count + 1}/{max_retries}): clearing fault"
        )
        state.status = ChannelState.OPEN
        state.latch_message = ""
        state.overcurrent_streak = 0
        state.fault_retry_count += 1

    def any_overprotected(self) -> bool:
        """True if any channel is in ``Overprotected`` (shift FSM is already backing off duty)."""
        return any(s.state_v2 == STATE_V2_OVERPROTECTED for s in self._states)

    def _can_outer_potential_nudge_now(self, force: bool) -> bool:
        if force:
            return True
        min_s = float(getattr(cfg, "OUTER_LOOP_POTENTIAL_MIN_S", 0.0) or 0.0)
        if min_s <= 0.0:
            return True
        last = self._last_outer_potential_nudge_s
        if last is None:
            return True
        return (time.monotonic() - last) >= min_s

    def _mark_outer_potential_nudge(self) -> None:
        self._last_outer_potential_nudge_s = time.monotonic()

    def update_potential_target(
        self,
        shift_mv: float | None,
        *,
        shift_target_mv: float | None = None,
        shift_max_mv: float | None = None,
        force: bool = False,
    ) -> None:
        """
        Outer loop: nudge TARGET_MA to keep polarization in the safe window.
        **Positive** shift means the mV reading **increased** under CP
        vs OCP (industry: ref on DVM +, structure on DVM −).

        ``shift_mv`` may be **live** (on-PWM) or **instant-off** (IR-free). The runtime calls this
        with live shift every :data:`~config.SAMPLE_INTERVAL_S` when in temperature band, subject
        to :data:`~config.OUTER_LOOP_POTENTIAL_MIN_S`, and on each :data:`~config.LOG_INTERVAL_S` tick
        with instant-off and ``force=True`` (bypasses the rate limit).

        No-ops when any channel is Overprotected (shift FSM reduces duty; avoid fighting it).

        **Below mV target:** nudges :data:`config.settings.TARGET_MA` up when
        ``shift < effective target`` (same as ``UNDER`` in :class:`~reference.ReferenceElectrode`).
        **Above mV over-max:** nudges down when over band. **Trim:** with
        ``OUTER_LOOP_TRIM_TO_SHIFT_CENTER`` True, nudges down from the high side of the
        in-band range when ``shift > center + OUTER_LOOP_SHIFT_TRIM_TOL_MV`` (e.g. toward center).

        ``shift_target_mv`` / ``shift_max_mv`` default to :obj:`config.settings.TARGET_SHIFT_MV` /
        ``MAX_SHIFT_MV``. When Phase 1b (galvanic) is commissioned, the reference layer should pass
        the **additional** target from the 1b baseline so total polarization from true native(1a)
        still matches ``TARGET_SHIFT_MV``.
        """
        if shift_mv is None:
            return
        if self.any_overprotected():
            return

        center = (
            float(shift_target_mv)
            if shift_target_mv is not None
            else float(cfg.TARGET_SHIFT_MV)
        )
        hi = float(shift_max_mv) if shift_max_mv is not None else float(cfg.MAX_SHIFT_MV)
        trim_tol = float(getattr(cfg, "OUTER_LOOP_SHIFT_TRIM_TOL_MV", 3.0))
        step = float(cfg.TARGET_MA_STEP)
        max_target = float(cfg.MAX_MA) * 0.8
        nudge_ok = self._can_outer_potential_nudge_now(force)

        # Use the same "below setpoint" test as :meth:`ReferenceElectrode.protection_status` (UNDER):
        # increase mA setpoint when shift is below the effective mV target — not 0.8×target, which
        # left a dead band where the UI was UNDER but OUTER_LOOP_TRIM_TO_SHIFT_CENTER=False froze
        # TARGET_MA. Optional trim nudges down from the high side of the [center, hi] window.
        from iccp_electrolyte import effective_target_ma_floor

        floor_m = float(effective_target_ma_floor())
        if shift_mv < float(center) - 1e-6 and nudge_ok:
            cfg.TARGET_MA = round(min(cfg.TARGET_MA + step, max_target), 3)
            self._mark_outer_potential_nudge()
        elif shift_mv > float(hi) + 1e-6 and nudge_ok:
            cfg.TARGET_MA = round(max(cfg.TARGET_MA - step, floor_m), 3)
            self._mark_outer_potential_nudge()
        elif bool(getattr(cfg, "OUTER_LOOP_TRIM_TO_SHIFT_CENTER", True)) and nudge_ok:
            if shift_mv > float(center) + float(trim_tol):
                cfg.TARGET_MA = round(max(cfg.TARGET_MA - step, floor_m), 3)
                self._mark_outer_potential_nudge()

    def duties(self) -> dict[int, float]:
        return {i: self._pwm.duty(i) for i in range(cfg.NUM_CHANNELS)}

    def channel_statuses(self) -> dict[int, str]:
        return {i: self._states[i].status for i in range(cfg.NUM_CHANNELS)}

    def channel_path_tags(self) -> dict[int, str]:
        """Short path-quality label from last classify_path (for verbose UI)."""
        tags: dict[int, str] = {}
        for i, s in enumerate(self._states):
            if s.status == ChannelState.FAULT:
                tags[i] = "—"
                continue
            p = s._last_path_class
            if p == PATH_OPEN:
                tags[i] = "open"
            elif p == PATH_STRONG:
                tags[i] = "strong"
            else:
                tags[i] = "weak"
        return tags

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
        self.all_outputs_off()

    def cleanup(self) -> None:
        self._pwm.cleanup()

    def channel_target_ma(self, ch: int) -> float:
        """Per-channel protection current setpoint (mA); same rule as internal classify."""
        return self._channel_target(ch)

    def _channel_target(self, ch: int) -> float:
        from iccp_electrolyte import effective_target_ma_floor

        t = float(getattr(cfg, "CHANNEL_TARGET_MA", {}).get(ch, cfg.TARGET_MA))
        return max(t, effective_target_ma_floor())

    def _channel_max_ma(self, ch: int) -> float:
        return float(getattr(cfg, "CHANNEL_MAX_MA", {}).get(ch, cfg.MAX_MA))

    def _latch_fault(self, ch: int, msg: str) -> None:
        if _shared_return_pwm():
            self._pwm.set_duty_unified(0.0)
        else:
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
            self._clear_channel_fault(state)
        self._fault_latched = False

    def _check_clear_fault_channel(self) -> None:
        """Drain per-channel clear side channel (docs/iccp-requirements.md §6.2).

        File is written atomically as JSON: ``{"channel": N, "ts": <unix>}`` (CLI
        `iccp clear-fault --channel N`). Plain-text ``N`` is also accepted for simplicity.
        Consumed once per call — the file is deleted whether parsing succeeds or not so a
        malformed payload cannot wedge the loop. N is 0-based (matches `anode_label`).
        """
        path = getattr(cfg, "CLEAR_FAULT_CHANNEL_FILE", None)
        if path is None or not path.exists():
            return
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        try:
            path.unlink()
        except OSError:
            pass
        ch: int | None = None
        if raw:
            try:
                import json

                data = json.loads(raw)
                if isinstance(data, dict) and "channel" in data:
                    ch = int(data["channel"])
                elif isinstance(data, int):
                    ch = int(data)
            except (ValueError, TypeError, Exception):
                try:
                    ch = int(raw)
                except ValueError:
                    ch = None
        if ch is None or not (0 <= ch < cfg.NUM_CHANNELS):
            return
        self._clear_channel_fault(self._states[ch])
        self._fault_latched = any(
            s.status == ChannelState.FAULT for s in self._states
        )

    def _clear_channel_fault(self, state: ChannelState) -> None:
        if state.status != ChannelState.FAULT and state.state_v2 != STATE_V2_FAULT:
            return
        state.status = ChannelState.OPEN
        state.latch_message = ""
        state.fault_retry_count = 0
        state.overcurrent_streak = 0
        state.fault_time = 0.0
        state.fault_reason = ""
        state.polarize_retry_count = 0
        state.polarize_retry_next_unix = None
        state.polarize_backoff_until_mono = None
        state.state_v2 = STATE_V2_OFF
        state.state_v2_enter_monotonic = time.monotonic()
        state.shift_above_target_since = None
        state.shift_below_target_since = None
        state.shift_over_max_since = None
        state.shift_under_max_since = None
        state.polarizing_since = None
        state.probing_since = None

    # ---- Shift-based FSM (docs/iccp-requirements.md §2.2 / §5 / §6) ------------------

    def advance_shift_fsm(
        self,
        readings: dict[int, dict],
        *,
        shift_mv: float | None,
        ref_valid: bool,
        ref_valid_reason: str = "",
        shift_target_mv: float | None = None,
        shift_max_mv: float | None = None,
    ) -> None:
        """Advance `state_v2` for every channel. Call once per tick AFTER `update()`.

        This runs alongside the legacy path FSM: it does not touch PWM duty except in
        Overprotected (where it ramps duty down to reduce polarization) and when it
        latches CANNOT_POLARIZE / OVERPROTECTION faults (which zero duty via
        :meth:`_latch_fault`). All timers are per-channel and driven by `time.monotonic`.

        ``shift_target_mv`` / ``shift_max_mv`` default to ``TARGET_SHIFT_MV`` / ``MAX_SHIFT_MV``,
        which define the **protected band** ``[target, over_max]`` mV (defaults 100–200 mV).
        **Protected** (``STATE_V2_PROTECTED``) means shift has entered and remains in that band
        (per slip / over hysteresis). **Polarizing → Protected** requires shift in that band
        (not only at/above *target*); if shift overshoots above *over_max* while polarizing,
        the state moves to **Overprotected** immediately.

        When galvanic 1b is used, pass the **additional** shift from the 1b baseline from
        :meth:`ReferenceElectrode.effective_shift_target_mv` (and
        :meth:`ReferenceElectrode.effective_max_shift_mv`) so the total from true native(1a)
        still matches the configured band.
        """
        now = time.monotonic()
        target = (
            float(shift_target_mv)
            if shift_target_mv is not None
            else float(getattr(cfg, "TARGET_SHIFT_MV", 100))
        )
        over_max = (
            float(shift_max_mv)
            if shift_max_mv is not None
            else float(getattr(cfg, "MAX_SHIFT_MV", 200))
        )
        hy_exit = float(getattr(cfg, "HYST_PROT_EXIT_MV", 10.0))
        hy_over_exit = float(getattr(cfg, "HYST_OVER_EXIT_MV", 10.0))
        hy_over_fault = float(getattr(cfg, "HYST_OVER_FAULT_MV", 50.0))
        t_pol_stable = float(getattr(cfg, "T_POL_STABLE", 30.0))
        t_slip = float(getattr(cfg, "T_SLIP", 10.0))
        t_pol_max = float(getattr(cfg, "T_POLARIZE_MAX", 600.0))
        t_over_fault = float(getattr(cfg, "T_OVER_FAULT", 60.0))
        t_over_exit = float(getattr(cfg, "T_OVER_EXIT", 15.0))
        retry_max = int(getattr(cfg, "POLARIZE_RETRY_MAX", 2))
        retry_interval = float(getattr(cfg, "POLARIZE_RETRY_INTERVAL_S", t_pol_max))

        def _set_state(state: ChannelState, new: str) -> None:
            if state.state_v2 != new:
                state.state_v2 = new
                state.state_v2_enter_monotonic = now
                if new != STATE_V2_POLARIZING:
                    state.polarizing_since = None
                if new != STATE_V2_PROBING:
                    state.probing_since = None

        for ch, state in enumerate(self._states):
            r = readings.get(ch, {})
            state.last_shift_mv = shift_mv

            if not cfg.is_channel_active(ch):
                _set_state(state, STATE_V2_OFF)
                continue

            # Legacy FAULT already holds PWM at 0%; mirror into state_v2 and skip timers.
            if state.status == ChannelState.FAULT:
                _set_state(state, STATE_V2_FAULT)
                state.shift_above_target_since = None
                state.shift_below_target_since = None
                state.shift_over_max_since = None
                state.shift_under_max_since = None
                continue

            # Reference invalid takes precedence over shift — we cannot prove protection.
            if not ref_valid and state.state_v2 != STATE_V2_OFF:
                # Don't hard-latch: switch to Fault with reason for visibility (§6.1).
                if state.state_v2 != STATE_V2_FAULT:
                    state.fault_reason = f"REFERENCE_INVALID:{ref_valid_reason or 'unknown'}"
                    _set_state(state, STATE_V2_FAULT)
                continue

            # Measurable current decides Probing → Polarizing / Off transitions.
            r_ok = bool(r.get("ok"))
            i_ma = float(r.get("current", 0.0) or 0.0) if r_ok else 0.0
            duty = self._pwm.duty(0) if _shared_return_pwm() else self._pwm.duty(ch)
            probe_ma = float(getattr(cfg, "CHANNEL_DRY_MA", 0.1))
            driving = duty >= float(getattr(cfg, "PWM_MIN_DUTY", 0.01)) and r_ok

            # --- Transitions per state_v2 ---
            if state.state_v2 == STATE_V2_OFF:
                if driving and i_ma > probe_ma:
                    state.probing_since = now
                    _set_state(state, STATE_V2_PROBING)
                continue

            if state.state_v2 == STATE_V2_PROBING:
                if not driving:
                    _set_state(state, STATE_V2_OFF)
                    continue
                # CANNOT_POLARIZE retry: wait POLARIZE_RETRY_INTERVAL_S after a failed window (§6.1 Q4).
                bo = state.polarize_backoff_until_mono
                if bo is not None and now < bo:
                    continue
                if bo is not None and now >= bo:
                    state.polarize_backoff_until_mono = None
                state.probing_since = state.probing_since or now
                # Move to Polarizing as soon as we have a stable non-zero current and
                # shift is measurable (non-None).
                if shift_mv is not None and i_ma > probe_ma:
                    state.polarizing_since = now
                    _set_state(state, STATE_V2_POLARIZING)
                continue

            if state.state_v2 == STATE_V2_POLARIZING:
                if not driving:
                    _set_state(state, STATE_V2_OFF)
                    continue
                if shift_mv is None:
                    continue
                if shift_mv > over_max:
                    # Overshoot: above protected band (target..over_max) → cut back, not
                    # "in band" for Protected.
                    state.shift_above_target_since = None
                    _set_state(state, STATE_V2_OVERPROTECTED)
                    continue
                if target <= shift_mv <= over_max:
                    if state.shift_above_target_since is None:
                        state.shift_above_target_since = now
                    if now - state.shift_above_target_since >= t_pol_stable:
                        state.polarize_retry_count = 0
                        state.polarize_retry_next_unix = None
                        _set_state(state, STATE_V2_PROTECTED)
                    continue
                state.shift_above_target_since = None
                pol_t0 = state.polarizing_since or state.state_v2_enter_monotonic
                if now - pol_t0 >= t_pol_max:
                    # Exceeded polarize window without reaching target → CANNOT_POLARIZE.
                    state.polarize_retry_count += 1
                    if state.polarize_retry_count > retry_max:
                        state.fault_reason = "CANNOT_POLARIZE:retry_exhausted"
                        _latch_msg = (
                            f"{anode_hw_label(ch)} CANNOT_POLARIZE: "
                            f"shift {shift_mv:.1f} mV < {target:.0f} mV after "
                            f"{t_pol_max:.0f}s × {retry_max} retries"
                        )
                        self._latch_fault(ch, _latch_msg)
                        _set_state(state, STATE_V2_FAULT)
                    else:
                        state.polarize_backoff_until_mono = now + retry_interval
                        state.polarize_retry_next_unix = None
                        state.polarizing_since = None
                        state.shift_above_target_since = None
                        state.probing_since = now
                        _set_state(state, STATE_V2_PROBING)
                continue

            if state.state_v2 == STATE_V2_PROTECTED:
                if not driving:
                    _set_state(state, STATE_V2_OFF)
                    continue
                if shift_mv is None:
                    continue
                if shift_mv > over_max:
                    state.shift_over_max_since = state.shift_over_max_since or now
                    state.shift_under_max_since = None
                    _set_state(state, STATE_V2_OVERPROTECTED)
                    continue
                if shift_mv < target - hy_exit:
                    state.shift_below_target_since = state.shift_below_target_since or now
                    if now - state.shift_below_target_since >= t_slip:
                        state.polarizing_since = now
                        state.shift_above_target_since = None
                        _set_state(state, STATE_V2_POLARIZING)
                else:
                    state.shift_below_target_since = None
                continue

            if state.state_v2 == STATE_V2_OVERPROTECTED:
                if shift_mv is None:
                    continue
                # Potential-control duty ramp down (§5.3): reduce duty a bit each tick
                # while overprotected. Keep it gentle — PROTECTING step is already small.
                new_duty = max(
                    0.0,
                    duty
                    - pwm_ramp_step(ch, regulating=False, increasing=False),
                )
                if _shared_return_pwm():
                    self._pwm.set_duty_unified(new_duty)
                else:
                    self._pwm.set_duty(ch, new_duty)
                # OVERPROTECTION fault when shift > over_max + HYST_OVER_FAULT for T_OVER_FAULT.
                if shift_mv > over_max + hy_over_fault:
                    state.shift_over_max_since = state.shift_over_max_since or now
                    if now - state.shift_over_max_since >= t_over_fault:
                        state.fault_reason = "OVERPROTECTION:shift_exceeds_max_band"
                        msg = (
                            f"{anode_hw_label(ch)} OVERPROTECTION: "
                            f"shift {shift_mv:.1f} mV > {over_max + hy_over_fault:.0f} mV "
                            f"sustained {t_over_fault:.0f}s"
                        )
                        self._latch_fault(ch, msg)
                        _set_state(state, STATE_V2_FAULT)
                    continue
                # Exit when shift drops back under MAX − HYST_OVER_EXIT for T_OVER_EXIT.
                if shift_mv < over_max - hy_over_exit:
                    state.shift_under_max_since = state.shift_under_max_since or now
                    if now - state.shift_under_max_since >= t_over_exit:
                        state.shift_over_max_since = None
                        state.shift_under_max_since = None
                        state.shift_above_target_since = None
                        if target <= shift_mv <= over_max:
                            _set_state(state, STATE_V2_PROTECTED)
                        elif shift_mv < target:
                            state.polarizing_since = now
                            _set_state(state, STATE_V2_POLARIZING)
                        else:
                            _set_state(state, STATE_V2_OVERPROTECTED)
                else:
                    state.shift_under_max_since = None
                continue

            if state.state_v2 == STATE_V2_FAULT:
                # Retry window handled by legacy _maybe_auto_clear_fault + polarize retry
                # scheduling (main loop consumes polarize_retry_next_unix).
                continue

    def t_in_state_v2_s(self, ch: int) -> float:
        s = self._states[ch]
        return max(0.0, time.monotonic() - s.state_v2_enter_monotonic)

    def t_in_polarizing_s(self, ch: int) -> float:
        """Seconds in the current `Polarizing` run (since ``polarizing_since``), or 0.0 if not polarizing."""
        s = self._states[ch]
        if s.state_v2 != STATE_V2_POLARIZING or s.polarizing_since is None:
            return 0.0
        return max(0.0, time.monotonic() - float(s.polarizing_since))

    def all_protected(self) -> bool:
        """True when every *participating* active channel is `Protected` (shift in
        ``[TARGET_SHIFT_MV, MAX_SHIFT_MV]`` by FSM design) for ``T_SYSTEM_STABLE`` (§2.2).

        Active channels still ``Off`` (no path / dry) are excluded. An active channel in
        ``Fault`` blocks the assertion until cleared.
        """
        tss = float(getattr(cfg, "T_SYSTEM_STABLE", 60.0))
        now = time.monotonic()
        if not self._states:
            return False
        participating: list[ChannelState] = []
        for ch, s in enumerate(self._states):
            if not cfg.is_channel_active(ch):
                continue
            if s.state_v2 == STATE_V2_FAULT:
                self._all_protected_streak_mono = None
                return False
            if s.state_v2 == STATE_V2_OFF:
                continue
            participating.append(s)
        if not participating:
            self._all_protected_streak_mono = None
            return False
        if not all(s.state_v2 == STATE_V2_PROTECTED for s in participating):
            self._all_protected_streak_mono = None
            return False
        if self._all_protected_streak_mono is None:
            self._all_protected_streak_mono = now
        if now - self._all_protected_streak_mono < tss:
            return False
        if self._first_all_protected_wall is None:
            self._first_all_protected_wall = time.time()
        return True

    def t_to_system_protected_s(self) -> float | None:
        """Wall seconds from boot until `all_protected` first became True; None if never."""
        if self._first_all_protected_wall is None:
            return None
        return max(0.0, self._first_all_protected_wall - self._boot_wall_time)

    def any_active(self) -> bool:
        """True when any channel is outside `Off` and `Fault` — used by legacy `wet`."""
        active = {STATE_V2_PROBING, STATE_V2_POLARIZING, STATE_V2_PROTECTED, STATE_V2_OVERPROTECTED}
        return any(s.state_v2 in active for s in self._states)

    def channel_state_v2(self) -> dict[int, str]:
        return {i: s.state_v2 for i, s in enumerate(self._states)}

    def channel_fault_reasons(self) -> dict[int, str]:
        return {i: s.fault_reason for i, s in enumerate(self._states)}
