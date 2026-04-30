"""
CoilShield — single source of truth for all tunables.
Import as: import config.settings as cfg  (never from config.settings import *)
"""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_log_dir(project_root: Path, environ: dict[str, str] | None = None) -> Path:
    """
    Single place for telemetry directory: controller, dashboard, CLI, and tests.

    Override with absolute path (recommended) or path relative to PROJECT_ROOT:
      COILSHIELD_LOG_DIR=/var/lib/iccp/logs
      ICCP_LOG_DIR=...   (alias)

    If unset, defaults to ``<project>/logs`` (same layout as a git checkout).
    """
    env = os.environ if environ is None else environ
    raw = (env.get("COILSHIELD_LOG_DIR") or env.get("ICCP_LOG_DIR") or "").strip()
    if not raw:
        return (project_root / "logs").resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = project_root / p
    return p.resolve()


# --- Project paths ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = _resolve_log_dir(PROJECT_ROOT)
CLEAR_FAULT_FILE = PROJECT_ROOT / "clear_fault"

# --- I2C ---
I2C_BUS = 1
# Reference INA219 / legacy ref I2C bus. Default **1** (shared header bus with anodes).
# For an isolated gpio bit-bang bus set **3** after adding to `/boot/firmware/config.txt`:
#   dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=20,i2c_gpio_scl=12
REF_I2C_BUS = 1

# INA list length must match NUM_CHANNELS (firmware Anode 1..N = idx 0..N-1).
#
# **Default: four anode INA219s** at 0x40, 0x41, 0x44, 0x45 with `PWM_GPIO_PINS` below.
# **Fallback (dead INA / three cells only):** use three addresses and `NUM_CHANNELS = 3`, e.g.
# `[0x41, 0x44, 0x45]` and `(27, 22, 23)` for the remaining gates — see
# docs/ina219-i2c-bringup.md (fewer than four INA219s).
INA219_ADDRESSES = [0x40, 0x41, 0x44, 0x45]
NUM_CHANNELS = 4

# ADS1115 reference ADC (header I2C; optional TCA9548A via I2C_MUX_*).
# I²C 7-bit overlap: ADS1115 ADDR pin selects 0x48-0x4B; INA219 A0/A1 can strap 0x48-0x4F.
# Default anodes (0x40-0x45) avoid colliding with default ADS1115 @ 0x48 — do not place another
# device at the same address on the same downstream bus segment (e.g. a fifth INA219 at 0x48).
ADS1115_ADDRESS = 0x48
ADS1115_BUS = 1
ADS1115_CHANNEL = 0
# If True, read the ADS1115 as a **differential** measurement (AIN+ − AIN−) instead of
# single-ended AINn vs GND. Use only when AIN− is **actually** tied to the intended sense
# node; a floating AIN1 picks up stray V and makes (AIN0−AIN1) meaningless.
# **Default False:** Ag/AgCl on AIN0, ADS1115 GND on the controller GND rail (plier / bath
# return on that same rail). Do not connect a second “plier” lead to AIN1 unless you are
# deliberately using differential mode with correct wiring.
ADS1115_DIFFERENTIAL = False
# Differential pair only supports certain ADS1115 mux combos; (0,1) is the common one.
ADS1115_DIFF_POS_CHANNEL = 0
ADS1115_DIFF_NEG_CHANNEL = 1
# TI PGA full-scale — must match the programmed range or every mV is scaled wrong vs a DMM.
# ±2.048 V is typical for Ag/AgCl + divider when the AIN node stays below ~2 V; use ±4.096 only
# if the front-end can exceed ±2.048 V (see COILSHIELD_ADS1115_FSR_V).
ADS1115_FSR_V = 2.048
# BCM pin for ADS1115 ALERT/RDY (conversion-ready, active low). None = poll config register only.
ADS1115_ALRT_GPIO: int | None = 24
# If True, try RPi.GPIO wait_for_edge on ALRT after starting a conversion; on failure
# firmware falls back to polled OS bit + sleep. Default False avoids Bookworm / 6.x kernel
# spam and matches safe polling; set True if you use `rpi-lgpio` and want ALRT edges.
# Stock RPi.GPIO often raises RuntimeError: Error waiting for edge — install `rpi-lgpio`
# (same import name) or keep False and rely on OS polling below.
ADS1115_ALRT_USE_WAIT_FOR_EDGE = False
# When True (default), do not print DEBUG when edge wait is skipped because OS was already set.
ADS1115_ALRT_SUPPRESS_EDGE_SKIP_LOG = True
# Poll interval (s) while waiting on config register OS after single-shot (ALRT path).
# This is the authoritative completion check; ALRT edges are optional (TI pulse ~µs).
ADS1115_OS_POLL_INTERVAL_S = 0.0003
# Median of N single-ended reads per sample (noise on long leads / PWM pickup).
REF_ADS_MEDIAN_SAMPLES = 5
# Data rate bits 0..7 for routine reference reads (5 = 250 SPS). Commissioning curve uses COMMISSIONING_ADS1115_DR.
REF_ADS1115_DR = 5
# At import and on first read if needed: open ADS1115 and retry (helps errno 5 EIO / busy I²C bus).
REF_ADS1115_INIT_MAX_ATTEMPTS: int = 12
REF_ADS1115_INIT_RETRY_DELAY_S: float = 0.12
# Anode INA219 init at import: per-channel retries (mux + pi-ina219 configure) on errno 5/121.
INA219_INIT_MAX_ATTEMPTS: int = 8
INA219_INIT_RETRY_DELAY_S: float = 0.1
# Optional first-open settle before any INA import I/O (0 = off). Try 0.02 if first touch often EIOs.
I2C_INA_IMPORT_FIRST_DELAY_S: float = 0.0
# If import-time INA init left ``_sensors`` empty, re-run full init at most this often (read_all_real).
INA219_REINIT_MIN_INTERVAL_S: float = 60.0
# Multiply ADC volts (after ×1000) for divider scaling vs. electrode node.
# Calibrate with a DMM at the ADS1115 AIN node (same ground): at fixed PWM state,
#   REF_ADS_SCALE ≈ V_dmm / (ref_raw_mv / 1000).
# Example: meter 0.120 V but ref_raw_mv ≈ 300 → scale ≈ 0.40.
# Optional env: COILSHIELD_REF_ADS_SCALE=0.4
# Optional FSR env (TI PGA only): COILSHIELD_ADS1115_FSR_V=2.048
# commissioning.json key ``ref_ads_scale`` overrides the below at runtime (see reference.py).
REF_ADS_SCALE = 1.0
_ADS1115_FSR_ALLOWED = (6.144, 4.096, 2.048, 1.024, 0.512, 0.256)
_rs_env = os.environ.get("COILSHIELD_REF_ADS_SCALE", "").strip()
if _rs_env:
    REF_ADS_SCALE = float(_rs_env)
_fsr_env = os.environ.get("COILSHIELD_ADS1115_FSR_V", "").strip()
if _fsr_env:
    _v = float(_fsr_env)
    if any(abs(_v - _k) < 1e-9 for _k in _ADS1115_FSR_ALLOWED):
        ADS1115_FSR_V = _v

