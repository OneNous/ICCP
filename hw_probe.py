#!/usr/bin/env python3
"""
CoilShield hardware probe — no control loop, no config, no FSM.

Runs three independent tests so you can verify hardware with a meter
before touching the main application:

  STEP 1 — I2C scan: which INA219 addresses actually respond
  STEP 2 — Raw INA219 reads: bus V / shunt mV / current mA  (compare to meter)
  STEP 3 — PWM GPIO test: set each pin to known duty %, measure gate and anode

Usage:
  python3 hw_probe.py                      # full test, I2C bus 1, shunt 0.1 Ω
  python3 hw_probe.py --shunt 0.01         # if your board has a 10 mΩ shunt
  python3 hw_probe.py --bus 3              # different I2C bus
  python3 hw_probe.py --skip-pwm          # INA219 only
  python3 hw_probe.py --skip-ina          # PWM only
  python3 hw_probe.py --continuous        # live rolling table, Ctrl+C to stop
"""

from __future__ import annotations

import argparse
import sys
import time

# ---------------------------------------------------------------------------
# Hardware constants — edit here if your wiring differs
# ---------------------------------------------------------------------------
INA219_ADDRESSES = [0x40, 0x41, 0x44, 0x45]   # CH1–CH4
I2C_BUS          = 1                            # Pi header I2C = bus 1
SHUNT_OHMS       = 0.1                          # R100 shunt on INA219 board
PWM_PINS_BCM     = [17, 27, 22, 23]            # CH1–CH4 gate drive pins
PWM_FREQ_HZ      = 1000
SUPPLY_V         = 5.0                          # your VIN+ rail
GPIO_HIGH_V      = 3.3                          # Pi GPIO logic high

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def section(title: str) -> None:
    print(f"\n{'═' * 62}")
    print(f"  {title}")
    print(f"{'═' * 62}")


def pause(msg: str = "  Press Enter to continue (Ctrl+C to quit)...") -> None:
    try:
        input(msg)
    except KeyboardInterrupt:
        _safe_gpio_cleanup()
        print("\nAborted.")
        sys.exit(0)


def _safe_gpio_cleanup() -> None:
    try:
        import RPi.GPIO as GPIO  # noqa: N814
        GPIO.cleanup()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# STEP 1 — I2C scan
# ---------------------------------------------------------------------------

def scan_i2c(bus: int) -> list[int]:
    try:
        import smbus2
    except ImportError:
        print("  [!] smbus2 not installed — pip install smbus2 --break-system-packages")
        return []

    found: list[int] = []
    try:
        b = smbus2.SMBus(bus)
        for addr in range(0x03, 0x78):
            try:
                b.read_byte(addr)
                found.append(addr)
            except OSError:
                pass
        b.close()
    except Exception as e:
        print(f"  [!] Could not open I2C bus {bus}: {e}")
        print("      Try: sudo python3 hw_probe.py  — or add yourself to the i2c group")
    return found


def run_i2c_scan(bus: int) -> None:
    section("STEP 1 — I2C scan")
    print(f"\n  Scanning bus {bus} for all devices (0x03–0x77) ...")
    found = scan_i2c(bus)

    if not found:
        print("  No devices found (or smbus2 unavailable).")
        return

    print(f"\n  Found:    {[hex(a) for a in found]}")
    print(f"  Expected: {[hex(a) for a in INA219_ADDRESSES]}  (CH1–CH4)")

    missing = [a for a in INA219_ADDRESSES if a not in found]
    extra   = [a for a in found if a not in INA219_ADDRESSES]

    if not missing:
        print("  ✓ All 4 expected INA219 addresses present")
    else:
        print(f"\n  ✗ MISSING: {[hex(a) for a in missing]}")
        print("    Possible causes:")
        print("    • Address jumper (A0/A1) on that board not set correctly")
        print("    • Board not powered (check 3.3 V to VCC)")
        print("    • SDA/SCL not connected, or swapped")
        print("    • Two boards sharing the same address — they fight and both disappear")

    if extra:
        print(f"\n  ? Unexpected addresses also found: {[hex(a) for a in extra]}")
        print("    (Could be reference INA219, PCF8591, or other I2C device)")


# ---------------------------------------------------------------------------
# STEP 2 — Raw INA219 reads
# ---------------------------------------------------------------------------

def _make_sensor(address: int, bus: int, shunt_ohms: float):
    from ina219 import INA219
    s = INA219(shunt_ohms, address=address, busnum=bus)
    s.configure(
        voltage_range=INA219.RANGE_16V,
        gain=INA219.GAIN_AUTO,
        bus_adc=INA219.ADC_32SAMP,    # 32 samples ≈ 17 ms — fast enough to probe
        shunt_adc=INA219.ADC_32SAMP,
    )
    return s


