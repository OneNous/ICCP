"""
CoilShield — single source of truth for all tunables.
Import as: import config.settings as cfg  (never from config.settings import *)
"""

from pathlib import Path

# --- Project paths ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
CLEAR_FAULT_FILE = PROJECT_ROOT / "clear_fault"

# --- I2C ---
I2C_BUS = 1
# REF I2C: dedicated gpio bit-bang bus (dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=20,i2c_gpio_scl=12)
REF_I2C_BUS = 3

INA219_ADDRESSES = [0x40, 0x41, 0x44, 0x45]
NUM_CHANNELS = 4

# ADS1115 reference ADC (header I2C; optional TCA9548A via I2C_MUX_*).
ADS1115_ADDRESS = 0x48
ADS1115_BUS = 1
ADS1115_CHANNEL = 0
ADS1115_FSR_V = 4.096
# Median of N single-ended reads per sample (noise on long leads).
REF_ADS_MEDIAN_SAMPLES = 1
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
TARGET_MA = 1.2
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

# REGULATE → PROTECTING: require near-target I while path is STRONG for this many ticks.
PROTECTING_ENTER_DELTA_MA = 0.2
PROTECTING_ENTER_HOLD_TICKS = 3
# PROTECTING → REGULATE: |error| above this (or weak path) for PROTECTING_EXIT_HOLD_TICKS.
PROTECTING_EXIT_DELTA_MA = 0.35
PROTECTING_EXIT_HOLD_TICKS = 3

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

# --- PWM ---
PWM_FREQUENCY_HZ = 1000
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

# --- Logging ---
LOG_BASE_NAME = "iccp"
FAULT_LOG_NAME = "iccp_faults.log"
LOG_MAX_BYTES = 1_000_000
LOG_ROTATION_KEEP = 5
SQLITE_DB_NAME = "coilshield.db"
LATEST_JSON_NAME = "latest.json"
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
COMMISSIONING_RAMP_SETTLE_S = 60.0
# Phase 2/3: PWM-off before ref read (s). Shorter cuts shed less charge between samples.
COMMISSIONING_INSTANT_OFF_S = 0.05
# Phase 2: current increment per ramp step (mA). Larger steps → fewer instant-offs per mA range.
COMMISSIONING_RAMP_STEP_MA = 0.15
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