# Reference backend: "ads1115" (default) or legacy "ina219" on REF_I2C_BUS.
REF_ADC_BACKEND = "ads1115"
# Field default Ag/AgCl sense; legacy bench Cu was ``copper_bench`` — informational for logs/docs.
REF_ELECTRODE_KIND = "ag_agcl"

# --- Product SKU / metal profile (absolute potential envelope; see polarization_safety.py) ---
# ``COILSHIELD_SKU=Al`` (or ``aluminum`` / ``coilshield-al``) enables cathode absolute mV limits.
_sku_env = (os.environ.get("COILSHIELD_SKU") or "generic").strip().lower()
METAL_SKU: str = _sku_env if _sku_env else "generic"
# When True, ``ref_raw_mv`` is compared to ``POLARIZATION_*`` after ``CATHODE_ABSOLUTE_MV_SIGN``.
# Auto-enabled for aluminum-like SKUs unless explicitly forced off.
_abs_env = (os.environ.get("COILSHIELD_CATHODE_ABSOLUTE_POTENTIAL") or "").strip().lower()
if _abs_env in ("1", "true", "yes", "on"):
    CATHODE_ABSOLUTE_POTENTIAL_ENABLED: bool = True
elif _abs_env in ("0", "false", "no", "off"):
    CATHODE_ABSOLUTE_POTENTIAL_ENABLED = False
else:
    CATHODE_ABSOLUTE_POTENTIAL_ENABLED = METAL_SKU in (
        "al",
        "aluminum",
        "coilshield-al",
        "coilshield_al",
    )
# Multiply ``ref_raw_mv`` before absolute checks if wiring/inversion does not match tables.
CATHODE_ABSOLUTE_MV_SIGN: float = 1.0
_sign_env = (os.environ.get("COILSHIELD_CATHODE_ABSOLUTE_MV_SIGN") or "").strip()
if _sign_env:
    CATHODE_ABSOLUTE_MV_SIGN = float(_sign_env)
# Allow absolute-limit checks in COILSHIELD_SIM=1 (bench harness); default off.
CATHODE_ABSOLUTE_SAFETY_IN_SIM: bool = (
    os.environ.get("COILSHIELD_CATHODE_ABSOLUTE_SAFETY_IN_SIM", "0").strip() == "1"
)
# Aluminum vs Ag/AgCl (3M KCl) — Watkins/Davie window; hard cutoff with margin.
PROTECTION_TARGET_MV: float = -1019.0
PROTECTION_WINDOW_MV_LO: float = -1069.0  # alkaline (more negative) bound — do not go past
PROTECTION_WINDOW_MV_HI: float = -969.0  # pitting (less negative) bound — stay at least this negative
POLARIZATION_HARD_CUTOFF_MV: float = -1080.0  # latch all channels; manual clear only
POLARIZATION_FLOOR_WARNING_MV: float = -900.0  # less negative than this → unprotected warning
POLARIZATION_FLOOR_WARNING_DURATION_S: float = 300.0
# If True, commissioning fails Phase 3 when instant-off raw is outside the protection window.
COMMISSIONING_ABSOLUTE_WINDOW_STRICT: bool = False

# --- Fault categories (manual clear vs auto-retry) ---
FAULT_CATEGORY_TRANSIENT: str = "TRANSIENT"
FAULT_CATEGORY_POLARIZATION: str = "POLARIZATION"
FAULT_CATEGORY_CURRENT_CAP: str = "CURRENT_CAP"
FAULT_CATEGORY_SENSOR_FAIL: str = "SENSOR_FAIL"
FAULT_CATEGORY_OVERPROTECTION: str = "OVERPROTECTION"
FAULT_CATEGORY_CANNOT_POLARIZE: str = "CANNOT_POLARIZE"
FAULT_CATEGORIES_MANUAL_CLEAR_ONLY: frozenset[str] = frozenset(
    {
        FAULT_CATEGORY_POLARIZATION,
        FAULT_CATEGORY_CURRENT_CAP,
        FAULT_CATEGORY_SENSOR_FAIL,
        FAULT_CATEGORY_OVERPROTECTION,
        FAULT_CATEGORY_CANNOT_POLARIZE,
    }
)

# Optional TCA9548A when anodes and ADS are on different downstream ports (legacy / alternate PCBs).
# **Default: no mux** — all INA219 + ADS1115 on the same Pi SDA/SCL; unique 7-bit addresses only.
# To use a multiplexer, set ``I2C_MUX_ADDRESS`` and the ``I2C_MUX_CHANNEL_*`` fields in this file
# (e.g. 0x70, INAs on 0..3, ADS on 4) or set env ``COILSHIELD_MUX_ADDRESS=0x70`` and configure
# channel maps below. Env ``COILSHIELD_MUX_ADDRESS=none`` forces all mux fields off if needed.
#   • INA219: ch0..3 at INA219_ADDRESSES; ref ADS on I2C_MUX_CHANNEL_ADS1115.
# Control byte = 1 << port. See docs/ina219-i2c-bringup.md.
I2C_MUX_ADDRESS: int | None = None
I2C_MUX_CHANNEL_ADS1115: int | None = None
# Single downstream port shared by every anode INA219 (ignored if I2C_MUX_CHANNELS_INA219 set).
I2C_MUX_CHANNEL_INA219: int | None = None
# Per anode index 0..NUM_CHANNELS-1: TCA9548A port before that INA219; None = no per-port select.
I2C_MUX_CHANNELS_INA219: tuple[int, ...] | None = None

# Optional env override: enable/disable mux without editing the assignments above.
_mxa = (os.environ.get("COILSHIELD_MUX_ADDRESS") or "").strip()
if _mxa:
    if _mxa.lower() in ("none", "off", "no", "false", "0"):
        I2C_MUX_ADDRESS = None
        I2C_MUX_CHANNEL_ADS1115 = None
        I2C_MUX_CHANNELS_INA219 = None
    else:
        I2C_MUX_ADDRESS = int(_mxa, 0)
