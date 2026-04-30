# Sensors & Hardware

> **Scope:** All sensor drivers, hardware abstractions, GPIO control, and physical I/O. Read this when working in `sensors.py`, `reference.py`, `temp.py`, or `leds.py`.

## Hardware Inventory

This is what's physically on the device. Pin assignments live in `config/settings.py` and `docs/HARDWARE.md`. If they ever conflict, code wins (settings.py is the source of truth) and docs need updating.

### Microcontroller

- **Raspberry Pi 3 Model B** (current). Pi 4 or Pi Zero 2 W are future options.
- Pi OS Lite, Bookworm (Debian 12).
- I2C enabled via `raspi-config`.
- 1-Wire enabled for DS18B20.
- Bluetooth enabled (built into Pi 3+).

### Current Sensing

- **INA3221** triple-channel current/voltage monitor. **Two boards required** for 4 channels (each board does 3 channels; we use 0-2 on board 1 and channel 0 on board 2, leaving 2 spare).
- I2C addresses: 0x40 and 0x41 (configurable via address strap).
- Replaced the older 4× INA219 design — see `docs/DECISIONS.md` for the rationale (cleaner wiring, fewer addresses, same accuracy).

### Reference Electrode Reading

- **ADS1115** 16-bit single-ended/differential ADC.
- I2C address: 0x48.
- Differential mode (A0-A1) for the Ag/AgCl reading.
- PGA = 1 (±4.096V range).
- 8 SPS sample rate (slow but stable; we average over 1 second).

### MOSFETs

- **IRLZ44N** N-channel logic-level MOSFETs, one per channel.
- Gate: Pi GPIO via PWM
- Drain: anode side of INA3221 shunt
- Source: anode wire → electrolyte → bond wire → GND rail

