#!/usr/bin/env python3
"""
CoilShield hardware probe — no control loop, no FSM.

Uses **smbus2 only** for INA219 and ADS1115 (no pi-ina219), so it works under
`sudo` and under a user venv the same way.

Steps:
  1 — I2C scan
  2 — INA219 raw reads (4 channels)
  3 — ADS1115 single-ended AIN0..AIN3 (default address from config/settings.py)
  4 — DS18B20 (1-Wire) if present
  5 — PWM GPIO walk (optional)

Also callable via:  iccp probe [same flags]

Useful flags:
  • --ads1115 [ADDR]   Quick ADS1115 AIN0..3 only (default ADDR 0x48); plan checklist.
  • --init             Force INA219 CONFIG write on each channel before reads.
  • --ads1115-only     Same as --ads1115 with address from config/settings.

Pi tips:
  • Prefer **no sudo** and add user to **i2c** group:  sudo usermod -aG i2c $USER
    then log out/in.  Then:  python3 hw_probe.py
  • If you must use sudo, use the **same** Python that has smbus2:
      sudo $(which python3) hw_probe.py
"""

from __future__ import annotations

import argparse
import getpass
import glob
import os
import sys
import time

try:
    import config.settings as cfg
except ImportError:
    cfg = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Hardware defaults (overridden by config.settings when importable)
# ---------------------------------------------------------------------------
if cfg is not None:
    INA219_ADDRESSES = tuple(cfg.INA219_ADDRESSES)
    I2C_BUS = int(cfg.I2C_BUS)
    PWM_PINS_BCM = list(cfg.PWM_GPIO_PINS)
    PWM_FREQ_HZ = int(cfg.PWM_FREQUENCY_HZ)
    ADS1115_ADDRESS = int(getattr(cfg, "ADS1115_ADDRESS", 0x48))
    ADS1115_BUS = int(getattr(cfg, "ADS1115_BUS", cfg.I2C_BUS))
    ADS1115_FSR_V = float(getattr(cfg, "ADS1115_FSR_V", 2.048))
else:
    INA219_ADDRESSES = (0x40, 0x41, 0x44, 0x45)
    I2C_BUS = 1
    PWM_PINS_BCM = [17, 27, 22, 23]
    PWM_FREQ_HZ = 100
    ADS1115_ADDRESS = 0x48
    ADS1115_BUS = 1
    ADS1115_FSR_V = 2.048

SHUNT_OHMS = 0.1
SUPPLY_V = 5.0
GPIO_HIGH_V = 3.3

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


def _mux_select_anode_for_probe(sm: object, ch_index: int) -> None:
    """TCA9548A: match sensors.py — per-channel port or one legacy port before INA219 I/O."""
    if cfg is None:
        return
    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    if mux_addr is None:
        return
    from i2c_bench import mux_select_on_bus

    per = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
    leg = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
    if per is not None and ch_index < len(per):
        mux_select_on_bus(sm, int(mux_addr), int(per[ch_index]))
    elif leg is not None:
        mux_select_on_bus(sm, int(mux_addr), int(leg))


def _i2c_diagnostic(e: BaseException, bus: int) -> None:
    print("\n  [!] I2C diagnostic:")
    print(f"      Error: {e!r}")
    try:
        u = getpass.getuser()
    except Exception:
        u = "?"
    print(f"      uid/euid={os.getuid()}/{os.geteuid()}  user={u!r}")
    try:
        dev = f"/dev/i2c-{bus}"
        st = os.stat(dev)
        print(f"      {dev} exists (mode {oct(st.st_mode)})")
    except OSError as e2:
        print(f"      {dev}: {e2}")
    print("      • Add user to i2c group:  sudo usermod -aG i2c $USER  (then re-login)")
    print("      • Or use same interpreter as venv:  sudo $(which python3) hw_probe.py")


# ---------------------------------------------------------------------------
# STEP 1 — I2C scan
# ---------------------------------------------------------------------------