# After selecting a mux downstream port, optional settle time before talking to INA219/ADS.
# Non-zero reduces ``[Errno 5] Input/output error`` when switching TCA9548A → ADS1115 / INA219.
# 0.001–0.002 s can help if mux→INA/ADS still EIOs after per-channel INA init retries.
I2C_MUX_POST_SELECT_DELAY_S: float = 0.0005
# Transient I²C errnos retried in mux select, INA import init, reference reads, etc.:
# 5 EIO, 121 EREMOTEIO, 110 ETIMEDOUT (common when SCL is stuck / clock-stretch or bus hangs).
I2C_TRANSIENT_ERRNOS: tuple[int, ...] = (5, 121, 110)
# TCA9548A ``write_byte`` can hit those errnos on shared Pi I²C. Keep attempts LOW (2) for
# the hot read path — each retry adds delay to every control-loop tick. The SMBus-reopen
# fallback in sensors.read_all_real handles persistent failures. Init paths (import,
# commissioning) use INA219_INIT_MAX_ATTEMPTS / REF_ADS1115_INIT_MAX_ATTEMPTS separately.
I2C_MUX_SELECT_MAX_ATTEMPTS: int = 2
I2C_MUX_SELECT_RETRY_DELAY_S: float = 0.003
# If mux select still raises EIO after all retries, ``sensors.read_all_real`` can close
# and reopen the SMBus handle once per channel (Pi kernels sometimes need this after
# a stuck transaction). Set False only for unit tests or unusual bus drivers.
I2C_MUX_SMBUS_REOPEN_ON_SELECT_EIO: bool = True
# ADS1115 i2c_bench.ads1115_read_single_ended (probe STEP 3, reference reads): full
# sequence retry on ``I2C_TRANSIENT_ERRNOS`` (each attempt re-starts the conversion).
ADS1115_SMBUS_READ_MAX_ATTEMPTS: int = 4
# Bus-level I²C failure policy (see docs/iccp-requirements.md §4.3, Decision Q8).
# When True, a **bus-level** INA219 read failure (OSError / errno 5 or equivalent — the
# whole I²C bus is unhealthy) forces every non-FAULT channel to 0% PWM. Per-channel
# transients that are not bus-level faults the offending channel only and leave siblings
# regulating. The classification lives in control.py:_bus_level_read_failure().
INA219_FAILSAFE_ALL_OFF: bool = True
# errno values that classify an INA219 read failure as a bus-level event (stuck/hung bus).
INA219_BUS_LEVEL_ERRNOS: tuple[int, ...] = (5, 121, 110)
# How many channels must fail with bus-level errors in a single tick before the whole
# bank drops to 0% PWM. Default 2 avoids a single-INA219 wiring nack tripping the system.
INA219_FAILSAFE_MIN_BUS_CHANNELS: int = 2
# Max duty (%) still treated as idle for errno-5/121 I²C transient suppression
# (:func:`sensors.ina219_read_failure_expected_idle`). Keep in line with :data:`DUTY_PROBE`
# (session start floor) so a 0.01% default command is not a false “drive on” for diagnostics.
INA219_BENIGN_IDLE_DUTY_MAX_PCT: float = 0.01
# INA219 numeric precision. Bus voltage (TI “bus”, IN− vs GND) is 4 mV/LSB; shunt
# voltage is the differential (VIN+ − VIN−) across the R_shunt, with LSB set by PGA
# (e.g. 10 µV at 40 mV / GAIN=1 — see :func:`i2c_bench.ina219_parse` and the datasheet).
# `READ_*` control :func:`sensors.read_all_real` / :func:`sensors.read_all_sim`; `LOG_*`
# control :meth:`Logger.record` JSON/SQLite rounding (keep READ ≥ LOG so telemetry
# is not the bottleneck).
INA219_BUS_V_READ_DECIMALS: int = 5
INA219_SHUNT_MV_READ_DECIMALS: int = 6
INA219_BUS_V_LOG_DECIMALS: int = 4
INA219_SHUNT_MV_LOG_DECIMALS: int = 5
INA219_SHUNT_UV_LOG_DECIMALS: int = 2
# Physical shunt on each anode INA219 branch (Ω). CoilShield v1 hardware uses **1.0** (1 Ω sense).
# **0.1** (R100 on many breakouts) is still valid — set :envvar:`COILSHIELD_INA219_SHUNT_OHMS` or
# this constant. Must match hardware or reported mA and LSB semantics are wrong.
INA219_SHUNT_OHMS: float = 1.0
_sohm = (os.environ.get("COILSHIELD_INA219_SHUNT_OHMS") or "").strip()
if _sohm:
    INA219_SHUNT_OHMS = max(1e-6, float(_sohm))
# TI INA219 shunt-voltage register LSB in volts (±40 mV full scale, PGA ÷1 → 10 µV/LSB).
INA219_SHUNT_LSB_V: float = 1e-5
# Nominal shunt-current LSB (mA) — mirrors ``ina219_nominal_current_lsb_ma()``; re-tune if you
# change only shunt value at runtime.
INA219_CURRENT_LSB_MA: float = (INA219_SHUNT_LSB_V / max(INA219_SHUNT_OHMS, 1e-9)) * 1000.0

# Practical measurement floor (mA): below this magnitude, the INA219 reading is typically dominated
# by offset/noise/pickup on real wiring and should not be treated as "accurate current".
# With v1 hardware (1.0 Ω shunt), the theoretical LSB is ~0.010 mA, but field noise commonly yields
# ~0.030 mA as the smallest stable non-zero magnitude.
INA219_CURRENT_NOISE_FLOOR_MA: float = 0.03
# GAIN_AUTO in :mod:`sensors` picks a PGA; shunt full scale is ±40 mV at ÷1. Shunt drop is
# V_shunt = I_mA/1000 × R_shunt. If you raise :data:`MAX_MA` or shunt R, confirm
# V_shunt ≤ 40 mV at the worst case (e.g. MAX_MA=5, R=1 Ω → 5 mV, comfortable).
# A spike far above the intended envelope can still saturate; keep **MAX_MA** aligned
# with actual protection and wiring. Do not change gain-related code without re-checking this.
# Floor for effective mA setpoint (``control.Controller._channel_target`` uses
# :func:`iccp_electrolyte.effective_target_ma_floor`). Clamps pathological sub-LSB targets; **0**
# means only the shunt-LSB floor (and :data:`INA219_ENFORCE_CURRENT_LSB_FLOOR`) applies.
TARGET_MA_FLOOR: float = INA219_CURRENT_NOISE_FLOOR_MA
# When True, :func:`iccp_electrolyte.effective_target_ma_floor` includes the shunt LSB mA.
# Set False in tests that use sub-LSB ``TARGET_MA`` on purpose.
INA219_ENFORCE_CURRENT_LSB_FLOOR: bool = True

# Feedforward duty from rolling median Z (see :mod:`iccp_electrolyte`).
FEEDFORWARD_ENABLED: bool = False
# Proportional trim: added to duty (%) after normal ramp step: ``Kp * (target_ma - current_ma)``.
FEEDBACK_KP: float = 0.0
# When feedforward applies, limit one tick to at most this many percentage points *above* the
# current duty (0 = no cap). Stale or extreme Z can otherwise step duty toward ``hi`` instantly.
FEEDFORWARD_MAX_DUTY_JUMP_PCT: float = 10.0

# Runtime health composite (0..1). Weights should sum to 1.0.
HEALTH_WEIGHT_ANODE: float = 0.40
HEALTH_WEIGHT_SURFACE: float = 0.35
HEALTH_WEIGHT_POLARIZATION: float = 0.25
HEALTH_ALERT_THRESHOLD: float = 0.50

# Dedicated INA219 for reference electrode (only if REF_ADC_BACKEND = "ina219").
# On the SAME bus as anodes: must not use any INA219_ADDRESSES. Default 0x42 (strap on module);
# re-strap if that collides with another device.
# On a dedicated gpio-only bus with only the ref INA: any free strap is fine.
REF_INA219_ADDRESS = 0x42
# Match :data:`INA219_SHUNT_OHMS` when the ref module uses the same R_shunt; override if different.
REF_INA219_SHUNT_OHMS = INA219_SHUNT_OHMS
# Optional: median of this many bus/shunt reads per reference sample (1 = single read).
# Try 9 or 16 on long leads or gpio I2C if readings are noisy.
REF_INA219_MEDIAN_SAMPLES = 1
# "bus_v": use INA219 bus voltage (V) × 1000 as the scalar for shift math (typical ref-to-GND wiring).
# "shunt_mv": use shunt voltage in mV from the chip (if your front-end puts signal across the shunt).
REF_INA219_SOURCE = "bus_v"