def read_one(address: int, bus: int, shunt_ohms: float) -> dict:
    try:
        s = _make_sensor(address, bus, shunt_ohms)
        return {
            "ok":         True,
            "bus_v":      s.voltage(),
            "shunt_mv":   s.shunt_voltage(),
            "current_ma": s.current(),
            "power_mw":   s.power(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def print_ina_table(readings: dict[int, dict], shunt_ohms: float) -> None:
    print()
    print(f"  {'CH':<4} {'Addr':<6} {'Bus V':>8} {'Shunt mV':>10} {'mA':>10} {'mW':>10}  Status")
    print("  " + "─" * 58)
    for ch, addr in enumerate(INA219_ADDRESSES):
        r = readings.get(addr, {"ok": False, "error": "not read"})
        label = f"CH{ch + 1}"
        if r["ok"]:
            print(
                f"  {label:<4} 0x{addr:02X}  "
                f"{r['bus_v']:>8.3f} "
                f"{r['shunt_mv']:>10.4f} "
                f"{r['current_ma']:>10.4f} "
                f"{r['power_mw']:>10.4f}  OK"
            )
        else:
            print(f"  {label:<4} 0x{addr:02X}  {'—':>8} {'—':>10} {'—':>10} {'—':>10}  FAIL: {r['error']}")


def run_ina219_reads(bus: int, shunt_ohms: float) -> None:
    section("STEP 2 — Raw INA219 reads")
    print(f"""
  Shunt resistance: {shunt_ohms} Ω  (--shunt N to override)
  bus_v    = voltage at IN− pin (load side of shunt) — should ≈ your 5 V rail
  shunt_mv = tiny drop across the shunt — {shunt_ohms} Ω × 1 mA = {shunt_ohms} mV
  mA       = shunt_mv ÷ {shunt_ohms} Ω
""")

    readings: dict[int, dict] = {}
    for addr in INA219_ADDRESSES:
        readings[addr] = read_one(addr, bus, shunt_ohms)

    print_ina_table(readings, shunt_ohms)

    print(f"""
  What to check with your meter:
  ─────────────────────────────────────────────────────────
  bus_v   → set meter to DC V, probe from IN− pin to GND.
            Should read ≈ {SUPPLY_V:.1f} V (minus a fraction of mV shunt drop).
            Reads 0 V?   → IN+ / IN− swapped, or supply not reaching board.
            Reads wrong? → Wrong I2C address — reading a different board.

  shunt_mv → you likely can't read this directly; it's < 1 mV at ICCP currents.
             Trust the INA219 or measure the shunt with a precision meter.

  mA      → put your meter in series (current mode, correct fuse!) and compare.
            Off by 10×? → shunt is 1 Ω or 0.01 Ω, not {shunt_ohms} Ω. Use --shunt.
  ─────────────────────────────────────────────────────────
""")


# ---------------------------------------------------------------------------
# STEP 2b — Continuous live table (--continuous flag)
# ---------------------------------------------------------------------------

def run_continuous(bus: int, shunt_ohms: float) -> None:
    section("CONTINUOUS MODE — Ctrl+C to stop")
    print(f"  Polling all 4 channels every second. Compare to your meter live.\n")

    try:
        sensors = {}
        for addr in INA219_ADDRESSES:
            try:
                sensors[addr] = _make_sensor(addr, bus, shunt_ohms)
            except Exception as e:
                print(f"  [!] 0x{addr:02X} init failed: {e}")

        tick = 0
        while True:
            tick += 1
            ts = time.strftime("%H:%M:%S")
            print(f"\n  [{ts}  tick {tick}]")
            print(f"  {'CH':<4} {'Bus V':>8} {'Shunt mV':>10} {'mA':>10} {'mW':>10}  Status")
            print("  " + "─" * 52)
            for ch, addr in enumerate(INA219_ADDRESSES):
                label = f"CH{ch + 1}"
                if addr not in sensors:
                    print(f"  {label:<4} {'—':>8} {'—':>10} {'—':>10} {'—':>10}  no sensor")
                    continue
                try:
                    s = sensors[addr]
                    print(
                        f"  {label:<4} {s.voltage():>8.3f} "
                        f"{s.shunt_voltage():>10.4f} "
                        f"{s.current():>10.4f} "
                        f"{s.power():>10.4f}  OK"
                    )
                except Exception as e:
                    print(f"  {label:<4} {'—':>8} {'—':>10} {'—':>10} {'—':>10}  ERR: {e}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n  Stopped.")


# ---------------------------------------------------------------------------
# STEP 3 — PWM GPIO test
# ---------------------------------------------------------------------------

def run_pwm_test() -> None:
    section("STEP 3 — PWM GPIO test")

    try:
        import RPi.GPIO as GPIO  # noqa: N814
    except ImportError:
        print("  [!] RPi.GPIO not available. Run on the Pi itself.")
        return

    duty_steps = [0, 10, 25, 50, 75]

    print(f"""
  Testing pins BCM {PWM_PINS_BCM} one at a time at {PWM_FREQ_HZ} Hz.
  GPIO logic high ≈ {GPIO_HIGH_V} V, supply ≈ {SUPPLY_V} V.

  At each duty level:
    Gate leg (GPIO output)   → meter reads ≈ duty% × {GPIO_HIGH_V:.1f} V  (DC average)
    Anode side (MOSFET out)  → meter reads ≈ duty% × {SUPPLY_V:.1f} V   (if MOSFET switches)

  If gate voltage does NOT track duty% × 3.3 V:
    → GPIO pin not reaching gate (check your wiring, which leg you're probing)

  If gate DOES track but anode does NOT:
    → MOSFET not switching (wrong part, insufficient Vgs, gate not the left leg)
""")

    GPIO.setmode(GPIO.BCM)

    for ch_idx, pin in enumerate(PWM_PINS_BCM):
        section(f"  CH{ch_idx + 1} — BCM pin {pin}")

        # Drive LOW first so there's no floating before PWM starts
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
        pwm = GPIO.PWM(pin, PWM_FREQ_HZ)
        pwm.start(0)

        print(f"\n  Probe your meter: gate leg of CH{ch_idx + 1} MOSFET  →  GND\n")

        for duty in duty_steps:
            pwm.ChangeDutyCycle(duty)
            gate_v  = GPIO_HIGH_V * duty / 100.0
            anode_v = SUPPLY_V   * duty / 100.0
            print(
                f"  Duty {duty:3d}%  →  gate ≈ {gate_v:.2f} V   anode avg ≈ {anode_v:.2f} V"
            )
            pause("           Measure now, then press Enter ...")

        pwm.stop()
        GPIO.output(pin, GPIO.LOW)   # explicitly LOW before releasing
        GPIO.setup(pin, GPIO.IN)
        print(f"\n  CH{ch_idx + 1} done — pin driven LOW and released to INPUT.")

        if ch_idx < len(PWM_PINS_BCM) - 1:
            next_pin = PWM_PINS_BCM[ch_idx + 1]
            pause(f"\n  Move meter to CH{ch_idx + 2} MOSFET gate (BCM {next_pin}) → GND, then press Enter...")

    GPIO.cleanup()
    print("\n  All PWM pins tested. GPIO cleanup done.")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary() -> None:
    section("Summary — what to confirm before running main.py")
    print("""
  INA219 (STEP 1 & 2):
  ✓  All 4 addresses (0x40 0x41 0x44 0x45) appear in the I2C scan
  ✓  bus_v reads ≈ 5 V on every channel (not 0, not garbage)
  ✓  mA changes when you change load; value matches meter in series

  PWM / MOSFET (STEP 3):
  ✓  Gate voltage tracks  duty% × 3.3 V  as duty changes
  ✓  Anode voltage tracks duty% × 5 V    (MOSFET actually switching)
  ✓  At duty 0% — anode reads 0 V (MOSFET fully off)

  If anything above fails — fix that first before running main.py.
  The control loop cannot work if sensors read wrong or MOSFETs don't switch.
""")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="CoilShield hardware probe")
    ap.add_argument("--bus",        type=int,   default=I2C_BUS,    help="I2C bus number (default 1)")
    ap.add_argument("--shunt",      type=float, default=SHUNT_OHMS, help="Shunt resistance Ω (default 0.1)")
    ap.add_argument("--skip-ina",   action="store_true",            help="Skip INA219 tests")
    ap.add_argument("--skip-pwm",   action="store_true",            help="Skip PWM GPIO test")
    ap.add_argument("--continuous", action="store_true",            help="Live rolling INA219 table (Ctrl+C to stop)")
    args = ap.parse_args()

    print("\n┌─────────────────────────────────────────────────────────┐")
    print("│   CoilShield hw_probe — raw hardware only, no control  │")
    print("│   Ctrl+C at any time exits cleanly                     │")
    print("└─────────────────────────────────────────────────────────┘")
    print(f"\n  I2C bus: {args.bus}   Shunt: {args.shunt} Ω   Supply assumed: {SUPPLY_V} V")

    try:
        if args.continuous:
            run_continuous(args.bus, args.shunt)
            return 0

        if not args.skip_ina:
            run_i2c_scan(args.bus)
            run_ina219_reads(args.bus, args.shunt)

        if not args.skip_pwm:
            pause("\n  Ready to start PWM test? Set meter to DC voltage mode, then Enter...")
            run_pwm_test()

        print_summary()

    except KeyboardInterrupt:
        _safe_gpio_cleanup()
        print("\nAborted.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
