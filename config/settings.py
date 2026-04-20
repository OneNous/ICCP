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

INA219_ADDRESSES = [0x40, 0x41, 0x44, 0x45]
NUM_CHANNELS = 4

# ADS1115 reference ADC (header I2C; optional TCA9548A via I2C_MUX_*).
ADS1115_ADDRESS = 0x48
ADS1115_BUS = 1
ADS1115_CHANNEL = 0
ADS1115_FSR_V = 4.096
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
# Multiply ADC volts (after ×1000) for divider scaling vs. electrode node.
REF_ADS_SCALE = 1.0

# Reference backend: "ads1115" (default) or legacy "ina219" on REF_I2C_BUS.
REF_ADC_BACKEND = "ads1115"
# Bench: copper wire; field: ag_agcl after swap — informational for logs/docs.
REF_ELECTRODE_KIND = "copper_bench"

# TCA9548A @ 0x70 (ADDR straps): ch0..3 → INA219 0x40/0x41/0x44/0x45; ch4 → ADS1115 @ 0x48.
# Control byte = 1 << N. Bench / no-mux rigs: set I2C_MUX_ADDRESS = None and all mux channels None.
I2C_MUX_ADDRESS: int | None = 0x70
I2C_MUX_CHANNEL_ADS1115: int | None = 4
# Single downstream port shared by every anode INA219 (ignored if I2C_MUX_CHANNELS_INA219 set).
I2C_MUX_CHANNEL_INA219: int | None = None
# Per anode index 0..NUM_CHANNELS-1: TCA9548A port before that INA219 (bytes 0x01,0x02,0x04,0x08).
I2C_MUX_CHANNELS_INA219: tuple[int, ...] | None = (0, 1, 2, 3)

# Dedicated INA219 for reference electrode.
# On the SAME bus as anodes: address must not collide with INA219_ADDRESSES (e.g. 0x42,
# 0x46, 0x47 per breakout straps).
# On a DEDICATE gpio-only bus with only this module: 0x40 is fine (no anode conflict).
REF_INA219_ADDRESS = 0x40
REF_INA219_SHUNT_OHMS = 0.1
# Optional: median of this many bus/shunt reads per reference sample (1 = single read).
# Try 9 or 16 on long leads or gpio I2C if readings are noisy.
REF_INA219_MEDIAN_SAMPLES = 1
# "bus_v": use INA219 bus voltage (V) × 1000 as the scalar for shift math (typical ref-to-GND wiring).
# "shunt_mv": use shunt voltage in mV from the chip (if your front-end puts signal across the shunt).
REF_INA219_SOURCE = "bus_v"

# --- Current targets ---
# Conservative default for aluminum fin chemistry; raise on bench copper if needed.
TARGET_MA = 0.5
MAX_MA = 5.0
# Per-channel overrides (0-indexed). Omit a channel key to use the global value.
# Example: CHANNEL_TARGET_MA = {1: 1.8}  → CH2 targets 1.8 mA
#          CHANNEL_MAX_MA    = {1: 3.5}  → CH2 faults above 3.5 mA
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
# Do not reset PROTECTING enter/exit streaks on periodic recheck (see control.py).
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
DUTY_PROBE = 3.0
# PROTECTING duty ceiling (%); keep in line with PWM_MAX_DUTY unless you intentionally cap lower.
DUTY_PROTECT_MAX = 80.0

# Hard ceiling on effective cell drive: Vc ≈ bus_v × (PWM%/100) ≤ this (clamps max duty).
# Example at bus≈4.85V: 1.6V → max duty ≈33%; 3.0V → ≈62%. A low cap with high-Z tap water
# limits current (Vc/R) and can block polarization / commissioning — raise for bench, lower
# for production cell chemistry, or set 0 to disable (PWM_MAX_DUTY only).
VCELL_HARD_MAX_V = 3.0

# Rolling window for median effective Ω logging (per channel).
IMPEDANCE_MEDIAN_WINDOW = 32
# Rolling window for std(Z) — film stability / noise (DataLogger).
Z_STATS_WINDOW = 16
# EMA smoothing for conductance proxy I/V (Siemens-scale; DataLogger).
FQI_EMA_ALPHA = 0.15

# --- Probe pulse (deprecated: REGULATE uses DUTY_PROBE floor continuously) ---
PROBE_DUTY_PCT = 3
PROBE_DURATION_S = 2.0
PROBE_INTERVAL_S = 60.0
PROBE_MAX_MA = 2.0

