"""
CoilShield — single source of truth for tunables.
Import as: `import config.settings as cfg` (never `from config.settings import *`).
"""

from pathlib import Path

# --- Project paths ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
CLEAR_FAULT_FILE = PROJECT_ROOT / "clear_fault"

# --- I2C ---
I2C_BUS = 1  # Pi 3/4/5: bus 1 (SCL/SDA). Try i2cdetect -y 0 on very old boards.

CHANNEL_ADDRESSES = (0x40, 0x41, 0x44, 0x45)
NUM_CHANNELS = len(CHANNEL_ADDRESSES)

# --- Current targets (aluminum-safe HVAC) ---
TARGET_MA = 0.5  # mA per channel — aluminum-safe HVAC coils
MAX_MA = 2.0  # hard cutoff per channel

# 0.5 mA/channel = ~0.1-0.2 mA/ft² for typical HVAC coil sections
# Conservative for aluminum-containing coils — overprotection damages Al
# Increase to 2-3 mA only for all-steel or all-copper systems

# --- PWM (v1: incremental only — no PID until hardware-tuned) ---
PWM_FREQUENCY_HZ = 1000  # 1 kHz — appropriate for electrochemical loads
PWM_STEP = 1  # percent per iteration — small steps prevent oscillation
PWM_MIN_DUTY = 0
PWM_MAX_DUTY = 80  # headroom so loop can respond to rising cell impedance

# --- GPIO (BCM numbers — replace from schematic) ---
PWM_GPIO_PINS = (17, 27, 22, 23)  # CH1..CH4
WET_SWITCH_GPIO = 24
LED_STATUS_GPIO = 25  # None to disable

# --- Wet switch ---
WET_DEBOUNCE_MS = 500  # mandatory — drain pan splash / condensate

# --- Timing ---
SAMPLE_INTERVAL_S = 0.5
LOG_INTERVAL_S = 60

# --- Bus voltage limits (INA219 bus voltage) ---
MIN_BUS_V = 9.0
MAX_BUS_V = 14.0

# --- Cathode bonding (wet but no meaningful current on any channel) ---
MIN_EXPECTED_MA_WHEN_WET = 0.05

# --- Logging / rotation (SD wear) ---
LOG_BASE_NAME = "iccp"
LOG_MAX_BYTES = 1_000_000
LOG_ROTATION_KEEP = 5

# --- Simulator ---
SIM_NOMINAL_BUS_V = 11.8
SIM_NOISE_MA = 0.05
SIM_DRIFT_MA = 0.002  # mA per tick — slow drift
SIM_INJECT_FAULT_CH = None  # 1..NUM_CHANNELS to inject overcurrent on that channel
SIM_INJECT_OVERCURRENT_MA = 3.0
SIM_ASSUME_WET = True  # sim: pan wet so control path runs
