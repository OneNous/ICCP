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
I2C_BUS = 1  # Pi 3/4/5: bus 1 (SCL/SDA). Try i2cdetect -y 0 on very old boards.

# INA3221 I2C addresses — two chips, 3 channels each
# Chip 1 (0x40): CH1–CH3   Chip 2 (0x41): CH4–CH5 (third channel unused)
INA3221_ADDRESSES = (0x40, 0x41)
NUM_CHANNELS = 5

# --- Current targets (aluminum-safe HVAC) ---
# Same target every channel — wet dwell time per anode does the “weighting.”
TARGET_MA = 0.5  # mA per channel when wet and regulating
MAX_MA = 2.0  # mA — hard safety cutoff per channel; latches that channel

# --- Per-channel wet detection (replaces master wet switch) ---
CHANNEL_WET_THRESHOLD_MA = 0.02  # mA — at or above = film bridging anode→coil

# Probe pulse for dormant (dry) channels
PROBE_DUTY_PCT = 3  # %
PROBE_DURATION_S = 2.0  # s — settle before evaluating probe current
PROBE_INTERVAL_S = 60.0  # s — between probes on a dormant channel

# --- PWM ---
PWM_FREQUENCY_HZ = 1000
PWM_STEP = 1
PWM_MIN_DUTY = 0
PWM_MAX_DUTY = 80

# --- GPIO (BCM) — CH1=top-left … CH5=center ---
PWM_GPIO_PINS = (17, 27, 22, 23, 12)
LED_STATUS_GPIO = 25  # None to disable

# --- Bus voltage limits ---
MIN_BUS_V = 9.0
MAX_BUS_V = 14.0

# --- Timing ---
SAMPLE_INTERVAL_S = 0.5
LOG_INTERVAL_S = 60

# --- Logging (daily CSV + append-only fault log; see logger.py) ---
LOG_BASE_NAME = "iccp"
FAULT_LOG_NAME = "iccp_faults.log"
LOG_MAX_BYTES = 1_000_000
LOG_ROTATION_KEEP = 5

# --- Simulator ---
SIM_NOMINAL_BUS_V = 11.8
SIM_NOISE_MA = 0.05
SIM_DRIFT_MA = 0.002
SIM_INJECT_FAULT_CH = None  # 0..NUM_CHANNELS-1 to inject overcurrent on that channel
SIM_INJECT_OVERCURRENT_MA = 3.0