def scan_i2c(bus: int) -> list[int]:
    try:
        import smbus2
    except ImportError:
        print("  [!] smbus2 not installed — pip install smbus2")
        return []

    found: list[int] = []
    try:
        b = smbus2.SMBus(bus)
        try:
            for addr in range(0x03, 0x78):
                try:
                    b.read_byte(addr)
                    found.append(addr)
                except OSError:
                    pass
        finally:
            b.close()
    except OSError as e:
        print(f"  [!] Could not open I2C bus {bus}: {e}")
        _i2c_diagnostic(e, bus)
    except Exception as e:
        print(f"  [!] Could not open I2C bus {bus}: {e}")
    return found


def run_i2c_scan(bus: int) -> None:
    section("STEP 1 — I2C scan")
    print(f"\n  Scanning bus {bus} for all devices (0x03–0x77) ...")
    found = scan_i2c(bus)

    if not found:
        print("  No devices found (or smbus2 unavailable).")
        return

    print(f"\n  Found:    {[hex(a) for a in found]}")
    print(f"  Expected: {[hex(a) for a in INA219_ADDRESSES]}  (CH1–CH4 INA219)")
    print(f"  ADS1115:  {hex(ADS1115_ADDRESS)} (reference ADC)")

    missing = [a for a in INA219_ADDRESSES if a not in found]
    extra = [a for a in found if a not in INA219_ADDRESSES]

    if not missing:
        print("  ✓ All 4 expected INA219 addresses present")
    else:
        print(f"\n  ✗ MISSING: {[hex(a) for a in missing]}")
        print("    • Address jumpers (A0/A1), power 3.3 V, SDA/SCL, or address clash")

    if ADS1115_ADDRESS in found:
        print(f"  ✓ ADS1115 present at {hex(ADS1115_ADDRESS)}")
    else:
        print(f"\n  ✗ ADS1115 not found at {hex(ADS1115_ADDRESS)} — reference ADC path will fail")
        if cfg is not None:
            mxa = getattr(cfg, "I2C_MUX_ADDRESS", None)
            mxc = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
            if mxa is not None and mxc is not None:
                print(
                    f"    • If ADS1115 is on TCA9548A port {mxc}, it only appears after mux "
                    "select — run STEP 3 or `python3 hw_probe.py --ads1115-only`."
                )

    if extra:
        print(f"\n  ? Other addresses: {[hex(a) for a in extra]}")


# ---------------------------------------------------------------------------
# STEP 2 — INA219 (smbus2 / i2c_bench)
# ---------------------------------------------------------------------------


def run_ina219_reads(bus: int, shunt_ohms: float, *, force_init: bool) -> None:
    from i2c_bench import INA219_DEFAULT_CONFIG_WORD, ina219_read, ina219_write_config

    section("STEP 2 — Raw INA219 reads (smbus2)")
    print(f"""
  Shunt resistance: {shunt_ohms} Ω  (--shunt N to override)
  bus_v    = voltage at IN− (load side of shunt)
  mA       = shunt voltage ÷ {shunt_ohms} Ω
""")

    try:
        import smbus2

        sm = smbus2.SMBus(bus)
    except OSError as e:
        print(f"  [!] Cannot open bus: {e}")
        _i2c_diagnostic(e, bus)
        return
    except ImportError:
        print("  [!] smbus2 missing")
        return

    readings: dict[int, dict] = {}
    try:
        if force_init:
            print(
                f"  (--init) writing INA219 CONFIG 0x{INA219_DEFAULT_CONFIG_WORD:04X} "
                "(pi-ina219 RANGE_16V / PGA÷1 / 128×ADC) on each channel …"
            )
            for ch, addr in enumerate(INA219_ADDRESSES):
                _mux_select_anode_for_probe(sm, ch)
                try:
                    ina219_write_config(sm, addr, INA219_DEFAULT_CONFIG_WORD)
                    time.sleep(0.015)
                except OSError as e:
                    print(f"  [!] init 0x{addr:02X}: {e}")
        for ch, addr in enumerate(INA219_ADDRESSES):
            _mux_select_anode_for_probe(sm, ch)
            readings[addr] = ina219_read(sm, addr, shunt_ohms)
    finally:
        sm.close()

    print()
    print(f"  {'CH':<4} {'Addr':<6} {'Bus V':>8} {'Shunt mV':>10} {'mA':>10} {'mW':>10}  Status")
    print("  " + "─" * 58)
    for ch, addr in enumerate(INA219_ADDRESSES):
        label = f"CH{ch + 1}"
        r = readings.get(addr, {"ok": False, "error": "not read"})
        if r.get("ok"):
            print(
                f"  {label:<4} 0x{addr:02X}  "
                f"{r['bus_v']:>8.3f} "
                f"{r['shunt_mv']:>10.4f} "
                f"{r['current_ma']:>10.4f} "
                f"{r['power_mw']:>10.4f}  OK"
            )
        else:
            print(f"  {label:<4} 0x{addr:02X}  {'—':>8} {'—':>10} {'—':>10} {'—':>10}  FAIL: {r.get('error')}")