# --- Current targets ---
# Conservative default for aluminum fin chemistry; tune after commissioning if needed.
MAX_MA = 5.0
TARGET_MA = 0.5
# Optional: COILSHIELD_TARGET_MA=0.75 — field adjustment without editing this file.
_tmae = (os.environ.get("COILSHIELD_TARGET_MA") or "").strip()
if _tmae:
    _tmv = float(_tmae)
    TARGET_MA = max(0.05, min(_tmv, float(MAX_MA) * 0.8))
# Per-channel overrides (0-indexed). Omit a channel key to use the global value.
# Example: CHANNEL_TARGET_MA = {1: 1.8}  → Anode 2 (idx 1) targets 1.8 mA
#          CHANNEL_MAX_MA    = {1: 3.5}  → Anode 2 faults above 3.5 mA
CHANNEL_TARGET_MA: dict = {}
CHANNEL_MAX_MA: dict = {}

# Dry-phase magnitude in read_all_sim (wet vs dry noise ceiling); not used by OPEN/REGULATE FSM.
CHANNEL_WET_THRESHOLD_MA = 0.15

# --- Wet / conduction thresholds (state machine in control.py) ---
CHANNEL_DRY_MA = 0.05  # below this → OPEN (with DRY_HOLD_TICKS hysteresis)
CHANNEL_CONDUCTIVE_MA = 0.5  # sustained above weak thresholds → PATH_STRONG (see control)

# --- Impedance guardrails (ohms; V / I) ---
MAX_EFFECTIVE_OHMS = 12000  # above this → weak path even if current reads “wet”
MIN_EFFECTIVE_OHMS = 800  # below this → hold path class (short / suspicious; use FAULT path)

# --- Timing / hysteresis ---
# Consecutive ticks with “strong path” readings before classify_path returns PATH_STRONG.
CONDUCTIVE_HOLD_TICKS = 5
DRY_HOLD_TICKS = 5  # consecutive ticks below CHANNEL_DRY_MA → OPEN
# Reset dry_count / conductive_count on this wall-clock cadence so stages can move.
STATE_RECHECK_INTERVAL_S = 10.0
# If True, reset PROTECTING enter/exit streaks every STATE_RECHECK_INTERVAL_S (see control.py).
# Default False — prior doc text assumed always-on reset; set True to match that behavior.
STATE_RECHECK_RESET_PROTECT_STREAKS = False

# REGULATE → PROTECTING: require near-target I while path is STRONG for this many ticks.
PROTECTING_ENTER_DELTA_MA = 0.2
PROTECTING_ENTER_HOLD_TICKS = 3
# PROTECTING → REGULATE: |error| above this (or weak path) for PROTECTING_EXIT_HOLD_TICKS.
# Wider band + longer hold reduces oscillation in condensate where film Z swings current.
PROTECTING_EXIT_DELTA_MA = 0.5
PROTECTING_EXIT_HOLD_TICKS = 5

# Minimum I (A) when computing R = V/I for display and Z windows (noise floor).
Z_COMPUTE_I_A_MIN = 1e-6

# --- Duty limits per state (% duty cycle) ---
# Floor in REGULATE: ramp up with PWM_STEP; ceiling is Vcell-capped PWM_MAX
# (no separate “staging %” caps — current/bus/overcurrent limits are the guards).
# 0.01% steps: match PWM_MIN_DUTY, PWM_DUTY_QUANTUM, and soft-PWM (RPi.GPIO 0.0–100.0 float).
# Also the default % when leaving static gate, after ref-soak re-seed, and as the REGULATE probe
# floor — not the PWMBank power-on state (which stays 0% until the FSM raises duty).
DUTY_PROBE = 0.01
# REGULATE: hold **0%% PWM** when sensed |I| is below this (mA) and **I ≥ per-channel
# target** (at/beyond setpoint on sensor noise). Does not apply while I < target — otherwise
# 0 mA with a small target would deadlock at 0%% and never apply DUTY_PROBE.
# Prevents runaway on open / ultra-high-Z when already “satisfied” on I.
# Set to **0** to disable the idle hold entirely.
REGULATE_IDLE_OFF_BELOW_MA = INA219_CURRENT_NOISE_FLOOR_MA
# PROTECTING duty ceiling (%); keep in line with PWM_MAX_DUTY unless you intentionally cap lower.
DUTY_PROTECT_MAX = 80.0

# Hard ceiling on effective cell drive: Vc ≈ bus_v × (PWM%/100) ≤ this (clamps max duty).
# Example at bus≈4.85V: 1.6V → max duty ≈33%; 3.0V → ≈62%; 4.8V → ≈99%. A low cap with
# high-Z tap water limits current (Vc/R) and can block polarization / commissioning — raise
# for bench, lower for production cell chemistry, or set 0 to disable (PWM_MAX_DUTY only).
VCELL_HARD_MAX_V = 4.8

# Rolling window for median effective Ω logging (per channel).
IMPEDANCE_MEDIAN_WINDOW = 32
# Rolling window for std(Z) — film stability / noise (DataLogger).
Z_STATS_WINDOW = 16
# EMA smoothing for conductance proxy I/V (Siemens-scale; DataLogger).
FQI_EMA_ALPHA = 0.15

# --- Probe pulse (deprecated: REGULATE uses DUTY_PROBE floor continuously) ---
PROBE_DUTY_PCT = 0.01
PROBE_DURATION_S = 2.0
PROBE_INTERVAL_S = 60.0
PROBE_MAX_MA = 2.0

# --- Fault auto-recovery ---
FAULT_AUTO_CLEAR = True
FAULT_RETRY_INTERVAL_S = 60.0
FAULT_RETRY_MAX = 3
# Consecutive over-threshold current samples before OVERCURRENT latch (1 = legacy single-tick).
OVERCURRENT_LATCH_TICKS = 1