**Critical wiring rule (don't get this wrong):** MOSFET source is NEVER directly connected to GND. The path is: source → anode → condensate → bond wire → GND. The electrolyte is the only path between source and GND. If you wire source directly to GND, you'll short the supply through the shunt and destroy the INA3221. Lost one this way already. Always power off before moving load-side wires.

### Anodes

- **MMO Ir-Ta titanium mesh** for production.
- **Graphite rods** for bench testing only — they consume too fast for production.
- 4 channels: 4 corners of A-frame coil.
- Optionally a 5th anode at the center peak (firmware supports up to 5 channels but only 4 wired currently).

### Reference Electrode

- **Stonylab Ag/AgCl saturated KCl, 6×65mm.**
- Filling solution: 3.0M KCl.
- Tip submerged in drain pan condensate (or in bench condensate during testing).
- Backup electrode kept dry, used for monthly drift verification.

### Temperature

- **DS18B20** 1-wire digital temp sensor.
- Read via sysfs at `/sys/bus/w1/devices/28-*/w1_slave`.
- Mounted near the coil drain pan to read condensate temperature.

### Status LEDs

- **3× discrete LEDs** wired to GPIO pins.
- Status (green): heartbeat blink, solid when protecting
- Fault (red): solid when any channel is faulted
- BLE (blue): blink during BLE advertising, solid when paired

### Power

- **5V USB.** Single USB cable provides power to the entire system.
- Pi 3 + 4 channels at 0.5 mA each = ~502 mA total. Well within Pi's USB power budget.
- No 12V supply, no buck converter, no battery backup. Simple is good.

## I2C Bus Management

Both INA3221s and the ADS1115 share one I2C bus (bus 1, the default Pi user bus). Three devices, three different addresses.

### Rule SH-1: Always Catch I2C Errors

I2C is not perfectly reliable. Sensors NACK occasionally. Cables can have intermittent contacts. Code that crashes on I2C errors is broken.

```python
# Bad
voltage = ina.bus_voltage  # Throws on bus error

# Good
try:
    voltage = ina.bus_voltage
except (OSError, IOError) as e:
    self.logger.warn(f"INA3221 read failed on ch{channel}: {e}")
    return None  # Or last known good value
```

If a sensor read fails, the affected channel transitions to FAULT (or stays in current state if the failure is transient). Don't crash the whole process.

### Rule SH-2: TCA9548A Multiplexer Is Optional

The TCA9548A I2C multiplexer is on the BOM as an option for bus isolation. During validation it's not strictly required — three devices on one bus is well within tolerances. If bus errors become frequent (>1 per hour), revisit this and add the mux.

### Rule SH-3: I2C Read Timing

Don't poll faster than the sensor's actual conversion time:

- INA3221: 1.1 ms per channel at default config. Reading all 3 channels takes ~3 ms.
- ADS1115: 125 ms at 8 SPS. Don't read faster than once per 200 ms.
- DS18B20: 750 ms for full 12-bit conversion. Don't poll the 1-wire bus more often than once per second.

Reading too fast returns stale data and wastes CPU. The control loop intervals (0.5s inner, 60s outer) are calibrated to the sensors' natural rates.

## GPIO

### Rule SH-4: Use `gpiozero` Where Possible, RPi.GPIO For PWM

`gpiozero` has a nicer API for digital I/O (LEDs, buttons, simple outputs). For PWM, use `RPi.GPIO` directly because gpiozero's PWM is software-only and jittery for our purposes.

PWM frequency: 100 Hz. Don't change without bench testing — too high and the MOSFET gate driver struggles, too low and the average current calculation breaks.

### Rule SH-5: Pin Assignments Are Frozen

Don't change PWM pin assignments without updating wiring diagrams in `docs/HARDWARE.md` AND the units actually built. The 10 validation units are wired to specific pins; reassigning means re-wiring physical hardware.

```python
PWM_GPIO_PINS = (17, 27, 22, 23)  # Channels 0-3
LED_STATUS_PIN = 25
LED_FAULT_PIN = 24
LED_BLE_PIN = 18
FACTORY_RESET_BUTTON_PIN = 4
```

If you absolutely must change a pin, document the rewire procedure for existing units.

### Rule SH-6: Cleanup On Exit

Register signal handlers so PWM stops cleanly on shutdown:

```python
import signal
def shutdown(signum, frame):
    for channel in channels:
        channel.gate_off()
    GPIO.cleanup()
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)
```

Without this, channels stay energized when the process dies. systemd will restart the process but during the gap, current flows uncontrolled.

## Sensor Simulator

`sensors.py` includes a simulator activated by `COILSHIELD_SIM=1`. It produces realistic sensor data without real hardware:

- 5 anode profiles (clean, partially fouled, fully fouled, broken, intermittent)
- 24-hour day cycles with humidity and temperature variation
- Time-scalable (run a simulated week in 5 minutes for testing)
- Realistic INA3221 readings, ADS1115 readings, DS18B20 readings

Use this for:
- Development on a laptop
- Unit tests
- Bug reproduction without real hardware
- Stress testing the control loop

Don't use this as a substitute for bench testing on real hardware. The simulator can't catch I2C bugs, GPIO timing issues, or actual electrical noise.

## Calibration

### Reference Electrode

The Ag/AgCl reference is calibrated at the factory by Stonylab (theoretical: +0.210 V vs SHE at 25 °C with 3M KCl filling). We don't recalibrate. We verify against a backup electrode monthly.

If the working electrode and backup disagree by >50 mV, the working electrode is suspect. Replace it.

### Current Sensing

INA3221 has internal calibration. We don't tweak it. The shunt resistor value (0.1 Ω) is set by the board. Documented in the code; don't change.

### Temperature

DS18B20 is factory-calibrated to ±0.5 °C accuracy. We don't recalibrate. If a sensor reads obviously wrong (e.g., 200 °C in a basement), assume it's broken and replace.

## Hardware Failure Modes

Real things that have happened or could happen:

| Failure | Symptom | Response |
|---|---|---|
| INA3221 destroyed by short | One I2C address drops off bus | Replace board, document incident, check wiring |
| Reference electrode KCl evaporated | Reading drifts toward more positive | Refill KCl from spare bottle |
| Reference electrode liquid junction clogged | Sustained positive readings during wet | Replace electrode, send old one for inspection |
| MOSFET fails open | Channel reports 0 mA always | Check fault counter, replace MOSFET if stuck |
| MOSFET fails closed | Channel runs at full duty regardless of PWM | Polarization cutoff catches it; latch fault |
| DS18B20 disconnected | sysfs reads return error | Continue without temperature compensation, log warning |
| Anode wire broken | Channel reports 0 mA but reference shows underprotected | Investigate during next service call |
| Bond wire broken | All channels report low/zero current | Major fault — alerts owner, requires service |
| WiFi router replaced (new SSID) | Device can't reach Supabase | Tech app re-provisions via BLE |

## Common Cursor Pitfalls in Sensor Code

- Using `time.sleep()` inside the inner loop instead of waiting for the next scheduled tick (causes drift)
- Reading I2C without retries — one NACK shouldn't fault the channel
- Reading INA3221 channels individually (3 separate I2C transactions) when there's a "read all channels" call
- Using `RPi.GPIO.output()` and `RPi.GPIO.input()` interchangeably — they're not interchangeable
- Forgetting to set GPIO mode (BCM vs BOARD) — pick one and document it
- Suggesting Adafruit libraries that don't exist for the specific hardware variants we use

## Smoke Test for Sensors

Before declaring sensor code "validation-ready":

1. `i2cdetect -y 1` shows 0x40, 0x41, 0x48, and any TCA9548A if used
2. `cat /sys/bus/w1/devices/28-*/w1_slave` returns valid temperature
3. INA3221 returns sensible bus voltage on all 4 channels (around 5V when idle)
4. INA3221 reports current changes when manually adjusting PWM duty
5. ADS1115 differential reading is stable to within ±2 mV when reference is dry (open circuit)
6. ADS1115 reading changes correctly when reference is placed in condensate
7. PWM frequency measured on oscilloscope is 100 Hz ±1%
8. LEDs respond to GPIO commands
9. Factory reset button is debounced (no false triggers from EMI)
10. All sensor reads complete within their expected time windows

If any step fails, sensor code is not validation-ready.