def run_continuous(bus: int, shunt_ohms: float, skip_ads: bool, *, force_init: bool) -> None:
    from i2c_bench import (
        INA219_DEFAULT_CONFIG_WORD,
        ads1115_read_single_ended,
        ina219_read,
        ina219_write_config,
    )

    section("CONTINUOUS MODE — Ctrl+C to stop")
    print("  INA219 four channels + ADS1115 AIN0 once per tick (unless --skip-ads).\n")

    try:
        import smbus2

        sm = smbus2.SMBus(bus)
    except OSError as e:
        print(f"  [!] Cannot open bus: {e}")
        _i2c_diagnostic(e, bus)
        return
    except ImportError:
        print("  [!] smbus2 missing")
        return

    ads_bus = None
    if not skip_ads:
        try:
            ads_bus = smbus2.SMBus(ADS1115_BUS)
        except OSError:
            ads_bus = None

    if force_init:
        print(
            f"  (--init) writing INA219 CONFIG 0x{INA219_DEFAULT_CONFIG_WORD:04X} "
            "(pi-ina219 parity) on each channel …"
        )
        for ch, addr in enumerate(INA219_ADDRESSES):
            _mux_select_anode_for_probe(sm, ch)
            try:
                ina219_write_config(sm, addr, INA219_DEFAULT_CONFIG_WORD)
                time.sleep(0.015)
            except OSError as e:
                print(f"  [!] init 0x{addr:02X}: {e}")

    tick = 0
    try:
        while True:
            tick += 1
            ts = time.strftime("%H:%M:%S")
            print(f"\n  [{ts}  tick {tick}]")
            print(f"  {'CH':<4} {'Bus V':>8} {'Shunt mV':>10} {'mA':>10} {'mW':>10}  Status")
            print("  " + "─" * 52)
            for ch, addr in enumerate(INA219_ADDRESSES):
                label = f"CH{ch + 1}"
                _mux_select_anode_for_probe(sm, ch)
                r = ina219_read(sm, addr, shunt_ohms)
                if r.get("ok"):
                    print(
                        f"  {label:<4} {r['bus_v']:>8.3f} "
                        f"{r['shunt_mv']:>10.4f} "
                        f"{r['current_ma']:>10.4f} "
                        f"{r['power_mw']:>10.4f}  OK"
                    )
                else:
                    print(f"  {label:<4} {'—':>8} {'—':>10} {'—':>10} {'—':>10}  {r.get('error')}")

            if ads_bus is not None:
                try:
                    from i2c_bench import mux_select_on_bus

                    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None) if cfg else None
                    mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None) if cfg else None
                    mux_select_on_bus(ads_bus, mux_addr, mux_ch)
                    v0 = ads1115_read_single_ended(
                        ads_bus, ADS1115_ADDRESS, 0, ADS1115_FSR_V
                    )
                    print(f"  ADS  AIN0   {v0:>8.4f} V   (FSR ±{ADS1115_FSR_V} V)")
                except OSError as e:
                    print(f"  ADS  read error: {e}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        sm.close()
        if ads_bus is not None:
            ads_bus.close()


# ---------------------------------------------------------------------------
# STEP 3 — ADS1115
# ---------------------------------------------------------------------------


def run_ads1115_reads(busnum: int, ads_address: int | None = None) -> None:
    from i2c_bench import ads1115_behind_i2c_mux, ads1115_read_single_ended, mux_select_on_bus

    addr = int(ADS1115_ADDRESS if ads_address is None else ads_address)
    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None) if cfg else None
    mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None) if cfg else None
    behind_mux = ads1115_behind_i2c_mux(
        int(mux_addr) if mux_addr is not None else None,
        int(mux_ch) if mux_ch is not None else None,
    )

    section("STEP 3 — ADS1115 (reference ADC)")
    print(
        f"\n  Bus {busnum}  address {hex(addr)}  "
        f"single-ended AIN0..AIN3  FSR ±{ADS1115_FSR_V} V\n"
    )

    try:
        import smbus2

        sm = smbus2.SMBus(busnum)
    except OSError as e:
        print(f"  [!] Cannot open bus: {e}")
        _i2c_diagnostic(e, busnum)
        return

    try:
        mux_select_on_bus(sm, mux_addr, mux_ch)
        found = scan_i2c(busnum)
        if addr not in found:
            if behind_mux:
                print(
                    f"  [!] {hex(addr)} not listed after TCA ch{mux_ch} select "
                    "(attempting ADC reads anyway)."
                )
            else:
                print(f"  [!] Skipping — {hex(addr)} not on bus scan.")
                return

        print(f"  {'AIN':<6} {'Volts':>10} {'mV':>10}")
        print("  " + "─" * 28)
        for ch in range(4):
            v = ads1115_read_single_ended(sm, addr, ch, ADS1115_FSR_V)
            print(f"  AIN{ch}   {v:>10.5f} {v * 1000.0:>10.2f}")
    except OSError as e:
        print(f"  [!] ADS1115 read failed: {e}")
        _i2c_diagnostic(e, busnum)
    finally:
        sm.close()