# --- PWM ---
# Anode drive: RPi.GPIO software PWM on all channels (`control.PWMBank`).
# Frequency tradeoffs (cell + wiring dependent):
#   ~100 Hz — default below: harmonics stay low; less capacitive / inductive pickup
#             on long reference jumpers, ADS1115, and shared I2C vs mid-audio PWM;
#             larger electrolyte / double-layer ripple at a given duty; wiring or
#             coil may be faintly audible.
#   ~1 kHz — smaller low-frequency ripple (drive looks “more DC” to the cell);
#             switching often couples into measurement runs; was a common bench default.
#   ≥20 kHz — inaudible; energy pushed above much ADC settling bandwidth (layout
#             still dominates); soft-PWM duty resolution and gate losses — verify on scope.
PWM_FREQUENCY_HZ = 100
# Base step (% duty per control tick). Use multiples of ``PWM_DUTY_QUANTUM`` (0.01%) so each tick
# moves a meaningful hardware step. Used when the per-mode ``PWM_STEP_*_`` keys are omitted
# (code: ``getattr(..., PWM_STEP)``).
PWM_STEP = 0.01
# Per-tick % duty: match quantum for fine servos. REGULATE: slightly faster up than down; PROTECTING
# one quantum each way. Effective %/s ≈ step / SAMPLE_INTERVAL_S. Raise to 0.05/0.1+ if too slow
# in your cell (e.g. high-Z / bench water).
PWM_STEP_UP_REGULATE = 0.02
PWM_STEP_DOWN_REGULATE = 0.01
PWM_STEP_UP_PROTECTING = 0.01
PWM_STEP_DOWN_PROTECTING = 0.01
# Per-anode ramp overrides (0-based channel index). Omit a key to use that direction’s global
# PWM_STEP_* value above. Lets one channel ramp faster or slower than the others independently.
# Example: CHANNEL_PWM_STEP_UP_REGULATE = {0: 2.0, 2: 0.5}  → Anode 1 faster up, Anode 3 slower up.
CHANNEL_PWM_STEP_UP_REGULATE: dict = {}
CHANNEL_PWM_STEP_DOWN_REGULATE: dict = {}
CHANNEL_PWM_STEP_UP_PROTECTING: dict = {}
CHANNEL_PWM_STEP_DOWN_PROTECTING: dict = {}
# Minimum non-zero command sent to a gate; use with PWM_DUTY_QUANTUM (below).
PWM_MIN_DUTY = 0.01
PWM_MAX_DUTY = 80
# Round duty to this many percentage points before RPi.GPIO (avoids float noise).
# 0.01 = hundredths of a % (10× finer than 0.1). Set 0 to disable extra rounding.
PWM_DUTY_QUANTUM = 0.01

# --- GPIO (BCM) ---
# Aligned with INA219_ADDRESSES: one gate GPIO per row (idx 0 = “Anode 1” in UI = first address).
# Default four anodes: (17, 27, 22, 23). If running three INAs only, use three pins (e.g. 27, 22, 23).
PWM_GPIO_PINS = (17, 27, 22, 23)
LED_STATUS_GPIO = 25

# **Software** union of MOSFET gate duty across GPIOs (one numeric duty for all channels).
# Distinct from whether the **electrolyte / cathode** return is physically shared — that
# is a hardware geometry question. See docs/hardware-shared-anode-bank.md. When True,
# CHANNEL_PWM_STEP_* per-channel dicts are ignored; ramps use the global PWM_STEP_* scalars.
# Default False = per-channel software PWM; set True to drive every gate to the same duty.
SHARED_RETURN_PWM: bool = False


def validate_channel_layout() -> None:
    """
    INA219 address count, logical channel count, and MOSFET GPIO count must match.
    For full startup validation, call :func:`validate_channel_config` (layout **and**
    active-anode / shared-bank rules). Calling this function alone skips
    :func:`validate_active_channel_selection` — use only if you know the active set is
    already validated.
    :func:`control.Controller` uses :func:`validate_channel_config` at construction.
    """
    n_addr = len(INA219_ADDRESSES)
    n_ch = int(NUM_CHANNELS)
    n_pin = len(PWM_GPIO_PINS)
    if n_addr == n_ch == n_pin and n_addr > 0:
        return
    raise ValueError(
        f"Channel layout mismatch: len(INA219_ADDRESSES)={n_addr}, "
        f"NUM_CHANNELS={n_ch}, len(PWM_GPIO_PINS)={n_pin} — "
        "all three must be equal and positive"
    )


# High-side anode 5V disconnect (per channel), optional — CoilShield production has none
# (MOSFETs only; safe-off = 0% PWM). For alternate/legacy PCBs with a supply relay, set
# pin tuple; de-energize on all_outputs_off / process shutdown. Example placeholder: (5, 6, 12, 13).
ANODE_RELAY_GPIO_PINS: tuple[int, ...] | None = None
# If True, relay coil energized (anodes to supply path on) when GPIO is HIGH; de-energize drives LOW.
ANODE_RELAY_ENERGIZE_HIGH: bool = True

# --- Bus voltage limits ---
MIN_BUS_V = 3.0
MAX_BUS_V = 6.0

# --- Timing ---
SAMPLE_INTERVAL_S = 0.5
# Also drives CSV flush cadence in DataLogger. Instant-off (decay) outer loop: same interval
# when ``OUTER_LOOP_INSTANT_OFF`` and ``temp_in_band`` (see iccp_runtime ``outer_loop_interval``).
LOG_INTERVAL_S = 120
# When True, a missing/unreadable DS18B20 (temp_f None) triggers thermal pause (outputs off).
# Default False preserves legacy behavior: do not block CP when the sensor is absent.
# Set env COILSHIELD_THERMAL_PAUSE_ON_MISSING_TEMP=1 to fail-safe on missing temp.
THERMAL_PAUSE_WHEN_SENSOR_MISSING: bool = (
    os.environ.get("COILSHIELD_THERMAL_PAUSE_ON_MISSING_TEMP", "0").strip() == "1"
)
# Outer-loop potential feedback: use commissioning-style instant-off (not live IR-corrupted ref).
OUTER_LOOP_INSTANT_OFF = True
# Single cut + no repolarize soak keeps each LOG_INTERVAL tick short (commissioning uses
# COMMISSIONING_OC_REPEAT_CUTS / COMMISSIONING_OC_REPOLARIZE_S for median measurements).
# Approximate protection interruption: ~OUTER_LOOP_OC_REPEAT_CUTS instant-off cut(s) per
# LOG_INTERVAL_S tick (e.g. ~1 s OC per 120 s) — adjust ``LOG_INTERVAL_S`` or disable
# ``OUTER_LOOP_INSTANT_OFF`` for testing.
OUTER_LOOP_OC_REPEAT_CUTS = 1
OUTER_LOOP_OC_REPOLARIZE_S = 0.0

# --- Logging ---
LOG_BASE_NAME = "iccp"
FAULT_LOG_NAME = "iccp_faults.log"
LOG_MAX_BYTES = 1_000_000
LOG_ROTATION_KEEP = 5
SQLITE_DB_NAME = "coilshield.db"
# Buffer high-frequency ``readings`` rows to cut SD write amplification. Flush when the
# first of: wall interval elapses, or buffer size reaches max. Set **either** to 0 to
# disable batching (one INSERT + COMMIT per tick, legacy behavior; best for unit tests).
SQLITE_FLUSH_INTERVAL_S: float = 30.0
SQLITE_FLUSH_MAX_ROWS: int = 60
LATEST_JSON_NAME = "latest.json"
# Touch `LOG_DIR / DIAGNOSTIC_REQUEST_FILE` while main is running to write
# `LOG_DIR / DIAGNOSTIC_SNAPSHOT_JSON` (rate-limited by DIAGNOSTIC_MIN_INTERVAL_S).
DIAGNOSTIC_SNAPSHOT_JSON = "diagnostic_snapshot.json"
DIAGNOSTIC_REQUEST_FILE = "request_diag"
DIAGNOSTIC_MIN_INTERVAL_S = 60.0
# When True, `latest.json` gains a `diag` object at most once per DIAG_THROTTLE_S (wall clock).
LATEST_JSON_INCLUDE_DIAG = False
DIAG_THROTTLE_S = 60.0
TELEMETRY_RETENTION_DAYS = 30
SQLITE_PURGE_EVERY_N_INSERTS = 10_000
# Web dashboard and consumers: treat latest.json as stale if its mtime is older than this
# (seconds), compared to wall clock when reading the file. 0 = auto:
#   max(4.0, 5.0 * SAMPLE_INTERVAL_S)
# so occasional slow ticks on a loaded Pi are less likely to look "stale" by mistake.
LATEST_FEED_STALE_THRESHOLD_S: float = 0.0