# --- Fault auto-recovery ---
FAULT_AUTO_CLEAR = True
FAULT_RETRY_INTERVAL_S = 60.0
FAULT_RETRY_MAX = 10
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
PWM_STEP = 1
PWM_MIN_DUTY = 1
PWM_MAX_DUTY = 80

# --- GPIO (BCM) ---
PWM_GPIO_PINS = (17, 27, 22, 23)
LED_STATUS_GPIO = 25

# --- Bus voltage limits ---
MIN_BUS_V = 3.0
MAX_BUS_V = 6.0

# --- Timing ---
SAMPLE_INTERVAL_S = 0.5
LOG_INTERVAL_S = 60
# Outer-loop potential feedback: use commissioning-style instant-off (not live IR-corrupted ref).
OUTER_LOOP_INSTANT_OFF = True
# Single cut + no repolarize soak keeps each LOG_INTERVAL tick short (commissioning uses
# COMMISSIONING_OC_REPEAT_CUTS / COMMISSIONING_OC_REPOLARIZE_S for median measurements).
OUTER_LOOP_OC_REPEAT_CUTS = 1
OUTER_LOOP_OC_REPOLARIZE_S = 0.0

# --- Logging ---
LOG_BASE_NAME = "iccp"
FAULT_LOG_NAME = "iccp_faults.log"
LOG_MAX_BYTES = 1_000_000
LOG_ROTATION_KEEP = 5
SQLITE_DB_NAME = "coilshield.db"
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

# --- Reference electrode (ADS1115 default; legacy INA219 if REF_ADC_BACKEND) ---
# Set False to skip reference ADC init until hardware is wired.
REF_ENABLED = True
TARGET_SHIFT_MV = 100
MAX_SHIFT_MV = 200
TARGET_MA_STEP = 0.02
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
COMMISSIONING_NATIVE_SAMPLE_COUNT = 30
COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S = 2.0
# Wall-clock regulate before final instant-off after target shift is confirmed.
# Actual settle = max(this, COMMISSIONING_RAMP_SETTLE_S) so lock-in is not truncated to 2 s.
COMMISSIONING_PHASE3_LOCK_SETTLE_S = 30.0
# Phase 2: shift confirm hysteresis — within this fraction of TARGET_SHIFT_MV counts as “still
# good”; below that band decays confirm_count instead of hard reset (noisy tap water).
COMMISSIONING_SHIFT_CONFIRM_TOLERANCE = 0.9
# After Phase 1 settle: confirm all PWM at 0% and INA219 |I| below COMMISSIONING_OC_CONFIRM_I_MA
# before native reads; during averaging, all_off() is re-applied each tick so probe duty
# cannot inject current. Set False to skip (e.g. unusual bench wiring).
COMMISSIONING_PHASE1_OFF_VERIFY = True
COMMISSIONING_PHASE1_OFF_CONFIRM_TIMEOUT_S = 3.0
# Stricter ceiling (mA) for “at rest” before native averaging — abort if exceeded after long settle.
COMMISSIONING_PHASE1_NATIVE_ABORT_I_MA = 0.1
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
COMMISSIONING_OC_CONFIRM_I_MA = 0.15
COMMISSIONING_OCBUS_MAX_DELTA_V = 0.05
COMMISSIONING_OC_CONFIRM_TIMEOUT_S = 0.5
# Optional PWM Hz override only during OC / sensitive commissioning paths (None = no change).
COMMISSIONING_PWM_HZ: int | None = None
SIM_NATIVE_ZINC_MV = 200.0

# --- Simulator ---
# Bench nominal bus (V); intentionally not tied to field supply (~4.85 V) — tune for your rig.
SIM_NOMINAL_BUS_V = 4.85
SIM_NOISE_MA = 0.05
SIM_DRIFT_MA = 0.002
SIM_INJECT_FAULT_CH = None
SIM_INJECT_OVERCURRENT_MA = 3.0
# Per-channel DC nudges (CH0..CH3) so bench sim does not show four identical columns.
SIM_CH_BUS_OFFSET_V = (0.0, -0.006, 0.009, -0.004)
SIM_CH_MA_BIAS_DRY = (0.006, 0.020, 0.034, 0.011)
SIM_CH_MA_BIAS_WET = (0.0, 0.07, -0.055, 0.045)
SIM_CH_DRY_NOISE_SCALE = (1.0, 1.4, 0.75, 1.2)
SIM_CH_WET_NOISE_SCALE = (1.0, 1.25, 0.85, 1.1)


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