# ---------------------------------------------------------------------------
# STEP 4 — DS18B20
# ---------------------------------------------------------------------------


def run_ds18b20_probe() -> None:
    section("STEP 4 — DS18B20 temperature (1-Wire)")
    paths = sorted(glob.glob("/sys/bus/w1/devices/28-*/w1_slave"))
    if not paths:
        print("  No /sys/bus/w1/devices/28-* — enable 1-Wire (raspi-config) or skip.")
        return
    for p in paths:
        try:
            lines = open(p, encoding="utf-8").read().strip().splitlines()
            if len(lines) < 2 or "YES" not in lines[0]:
                print(f"  {p}: no valid reading yet")
                continue
            i = lines[1].find("t=")
            if i < 0:
                continue
            c_centi = int(lines[1][i + 2 :])
            c = c_centi / 1000.0
            f = c * 9.0 / 5.0 + 32.0
            print(f"  {p}:  {c:.2f} °C   {f:.1f} °F")
        except OSError as e:
            print(f"  {p}: {e}")


# ---------------------------------------------------------------------------
# STEP 5 — PWM
# ---------------------------------------------------------------------------


def run_pwm_test() -> None:
    section("STEP 5 — PWM GPIO test")

    try:
        import RPi.GPIO as GPIO  # noqa: N814
    except ImportError:
        print("  [!] RPi.GPIO not available. Run on the Pi itself.")
        return

    duty_steps = [0, 10, 25, 50, 75]

    print(f"""
  Testing pins BCM {PWM_PINS_BCM} one at a time at {PWM_FREQ_HZ} Hz.

  At each duty level:
    Gate (GPIO)  → meter ≈ duty% × {GPIO_HIGH_V:.1f} V average
    Anode        → meter ≈ duty% × {SUPPLY_V:.1f} V if MOSFET switches
""")

    GPIO.setmode(GPIO.BCM)

    for ch_idx, pin in enumerate(PWM_PINS_BCM):
        section(f"  CH{ch_idx + 1} — BCM pin {pin}")
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
        pwm = GPIO.PWM(pin, PWM_FREQ_HZ)
        pwm.start(0)

        print(f"\n  Probe: CH{ch_idx + 1} MOSFET gate → GND\n")

        for duty in duty_steps:
            pwm.ChangeDutyCycle(duty)
            gate_v = GPIO_HIGH_V * duty / 100.0
            anode_v = SUPPLY_V * duty / 100.0
            print(
                f"  Duty {duty:3d}%  →  gate ≈ {gate_v:.2f} V   anode avg ≈ {anode_v:.2f} V"
            )
            pause("           Measure now, then press Enter ...")

        pwm.stop()
        GPIO.output(pin, GPIO.LOW)
        GPIO.setup(pin, GPIO.IN)
        print(f"\n  CH{ch_idx + 1} done — pin LOW then INPUT.")

        if ch_idx < len(PWM_PINS_BCM) - 1:
            next_pin = PWM_PINS_BCM[ch_idx + 1]
            pause(
                f"\n  Move meter to CH{ch_idx + 2} gate (BCM {next_pin}) → GND, then Enter..."
            )

    GPIO.cleanup()
    print("\n  All PWM pins tested.")