def latest_feed_stale_threshold_s() -> float:
    """Max acceptable age of ``latest.json`` mtime (and JSON ``ts_unix``) for a trusted feed."""
    raw = float(LATEST_FEED_STALE_THRESHOLD_S or 0.0)
    if raw > 0.0:
        return max(3.0, raw)
    return max(4.0, 5.0 * float(SAMPLE_INTERVAL_S))


# --- Reference electrode (ADS1115 default; legacy INA219 if REF_ADC_BACKEND) ---
# Set False to skip reference ADC init until hardware is wired.
REF_ENABLED = True
# Polarization shift = instant-off mV − baseline_mv_for_shift (same sign as a DMM: ref +,
# structure −; **positive** when protected); baseline
# is open-circuit with anodes in bath (Phase 1b) when commissioned, else Phase 1a true native.
# Default 100 mV
# matches a common field picture: native ~100–130 mV at the AIN after Phase 1, ramp succeeds
# when OC inflection sits ~20–40 mV (order ~100 mV below native). Tune if chemistry differs.
# With two-phase 1a+1b commissioning, this is **total** polarization from true native(1a);
# software subtracts ``galvanic_offset_mv`` for the additional shift from the 1b baseline.
TARGET_SHIFT_MV = 100
# Upper band for the same “total from 1a” story: effective max additional shift from 1b
# is ``MAX_SHIFT_MV − galvanic_offset_mv`` when offset is known.
MAX_SHIFT_MV = 200
# mA: outer-loop nudge (``update_potential_target``). Match fine resolution with inner duty steps.
TARGET_MA_STEP = 0.01
# Min wall time (s) between live-``ref_shift`` TARGET_MA nudges in the main loop. ``0`` = no
# limit (aggressive, tests use this). LOG_INTERVAL instant-off nudges use ``force`` and ignore this.
OUTER_LOOP_POTENTIAL_MIN_S: float = 5.0
# Outer loop (``update_potential_target``). When True, nudge **toward** the effective
# mV target while shift stays in the in-band window (|shift−center| > tol from above).
OUTER_LOOP_TRIM_TO_SHIFT_CENTER = True
OUTER_LOOP_SHIFT_TRIM_TOL_MV = 3.0
# Session / field tuning aliases (used by ``update_potential_target`` when set).
# Optional mV deadband for future outer-loop gating (session summary; not wired yet).
OUTER_LOOP_DEADBAND_MV: float = 30.0
# Optional Ag/AgCl linear trim vs pan temperature (°F only): raw mV += (temp_f − anchor)×coef.
# Anchor is ``native_temp_f`` from commissioning.json when present; else ``REF_TEMP_COMP_BASE_F``.
# Literature often quotes mV/°C — convert once: mV_per_F = mV_per_C × (5/9). Default 0 = off.
REF_TEMP_COMP_MV_PER_F = 0.0
# Default anchor when ``native_temp_f`` is missing (e.g. 25 °C = 77 °F electrochemical ref).
REF_TEMP_COMP_BASE_F: float = 77.0
COMMISSIONING_SETTLE_S = 60
# Phase 2: regulate before each instant-off ref sample (s). Longer soak helps
# surface polarization on high-Z bench water; real coil + condensate is faster.
COMMISSIONING_RAMP_SETTLE_S = 80.0
# Phase 2/3: seconds at 0% PWM before reference read (OC / IR decay). Longer dwell →
# cleaner open-circuit scalar but longer CP interruption; tune per rig (default 2.0 s).
COMMISSIONING_INSTANT_OFF_S = 2.0
# Phase 2: current increment per ramp step (mA). Larger steps → fewer instant-offs per mA range.
COMMISSIONING_RAMP_STEP_MA = 0.15
# When shift is above this fraction of TARGET_SHIFT_MV, use finer steps near goal.
COMMISSIONING_RAMP_FINE_STEP_MA = 0.05
COMMISSIONING_RAMP_FINE_NEAR_SHIFT_FRAC = 0.5
# Phase 1 native baseline: sample count and spacing (e.g. 30 × 2 s ≈ 60 s).
# **Not** the same as spec ``capture_native`` in reference.py, which samples for a **wall
# time** ``T_RELAX`` at ``NATIVE_SAMPLE_INTERVAL_S`` (no fixed N). See NATIVE_* block below.
COMMISSIONING_NATIVE_SAMPLE_COUNT = 30
COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S = 2.0
# Wall-clock regulate before final instant-off after target shift is confirmed.
# Actual settle = max(this, COMMISSIONING_RAMP_SETTLE_S) so lock-in is not truncated to 2 s.
COMMISSIONING_PHASE3_LOCK_SETTLE_S = 30.0
# Phase 2: shift confirm hysteresis — within this fraction of TARGET_SHIFT_MV counts as “still
# good”; below that band decays confirm_count instead of hard reset (noisy tap water).
COMMISSIONING_SHIFT_CONFIRM_TOLERANCE = 0.9
# Phase 2: ``"linear"`` = step :data:`COMMISSIONING_RAMP_STEP_MA` until shift confirms;
# ``"binary"`` = bisect mA between :data:`COMMISSIONING_BINARY_MA_LO` and ``MAX_MA``;
# ``"hybrid"`` = binary search then linear ramp/confirm (Algorithm D) — v1 default.
COMMISSIONING_RAMP_MODE: str = "hybrid"
# Binary search must not target below the INA219 current LSB, or the sensor cannot confirm
# a distinct step (same class of failure as a linear ramp to sub-LSB mA).
COMMISSIONING_BINARY_MA_LO: float = max(1e-4, INA219_CURRENT_LSB_MA)
# Written to commissioning.json; bump and handle migration in :func:`commissioning.load_commissioned_target`.
COMMISSIONING_JSON_SCHEMA_VERSION: int = 2
COMMISSIONING_BINARY_RESOLUTION_MA: float = 0.01
COMMISSIONING_BINARY_MAX_ITERATIONS: int = 12
# 0 = off. Aborts ``commissioning.run`` if wall time from run start exceeds this (stuck I²C, etc.).
COMMISSIONING_WALL_TIMEOUT_S: float = 0.0
# After PWM cut, brief settle before the OC **burst** when ``COMMISSIONING_OC_CURVE_ENABLED`` (``COMMISSIONING_OC_INFLECTION_SKIP_RATES``
# already strips the inductive ring from the burst samples; this only covers pre-burst transients).
COMMISSIONING_OC_CURVE_PREBURST_S: float = 0.3
# After Phase 1 settle: confirm all PWM at 0% and INA219 |I| below COMMISSIONING_OC_CONFIRM_I_MA
# before native reads; during averaging, all_off() is re-applied each tick so probe duty
# cannot inject current. Set False to skip (e.g. unusual bench wiring).
COMMISSIONING_PHASE1_OFF_VERIFY = True
# Phase 1: stop soft-PWM and hold each gate pin at static LOW (same idea as PWMBank.cleanup).
# Improves “true off” vs ChangeDutyCycle(0) alone; set False only if your hardware misbehaves.
COMMISSIONING_PHASE1_STATIC_GATE_LOW = True
# Pauses: confirm anodes **removed** before open-circuit native (Phase 1a), then **installed**
# for Phase 1b (OCP, MOSFETs off, same T_RELAX as 1a), then Phase 2 ramp. Gated in code: off in
# `COILSHIELD_SIM=1`, when stdin is not a TTY, when ``COMMISSIONING_FIELD_MODE`` is True, or
# `iccp commission --no-anode-prompts` / env `ICCP_COMMISSION_NO_ANODE_PROMPTS=1`.
# The ``after_phase1`` pause (install anodes before 1b) only runs when Phase 1b is enabled; see
# ``commissioning._galvanic_1b_wanted()``.
COMMISSIONING_ANODE_PLACEMENT_PROMPTS: bool = True
# Field / production coil: anodes stay mounted on fins — no bench “remove for 1a” step.
# When True: no anode placement Enter pauses, no Phase 1b second capture; one OCP native
# (MOSFETs off, same settle + ``capture_native``) is both stored ``native_mv`` and the shift
# baseline (galvanic couple at rest is baked in). Ramp still targets TARGET_SHIFT_MV additional
# polarization vs that baseline. Read by ``commissioning._commissioning_field_mode()``; optional
# env override: ``ICCP_COMMISSION_FIELD_MODE=1|0``.
COMMISSIONING_FIELD_MODE: bool = False
# After Phase 1a, run Phase 1b: second ``capture_native`` with anodes in the bath, gates off.
# Shift / instant-off use the 1b scalar as baseline when present (see ``baseline_mv_for_shift``).
# Set False or `ICCP_SKIP_GALVANIC_1B=1` for legacy single-baseline installs. Implied False when
# ``COMMISSIONING_FIELD_MODE`` is True (single native only).
COMMISSIONING_GALVANIC_1B_ENABLED: bool = True
# Re-commission: if new ``galvanic_offset_mv`` < this fraction of ``galvanic_offset_baseline_mv``
# (first install), persist ``galvanic_offset_service_recommended`` and print a warning.
GALVANIC_OFFSET_SERVICE_FRACTION: float = 0.2
COMMISSIONING_PHASE1_OFF_CONFIRM_TIMEOUT_S = 3.0
# Stricter ceiling (mA) for “at rest” before native averaging — abort if exceeded after long settle.
# Align with I_REST_MA (spec rest gate) so Phase 1 and scheduled native capture use the same idea
# of “at rest” (parasitic / galvanic paths may still need tuning on your rig).
COMMISSIONING_PHASE1_NATIVE_ABORT_I_MA = 0.3
# OC decay curve + inflection (Phase 2/3 instant-off).
COMMISSIONING_OC_CURVE_ENABLED = True
# Post-cutoff potential spike: industry practice treats ~0.3 s of inductive/capacitive
# artifact before the decay curve is meaningful. `COMMISSIONING_OC_INFLECTION_SKIP_RATES`
# skips the first N pairwise |dV/dt| segments; keep N * COMMISSIONING_OC_BURST_INTERVAL_S
# >= ~0.3 s (and total samples * interval >= ~1 s capture for the knee).
COMMISSIONING_OC_BURST_SAMPLES = 50
COMMISSIONING_OC_BURST_INTERVAL_S = 0.02
# Alternative to fixed burst count: sample for a wall-time window (tap water / fast depolarization).
# Enable with e.g. COMMISSIONING_OC_CURVE_DURATION_S = 2.0 and COMMISSIONING_OC_CURVE_POLL_S = 0.025.
COMMISSIONING_OC_DURATION_MODE = False
COMMISSIONING_OC_CURVE_DURATION_S = 2.0
COMMISSIONING_OC_CURVE_POLL_S = 0.025
COMMISSIONING_ADS1115_DR = 7
COMMISSIONING_OC_ADS_MEDIAN_SAMPLES = 1
COMMISSIONING_OC_INFLECTION_SKIP_RATES = 15
COMMISSIONING_OC_INFLECTION_TAIL_EXCLUDE = 0.2
# Multiple instant-off cuts per step; median scalar + repolarize between cuts (s).
COMMISSIONING_OC_REPEAT_CUTS = 3
COMMISSIONING_OC_REPOLARIZE_S = 10.0
# Per-channel cut → ref curve (diagnostics); False = all channels off together.
COMMISSIONING_OC_SEQUENTIAL_CHANNELS = False
# INA219 gate before ADS curve: none | current | delta_v | both
COMMISSIONING_OCBUS_CONFIRM_MODE = "current"
# Bench rigs often show ~0.5–1 mA |I| at 0%% PWM (offset / leakage); 0.15 mA was too tight.
COMMISSIONING_OC_CONFIRM_I_MA = 1.0
COMMISSIONING_OCBUS_MAX_DELTA_V = 0.05
COMMISSIONING_OC_CONFIRM_TIMEOUT_S = 1.5
# Optional PWM Hz override only during OC / sensitive commissioning paths (None = no change).
COMMISSIONING_PWM_HZ: int | None = None
# Sim-only open-circuit baseline (mV-like); name is legacy — field Ag/AgCl uses the same shift math.
SIM_NATIVE_ZINC_MV = 200.0

