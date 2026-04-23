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

Launch (after ``pip install -e .`` from repo root):  iccp probe [flags]

Useful flags:
  • --continuous / --live  All INA + ADS AIN0..3 every ``--interval`` s until Ctrl+C.
  • --ads1115 [ADDR]   Quick ADS1115 AIN0..3 only (default ADDR 0x48); plan checklist.
  • --init             Force INA219 CONFIG write on each channel before reads.
  • --ads1115-only     Same as --ads1115 with address from config/settings.

Pi tips:
  • Prefer **no sudo** and add user to **i2c** group:  sudo usermod -aG i2c $USER
    then log out/in.  Then:  iccp probe
  • If you must use sudo, use the **same** Python that has smbus2:
      sudo $(which iccp) probe

Direct execution (``python3 hw_probe.py``) is not supported — it prints a redirect and
exits. The module stays importable so ``iccp probe`` can drive it.
"""

from __future__ import annotations

import argparse
import getpass
import glob
import math
import os
import sys
import time
from dataclasses import dataclass, field

try:
    import config.settings as cfg
except ImportError:
    cfg = None  # type: ignore[assignment]

try:
    from channel_labels import anode_label
except ImportError:
    # Standalone copy without full tree (unlikely in-repo).
    def anode_label(ch: int) -> str:  # type: ignore[misc]
        return f"Anode {ch + 1} (idx {ch})"

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


def _mux_select_anode_for_probe(sm: object, ch_index: int) -> bool:
    """TCA9548A: match sensors.py — per-channel port or one legacy port before INA219 I/O.

    Returns False if the mux control write failed (e.g. EIO/ETIMEDOUT) after retry.
    """
    if cfg is None:
        return True
    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    if mux_addr is None:
        return True
    from i2c_bench import mux_select_on_bus

    per = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
    leg = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
    if per is not None and ch_index < len(per):
        port = int(per[ch_index])
    elif leg is not None:
        port = int(leg)
    else:
        return True
    trans = (5, 121, 110)
    if cfg is not None:
        try:
            trans = tuple(
                int(x) for x in getattr(cfg, "I2C_TRANSIENT_ERRNOS", (5, 121, 110))
            )
        except Exception:
            pass
    for attempt in range(2):
        try:
            mux_select_on_bus(sm, int(mux_addr), port)
            return True
        except OSError as e:
            en = getattr(e, "errno", None)
            if en is not None and int(en) in trans and attempt == 0:
                time.sleep(0.003)
                continue
            return False
    return False


def _format_z_ohm_effective(bus_v: float, current_ma: float) -> str:
    """Effective |Vbus|/|I| in Ω (same idea as main controller impedance proxy)."""
    if not math.isfinite(bus_v) or not math.isfinite(current_ma):
        return "—"
    if abs(current_ma) < 0.0005:
        return "—"
    i_a = abs(current_ma) / 1000.0
    if i_a < 1e-12:
        return "—"
    z = abs(bus_v) / i_a
    if not math.isfinite(z) or z < 0:
        return "—"
    if z > 1e9:
        return f"{z:.2e} Ω"
    if z >= 1e6:
        return f"{z / 1e6:.2f} MΩ"
    if z >= 1e3:
        return f"{z / 1e3:.2f} kΩ"
    return f"{z:.1f} Ω"


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
    print("      • Or use the venv's iccp binary:  sudo $(which iccp) probe")


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


def _i2c_ping_device(sb: object, addr: int) -> bool:
    """True if `read_byte(addr)` succeeds (device acknowledges)."""
    try:
        sb.read_byte(int(addr))  # type: ignore[union-attr]
        return True
    except OSError:
        return False


@dataclass
class MuxDownstreamI2CResult:
    """Per-port pings after TCA9548A select (mirrors `sensors.py` / `i2c_bench.mux_select_on_bus`)."""

    ina_rows: list[tuple[int, int, int, bool]] = field(
        default_factory=list
    )  # (iccp_ch, tca_ch, ina_addr, ok)
    ads_tca_ch: int | None = None
    ads_addr: int = 0x48
    ads_ok: bool = False
    ads_checked: bool = False
    error: str | None = None


def mux_downstream_i2c_probe(bus: int) -> MuxDownstreamI2CResult | None:
    """
    When ``I2C_MUX_ADDRESS`` is set, INA219/ADS are invisible on an idle bus scan
    until each TCA downstream port is selected. Ping expected devices per
    ``I2C_MUX_CHANNELS_INA219`` / ``I2C_MUX_CHANNEL_INA219`` and
    ``I2C_MUX_CHANNEL_ADS1115`` from config.
    """
    if cfg is None:
        return None
    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None)
    if mux_addr is None:
        return None
    try:
        import smbus2
    except ImportError:
        return None
    from i2c_bench import mux_select_on_bus

    out = MuxDownstreamI2CResult(ads_addr=int(ADS1115_ADDRESS))
    try:
        sb = smbus2.SMBus(bus)
    except OSError as e:
        out.error = f"Could not open I2C bus {bus} for downstream probe: {e}"
        return out

    per = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
    leg = getattr(cfg, "I2C_MUX_CHANNEL_INA219", None)
    mxa = int(mux_addr)

    try:
        if per is not None and len(per) > 0:
            n = min(len(INA219_ADDRESSES), len(per))
            for ch in range(n):
                mux_select_on_bus(sb, mxa, int(per[ch]))
                addr = int(INA219_ADDRESSES[ch])
                ok = _i2c_ping_device(sb, addr)
                out.ina_rows.append((ch, int(per[ch]), addr, ok))
        elif leg is not None:
            mux_select_on_bus(sb, mxa, int(leg))
            for ch, addr in enumerate(INA219_ADDRESSES):
                ok = _i2c_ping_device(sb, int(addr))
                out.ina_rows.append((ch, int(leg), int(addr), ok))

        ads_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
        if ads_ch is not None:
            mux_select_on_bus(sb, mxa, int(ads_ch))
            out.ads_tca_ch = int(ads_ch)
            out.ads_addr = int(ADS1115_ADDRESS)
            out.ads_ok = _i2c_ping_device(sb, int(ADS1115_ADDRESS))
            out.ads_checked = True
    except OSError as e:
        out.error = f"downstream I2C error: {e}"
    except ValueError as e:
        out.error = str(e)
    finally:
        try:
            sb.write_byte(mxa, 0x00)
        except OSError:
            pass
        try:
            sb.close()
        except OSError:
            pass

    return out


def run_i2c_scan(bus: int) -> None:
    section("STEP 1 — I2C scan")
    print(
        f"\n  Scanning bus {bus} for all devices (0x03–0x77) without mux select (idle) …"
    )
    found = scan_i2c(bus)

    if not found:
        print("  No devices found (or smbus2 unavailable).")
        return

    mxa = getattr(cfg, "I2C_MUX_ADDRESS", None) if cfg is not None else None
    print(f"\n  Found (idle, upstream bus):  {[hex(a) for a in found]}")
    print(
        f"  Expected INA219 addrs: {[hex(a) for a in INA219_ADDRESSES]}  "
        "(Anode 1–4; idx 0–3 in firmware)"
    )
    print(f"  ADS1115:  {hex(ADS1115_ADDRESS)} (reference ADC)")

    if mxa is not None:
        mux_r = mux_downstream_i2c_probe(bus)
        if mux_r is not None and (
            mux_r.ina_rows
            or mux_r.ads_checked
            or (mux_r.error and not (mux_r.ina_rows or mux_r.ads_checked))
        ):
            print(
                "\n  TCA9548A is configured (I2C_MUX_ADDRESS). INA/ADS on downstream "
                "ports are invisible in the idle scan — checking each port from config…"
            )
            if mux_r.error:
                print(f"  [!] {mux_r.error}")
            if mux_r.ina_rows or mux_r.ads_checked:
                section("STEP 1b — I2C downstream (TCA9548A per port)")
            for ch, tca_ch, ina_addr, ok in mux_r.ina_rows:
                st = "✓" if ok else "✗"
                print(
                    f"  {st}  Anode idx {ch}  TCA ch{tca_ch}  INA@ {hex(ina_addr)}"
                )
            if mux_r.ads_checked and mux_r.ads_tca_ch is not None:
                st = "✓" if mux_r.ads_ok else "✗"
                print(
                    f"  {st}  ADS1115 @ {hex(ADS1115_ADDRESS)}  "
                    f"(TCA ch{mux_r.ads_tca_ch} — from config)"
                )
            if mux_r.ina_rows:
                if all(r[3] for r in mux_r.ina_rows):
                    print("  ✓ All configured anode INA219 addresses respond behind mux")
                else:
                    mina = [hex(r[2]) for r in mux_r.ina_rows if not r[3]]
                    print(f"\n  ✗ MISSING (behind mux): {mina}")
                    print(
                        "    • Per-port wiring, A0/A1, power, or TCA ch vs "
                        "I2C_MUX_CHANNELS_INA219 in config.settings"
                    )
            if mux_r.ads_checked:
                if mux_r.ads_ok:
                    print(
                        f"  ✓ ADS1115 responds at {hex(ADS1115_ADDRESS)} (behind mux)"
                    )
                else:
                    print(
                        f"\n  ✗ ADS1115 not at {hex(ADS1115_ADDRESS)} on selected "
                        "TCA ch — reference ADC path will fail"
                    )
            if (
                mux_r.ina_rows
                and not mux_r.ads_checked
                and getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None) is None
                and ADS1115_ADDRESS not in found
            ):
                print(
                    f"\n  (ADS: I2C_MUX_CHANNEL_ADS1115 unset — "
                    f"set it if ADS is on a downstream port; idle bus did not show "
                    f"{hex(ADS1115_ADDRESS)}.)"
                )
            if (
                mxa is not None
                and (mux_r.ina_rows or mux_r.ads_checked)
                and not (
                    (not mux_r.ina_rows)
                    and not mux_r.ads_checked
                    and mux_r.error
                )
            ):
                maddr = int(mxa)
                other = [a for a in found if a not in INA219_ADDRESSES and a != maddr]
                if other:
                    print(
                        f"\n  ? Other addresses (idle, upstream bus): "
                        f"{[hex(a) for a in other]}"
                    )
            if mux_r.ina_rows or mux_r.ads_checked:
                return
            if mux_r.error:
                return

    missing = [a for a in INA219_ADDRESSES if a not in found]
    extra: list[int] = [a for a in found if a not in INA219_ADDRESSES]
    if mxa is not None:
        extra = [a for a in extra if a != int(mxa)]

    if not missing:
        print("  ✓ All 4 expected INA219 addresses present (idle / no-mux scan)")
    else:
        print(f"\n  ✗ MISSING: {[hex(a) for a in missing]}")
        print(
            "    • Address jumpers (A0/A1), power 3.3 V, SDA/SCL, or address clash; "
            "on TCA rigs, confirm I2C_MUX_* in config and see STEP 1b if shown above."
        )

    if ADS1115_ADDRESS in found:
        print(f"  ✓ ADS1115 present at {hex(ADS1115_ADDRESS)} (idle bus)")
    else:
        print(
            f"\n  ✗ ADS1115 not found at {hex(ADS1115_ADDRESS)} in idle scan — "
            "reference only works if the chip is on this bus or use STEP 1b / "
            "STEP 3 (mux-aware)."
        )
        if cfg is not None:
            cads = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
            cmx = getattr(cfg, "I2C_MUX_ADDRESS", None)
            if cmx is not None and cads is not None:
                print(
                    f"    • If ADS1115 is on TCA9548A port {cads}, run "
                    f"`iccp probe --ads1115-only` or use STEP 3 (mux select in probe)."
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
                if not _mux_select_anode_for_probe(sm, ch):
                    print(
                        f"  [!] mux before init 0x{addr:02X}: failed — "
                        "use one I2C user (e.g. stop `iccp`); skipping CONFIG for this ch"
                    )
                    continue
                try:
                    ina219_write_config(sm, addr, INA219_DEFAULT_CONFIG_WORD)
                    time.sleep(0.015)
                except OSError as e:
                    print(f"  [!] init 0x{addr:02X}: {e}")
        for ch, addr in enumerate(INA219_ADDRESSES):
            if not _mux_select_anode_for_probe(sm, ch):
                readings[addr] = {
                    "ok": False,
                    "error": "TCA9548A mux select failed (EIO) — one process on I2C? try: systemctl stop iccp",
                }
            else:
                readings[addr] = ina219_read(sm, addr, shunt_ohms)
    finally:
        sm.close()

    print()
    print(
        f"  {'Anode':<14} {'Addr':<6} {'Bus V':>8} {'Shunt mV':>10} {'mA':>10} {'mW':>10}  Status"
    )
    print("  " + "─" * 58)
    for ch, addr in enumerate(INA219_ADDRESSES):
        label = anode_label(ch)
        r = readings.get(addr, {"ok": False, "error": "not read"})
        if r.get("ok"):
            print(
                f"  {label:<14} 0x{addr:02X}  "
                f"{r['bus_v']:>8.3f} "
                f"{r['shunt_mv']:>10.4f} "
                f"{r['current_ma']:>10.4f} "
                f"{r['power_mw']:>10.4f}  OK"
            )
        else:
            print(
                f"  {label:<14} 0x{addr:02X}  {'—':>8} {'—':>10} {'—':>10} {'—':>10}  "
                f"FAIL: {r.get('error')}"
            )


def run_continuous(
    bus: int,
    shunt_ohms: float,
    skip_ads: bool,
    *,
    force_init: bool,
    interval_s: float = 1.0,
) -> None:
    from i2c_bench import (
        INA219_DEFAULT_CONFIG_WORD,
        ads1115_read_single_ended,
        ina219_read,
        ina219_write_config,
    )

    ref_ain = int(getattr(cfg, "ADS1115_CHANNEL", 0) if cfg is not None else 0)
    section("LIVE / CONTINUOUS — Ctrl+C to stop")
    if skip_ads:
        print(f"  Every {interval_s:.2f} s: INA219 only (no ADS).")
    else:
        print(
            f"  Every {interval_s:.2f} s: all INA219 ch + ADS1115 AIN0..3 "
            f"(firmware ref = AIN{ref_ain})."
        )
    print()

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
            if not _mux_select_anode_for_probe(sm, ch):
                print(
                    f"  [!] mux before init 0x{addr:02X}: failed (EIO?) — stop other I2C users"
                )
                continue
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
            print(
                f"  {'Anode':<14} {'Bus V':>8} {'Shunt mV':>10} {'mA':>10} {'mW':>10}  Status"
            )
            print("  " + "─" * 52)
            for ch, addr in enumerate(INA219_ADDRESSES):
                label = anode_label(ch)
                if not _mux_select_anode_for_probe(sm, ch):
                    print(
                        f"  {label:<14} {'—':>8} {'—':>10} {'—':>10} {'—':>10}  "
                        f"mux EIO (stop `iccp`?)"
                    )
                    continue
                r = ina219_read(sm, addr, shunt_ohms)
                if r.get("ok"):
                    print(
                        f"  {label:<14} {r['bus_v']:>8.3f} "
                        f"{r['shunt_mv']:>10.4f} "
                        f"{r['current_ma']:>10.4f} "
                        f"{r['power_mw']:>10.4f}  OK"
                    )
                else:
                    print(
                        f"  {label:<14} {'—':>8} {'—':>10} {'—':>10} {'—':>10}  {r.get('error')}"
                    )

            if ads_bus is not None:
                try:
                    from i2c_bench import mux_select_on_bus

                    mux_addr = getattr(cfg, "I2C_MUX_ADDRESS", None) if cfg else None
                    mux_ch = getattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None) if cfg else None
                    mux_select_on_bus(ads_bus, mux_addr, mux_ch)
                    print(
                        f"  {'ADS':<6} {'Volts':>10} {'mV':>10}  (ref → AIN{ref_ain} per settings)"
                    )
                    print("  " + "─" * 38)
                    for ach in range(4):
                        v_ach = ads1115_read_single_ended(
                            ads_bus, ADS1115_ADDRESS, ach, ADS1115_FSR_V
                        )
                        tag = "  ← ref" if ach == ref_ain else ""
                        print(
                            f"  AIN{ach}  {v_ach:>10.5f} {v_ach * 1000.0:>10.2f}{tag}"
                        )
                except OSError as e:
                    print(f"  ADS  read error: {e}")
            time.sleep(max(0.05, float(interval_s)))
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
        if behind_mux:
            print(
                f"  • Mux: confirm `I2C_MUX_CHANNEL_ADS1115` ({mux_ch!r}) matches the ADS1115 "
                f"downstream branch; 0x48 is invisible until that port is selected."
            )
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


def run_pwm_test(
    bus: int,
    shunt_ohms: float,
    *,
    force_init: bool = False,
    read_ina219: bool = True,
) -> None:
    section("STEP 5 — PWM GPIO test")

    try:
        import RPi.GPIO as GPIO  # noqa: N814
    except ImportError:
        print("  [!] RPi.GPIO not available. Run on the Pi itself.")
        return

    from i2c_bench import INA219_DEFAULT_CONFIG_WORD, ina219_read, ina219_write_config

    sm = None
    ina_ok = False
    if read_ina219:
        try:
            import smbus2

            sm = smbus2.SMBus(bus)
            ina_ok = True
        except OSError as e:
            print(f"  [!] I2C bus {bus} not opened — INA219 line omitted: {e}")
        except ImportError:
            print("  [!] smbus2 not installed — INA219 line omitted")

    if ina_ok and sm is not None and force_init:
        print(
            f"  (--init) INA219 CONFIG 0x{INA219_DEFAULT_CONFIG_WORD:04X} before PWM …"
        )
        nch = min(len(PWM_PINS_BCM), len(INA219_ADDRESSES))
        for ch in range(nch):
            if not _mux_select_anode_for_probe(sm, ch):
                print(
                    f"  [!] mux before init 0x{INA219_ADDRESSES[ch]:02X}: EIO (skipped CONFIG)"
                )
                continue
            try:
                ina219_write_config(
                    sm, int(INA219_ADDRESSES[ch]), INA219_DEFAULT_CONFIG_WORD
                )
                time.sleep(0.015)
            except OSError as e:
                print(f"  [!] init 0x{INA219_ADDRESSES[ch]:02X}: {e}")

    duty_steps = [0, 10, 25, 50, 75]
    if ina_ok:
        ina_note = (
            "  After each step, INA219 (this anode) reports shunt mA, Vbus, "
            "and Z ≈ |Vbus|/|I| (effective Ω).\n"
        )
    elif not read_ina219:
        ina_note = "  (INA219 is skipped because --skip-ina.)\n"
    else:
        ina_note = (
            "  I2C bus not opened — use DMM for mA/Ω, or fix smbus2 and permissions.\n"
        )

    print(f"""
  Testing pins BCM {PWM_PINS_BCM} one at a time at {PWM_FREQ_HZ} Hz.

  At each duty level:
    Gate (GPIO)  → meter ≈ duty% × {GPIO_HIGH_V:.1f} V average
    Anode        → meter ≈ duty% × {SUPPLY_V:.1f} V if MOSFET switches
{ina_note}""")

    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        mux_eio_warned = False
        for ch_idx, pin in enumerate(PWM_PINS_BCM):
            ina_addr: int | None = None
            if ch_idx < len(INA219_ADDRESSES):
                ina_addr = int(INA219_ADDRESSES[ch_idx])

            section(f"  {anode_label(ch_idx)} — BCM pin {pin}")
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
            pwm = GPIO.PWM(pin, PWM_FREQ_HZ)
            pwm.start(0)

            print(f"\n  Probe: {anode_label(ch_idx)} MOSFET gate → GND\n")

            for duty in duty_steps:
                pwm.ChangeDutyCycle(duty)
                # Let soft-PWM and cell settle (several periods at 100 Hz default).
                time.sleep(0.22)
                gate_v = GPIO_HIGH_V * duty / 100.0
                anode_v = SUPPLY_V * duty / 100.0
                ina_str = ""
                if ina_ok and sm is not None and ina_addr is not None:
                    if not _mux_select_anode_for_probe(sm, ch_idx):
                        if not mux_eio_warned:
                            print(
                                "  [!] TCA9548A mux write failed (I²C EIO/timeout). Use one "
                                "I2C client: e.g.  sudo systemctl stop iccp  then re-run this "
                                "step, or use  --skip-pwm  /  --skip-ina. (See dmesg for i2c-1.)\n"
                            )
                            mux_eio_warned = True
                        ina_str = "   INA219: (mux EIO; PWM continues — use DMM for I/V)"
                    else:
                        time.sleep(0.02)
                        samples_ma: list[float] = []
                        samples_bv: list[float] = []
                        for _ in range(3):
                            if not _mux_select_anode_for_probe(sm, ch_idx):
                                break
                            r = ina219_read(sm, ina_addr, shunt_ohms)
                            if r.get("ok"):
                                samples_ma.append(float(r["current_ma"]))
                                samples_bv.append(float(r["bus_v"]))
                            time.sleep(0.04)
                        if samples_ma:
                            ma = sum(samples_ma) / len(samples_ma)
                            bv = sum(samples_bv) / len(samples_bv)
                            z_s = _format_z_ohm_effective(bv, ma)
                            ina_str = (
                                f"   shunt mA = {ma:8.3f}   Vbus = {bv:5.2f} V   Z ≈ {z_s}"
                            )
                        else:
                            ina_str = "   INA219: (no successful read)"
                elif read_ina219 and ina_addr is None:
                    ina_str = "   INA: (no address for this index)"

                print(
                    f"  Duty {duty:3d}%  →  gate ≈ {gate_v:.2f} V   anode avg ≈ {anode_v:.2f} V"
                    f"{ina_str}"
                )
                pause("           Measure now, then press Enter ...")

            pwm.stop()
            GPIO.output(pin, GPIO.LOW)
            GPIO.setup(pin, GPIO.IN)
            print(f"\n  {anode_label(ch_idx)} done — pin LOW then INPUT.")

            if ch_idx < len(PWM_PINS_BCM) - 1:
                next_pin = PWM_PINS_BCM[ch_idx + 1]
                pause(
                    f"\n  Move meter to {anode_label(ch_idx + 1)} gate (BCM {next_pin}) → GND, "
                    f"then Enter..."
                )

    finally:
        try:
            GPIO.cleanup()
        except Exception:
            pass
        if sm is not None:
            try:
                sm.close()
            except OSError:
                pass
    print("\n  All PWM pins tested.")


def _hex_int(s: str) -> int:
    return int(s, 0)


def print_summary() -> None:
    section("Summary — confirm before `iccp start`")
    print("""
  INA219: 4 addresses on scan; bus_v sensible; mA tracks load
  ADS1115: AIN0..3 read without error (reference on AIN0 typically)
  DS18B20: optional — 28-* in sysfs when 1-Wire enabled
  PWM: gate follows duty × 3.3 V; anode follows if MOSFET correct; INA219 mA/Ω on line
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
    ap.add_argument(
        "--continuous",
        "--live",
        action="store_true",
        dest="continuous",
        help="Stream all INA + ADS AIN0..3 every --interval s (Ctrl+C stops).",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Update period for --continuous / --live (default 1.0, min 0.05 s)",
    )
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
                interval_s=args.interval,
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
            run_pwm_test(
                args.bus,
                args.shunt,
                force_init=args.init,
                read_ina219=not args.skip_ina,
            )

        print_summary()

    except KeyboardInterrupt:
        _safe_gpio_cleanup()
        print("\nAborted.")
        return 1

    return 0


_DIRECT_EXEC_REDIRECT = (
    "Direct execution is not supported. Use the iccp CLI:\n"
    "  iccp start        # was: python3 main.py\n"
    "  iccp tui          # was: python3 tui.py\n"
    "  iccp probe        # was: python3 hw_probe.py\n"
    "  iccp dashboard    # was: python3 dashboard.py\n"
    "  iccp commission   # was: ad-hoc commissioning\n"
    "Install once with: pip install -e . (from repo root)\n"
)


if __name__ == "__main__":
    sys.stderr.write(_DIRECT_EXEC_REDIRECT)
    sys.exit(2)