def _hex_int(s: str) -> int:
    return int(s, 0)


def print_summary() -> None:
    section("Summary — confirm before iccp -start / main.py")
    print("""
  INA219: 4 addresses on scan; bus_v sensible; mA tracks load
  ADS1115: AIN0..3 read without error (reference on AIN0 typically)
  DS18B20: optional — 28-* in sysfs when 1-Wire enabled
  PWM: gate follows duty × 3.3 V; anode follows if MOSFET correct
""")


def main() -> int:
    ap = argparse.ArgumentParser(description="CoilShield hardware probe (smbus2)")
    ap.add_argument("--bus", type=int, default=I2C_BUS, help="I2C bus for INA219 (default 1)")
    ap.add_argument(
        "--ads-bus",
        type=int,
        default=None,
        help="I2C bus for ADS1115 (default: same as --bus / settings ADS1115_BUS)",
    )
    ap.add_argument("--shunt", type=float, default=SHUNT_OHMS, help="Shunt Ω (default 0.1)")
    ap.add_argument("--skip-ina", action="store_true", help="Skip INA219 steps")
    ap.add_argument("--skip-ads", action="store_true", help="Skip ADS1115 step")
    ap.add_argument("--skip-temp", action="store_true", help="Skip DS18B20 step")
    ap.add_argument("--skip-pwm", action="store_true", help="Skip PWM GPIO test")
    ap.add_argument("--continuous", action="store_true", help="Live INA219 + ADS AIN0 table")
    ap.add_argument(
        "--ads1115",
        nargs="?",
        const="0x48",
        default=None,
        type=_hex_int,
        metavar="ADDR",
        help="Quick ADS1115 AIN0..3 read then exit (default ADDR 0x48).",
    )
    ap.add_argument(
        "--ads1115-only",
        action="store_true",
        help="Same as --ads1115 using address from config/settings.py",
    )
    ap.add_argument(
        "--init",
        action="store_true",
        help="Force INA219 CONFIG write (same word as pi-ina219 RANGE_16V defaults) on each channel",
    )
    args = ap.parse_args()
    ads_bus = args.ads_bus if args.ads_bus is not None else ADS1115_BUS

    print("\n┌─────────────────────────────────────────────────────────┐")
    print("│   CoilShield hw_probe — smbus2 INA219 + ADS1115        │")
    print("│   Ctrl+C exits cleanly                                   │")
    print("└─────────────────────────────────────────────────────────┘")
    print(
        f"\n  INA219 bus: {args.bus}   ADS bus: {ads_bus}   "
        f"Shunt: {args.shunt} Ω   Supply assumed: {SUPPLY_V} V"
    )

    try:
        if args.ads1115 is not None:
            run_ads1115_reads(ads_bus, ads_address=args.ads1115)
            return 0
        if args.ads1115_only:
            run_ads1115_reads(ads_bus, ads_address=None)
            return 0

        if args.continuous:
            run_continuous(
                args.bus,
                args.shunt,
                skip_ads=args.skip_ads,
                force_init=args.init,
            )
            return 0

        if not args.skip_ina:
            run_i2c_scan(args.bus)
            run_ina219_reads(args.bus, args.shunt, force_init=args.init)

        if not args.skip_ads:
            run_ads1115_reads(ads_bus)

        if not args.skip_temp:
            run_ds18b20_probe()

        if not args.skip_pwm:
            pause("\n  Ready for PWM test? Enter...")
            run_pwm_test()

        print_summary()

    except KeyboardInterrupt:
        _safe_gpio_cleanup()
        print("\nAborted.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