# --- Spec v2: shift-based FSM, native re-capture, fault taxonomy (docs/iccp-requirements.md) ---
# Interim defaults per Decisions log Q3 — revisit after first bench soak where noted.
# Native baseline capture gates (§3.2, §8.1 Phase 1).
T_RELAX: float = 60.0                       # s relax before native samples (1 min) [interim]
NATIVE_SAMPLE_INTERVAL_S: float = 1.0      # s between samples during median capture
NATIVE_CAPTURE_RETRIES: int = 3             # §3.3 — before REFERENCE_INVALID
I_REST_MA: float = 0.3                      # |I| ceiling for “at rest” (stricter than legacy 1.0 mA)
T_REST_CONFIRM: float = 3.0                 # s the rest gate must hold
# max−min of ref samples in capture_native() must be ≤ this (mV) over T_RELAX; not std-dev
NATIVE_STABILITY_MV: float = 30.0           # peak-to-peak ceiling during native capture [interim]
W_REF: float = 10.0                        # s — stability window (spec §3.2)
NATIVE_SLOPE_MV_PER_MIN: float = 2.0
NATIVE_RECAPTURE_S: float = 24 * 3600.0     # daily scheduled re-capture (§3.4)
NATIVE_DRIFT_TRIGGER_MV: float = 50.0      # drift warning only (§3.4)
NATIVE_BENCH_TOL_MV: float = 5.0            # DMM vs controller [interim]
# After `iccp start` / main: hold 0%% drive (no CP) for this many seconds so the reference
# electrode can depolarize from a **prior** run before the loop uses shift / potential targets.
# Aligns in spirit with ``T_RELAX``/OCP settle. 0 = skip. Default overridden by
# ``ICCP_REFERENCE_STARTUP_STABILIZE_S`` (e.g. ``0`` to disable, ``120`` for two minutes).
_rss_env = (os.environ.get("ICCP_REFERENCE_STARTUP_STABILIZE_S") or "").strip()
REFERENCE_STARTUP_STABILIZE_S: float = (
    max(0.0, float(_rss_env)) if _rss_env else 60.0
)
# FSM timing (§2, §4.4, §6). Per-channel / system timers in control.py.
# T_POL_STABLE: bench-friendly default; increase for noisier field water (e.g. 300 s).
# s with TARGET_SHIFT_MV ≤ shift ≤ MAX_SHIFT_MV before Polarizing → Protected
T_POL_STABLE: float = 30.0
T_SLIP: float = 60.0                        # s below hysteresis before leaving Protected
# Must exceed worst-case Phase 2 ramp wall time (steps × COMMISSIONING_RAMP_SETTLE_S) or
# CANNOT_POLARIZE can fire before ramp reaches MAX_MA.
T_POLARIZE_MAX: float = 3600.0             # s in Polarizing → CANNOT_POLARIZE [interim]
T_PROBE_MAX: float = 30.0
T_OVER_EXIT: float = 30.0
T_OVER_FAULT: float = 60.0
T_SYSTEM_STABLE: float = 60.0               # §2.2 — participating channels (active, not Off/Fault) Protected this long → all_protected
# Hysteresis (mV)
HYST_PROT_EXIT_MV: float = 10.0
HYST_OVER_EXIT_MV: float = 10.0
HYST_OVER_FAULT_MV: float = 50.0
# Polarize retry (§6.1 Q4)
POLARIZE_RETRY_MAX: int = 3
POLARIZE_RETRY_INTERVAL_S: float = T_POLARIZE_MAX  # wait between failed polarize windows
# Per-channel clear-fault side channel (file or JSON line). Single file; content selects channel.
# CLI `iccp clear-fault --channel N` writes {"channel": N, "ts": <unix>} here; Controller drains once.
CLEAR_FAULT_CHANNEL_FILE = PROJECT_ROOT / "clear_fault_channel"

# --- Simulator ---
# Bench nominal bus (V). Matches single-5V-USB product story (~4.8 V); field DMM may read 4.85–5.0.
SIM_NOMINAL_BUS_V = 4.8
SIM_NOISE_MA = 0.05
SIM_DRIFT_MA = 0.002
SIM_INJECT_FAULT_CH = None
SIM_INJECT_OVERCURRENT_MA = 3.0
# Per-channel DC nudges (idx 0..NUM_CHANNELS-1) so bench sim does not show four identical columns.
SIM_CH_BUS_OFFSET_V = (0.0, -0.006, 0.009, -0.004)
SIM_CH_MA_BIAS_DRY = (0.006, 0.020, 0.034, 0.011)
SIM_CH_MA_BIAS_WET = (0.0, 0.07, -0.055, 0.045)
SIM_CH_DRY_NOISE_SCALE = (1.0, 1.4, 0.75, 1.2)
SIM_CH_WET_NOISE_SCALE = (1.0, 1.25, 0.85, 1.1)


def _parse_active_channel_indices() -> frozenset[int] | None:
    """
    Comma-separated 0-based indices in ``COILSHIELD_ACTIVE_CHANNELS`` (e.g. ``0,2``).
    ``None`` means all channels ``0..NUM_CHANNELS-1`` are driven by the controller.
    """
    raw = (os.environ.get("COILSHIELD_ACTIVE_CHANNELS") or "").strip()
    if not raw:
        return None
    out: set[int] = set()
    for p in raw.replace(" ", "").split(","):
        if not p:
            continue
        out.add(int(p, 10))
    nch = int(NUM_CHANNELS)
    for i in out:
        if i < 0 or i >= nch:
            raise ValueError(
                f"COILSHIELD_ACTIVE_CHANNELS: index {i} out of range 0..{nch - 1}"
            )
    if not out:
        return None
    return frozenset(out)


ACTIVE_CHANNEL_INDICES: frozenset[int] | None = _parse_active_channel_indices()


def is_channel_active(ch: int) -> bool:
    """True if this logical anode is selected for CP drive (regulation, PWM) this run."""
    ac = ACTIVE_CHANNEL_INDICES
    nch = int(NUM_CHANNELS)
    if 0 > ch or ch >= nch:
        return False
    if ac is None:
        return True
    return ch in ac


def active_channel_indices_list() -> list[int]:
    """Sorted 0-based indices for UI rows when ``COILSHIELD_ACTIVE_CHANNELS`` is a subset."""
    ac = ACTIVE_CHANNEL_INDICES
    nch = int(NUM_CHANNELS)
    if ac is None:
        return list(range(nch))
    return sorted(int(x) for x in ac)


def validate_active_channel_selection() -> None:
    """
    :func:`is_channel_active` is strict subset of ``0..NUM_CHANNELS-1``;
    partial selection cannot use shared bank PWM (see docs/hardware-shared-anode-bank.md).
    """
    ac = ACTIVE_CHANNEL_INDICES
    nch = int(NUM_CHANNELS)
    if ac is None:
        return
    if not ac:
        raise ValueError("COILSHIELD_ACTIVE_CHANNELS must name at least one anode index")
    if bool(SHARED_RETURN_PWM) and len(ac) < nch:
        raise ValueError(
            "Partial anode selection (COILSHIELD_ACTIVE_CHANNELS or --channels / --anodes) "
            "requires SHARED_RETURN_PWM = False: bank mode drives every MOSFET gate to the "
            "same duty. Set SHARED_RETURN_PWM = False in config/settings.py or clear the "
            "anode filter to use all channels."
        )


def validate_channel_config() -> None:
    """:func:`control.Controller` calls this at construction: layout + active anode set."""
    validate_channel_layout()
    validate_active_channel_selection()


def resolved_telemetry_paths() -> dict[str, str]:
    """Absolute paths shared by main, dashboard, and `iccp live` (see `/api/live` ``telemetry_paths``)."""
    root = PROJECT_ROOT.resolve()
    logd = LOG_DIR.resolve()
    src = (
        "COILSHIELD_LOG_DIR"
        if os.environ.get("COILSHIELD_LOG_DIR", "").strip()
        else (
            "ICCP_LOG_DIR"
            if os.environ.get("ICCP_LOG_DIR", "").strip()
            else "default (<project>/logs)"
        )
    )
    return {
        "project_root": str(root),
        "log_dir": str(logd),
        "latest_json": str((logd / LATEST_JSON_NAME).resolve()),
        "sqlite_db": str((logd / SQLITE_DB_NAME).resolve()),
        "log_dir_source": src,
    }


# --- Supabase (optional; set via environment or repo-root ``.env`` loaded before import) ---
# Prefer ``SUPABASE_SERVICE_ROLE_KEY`` (dashboard name) or ``SUPABASE_SERVICE_KEY`` (.claude naming).
# Never log these values. On Pi, prefer systemd ``EnvironmentFile`` over a world-readable ``.env``.
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip()
SUPABASE_SERVICE_KEY = (
    (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    or (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
)
SUPABASE_ANON_KEY = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_PROJECT_REF = (os.environ.get("SUPABASE_PROJECT_REF") or "").strip()
SUPABASE_CONFIGURED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
