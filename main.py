#!/usr/bin/env python3
"""
CoilShield ICCP controller — main loop.

Set COILSHIELD_SIM=1 for simulator (default on macOS if unset).
Clear fault latch: `touch <PROJECT_ROOT>/clear_fault`
Commissioning reset: `python3 -c "import commissioning; commissioning.reset()"`
Sim speed: SIM_TIME_SCALE=10 or `python3 main.py --sim --sim-time-scale 60`
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def _default_sim_on_mac() -> None:
    if sys.platform == "darwin" and "COILSHIELD_SIM" not in os.environ:
        os.environ["COILSHIELD_SIM"] = "1"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CoilShield ICCP monitor/controller")
    p.add_argument("--sim", action="store_true", help="force simulator mode")
    p.add_argument("--real", action="store_true", help="force real hardware mode")
    p.add_argument(
        "--skip-commission",
        action="store_true",
        help="skip commissioning even if commissioning.json is absent",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print status table each tick",
    )
    p.add_argument(
        "--sim-time-scale",
        type=int,
        default=None,
        metavar="N",
        help="sim only: real seconds per simulated hour",
    )
    p.add_argument(
        "--set-native",
        type=float,
        default=None,
        metavar="MV",
        help="write native_mv to commissioning.json and exit (e.g. --set-native -232.0)",
    )
    return p.parse_args()


def _print_sim_schedule(sensor_module: object) -> None:
    scale = getattr(sensor_module, "SIM_REAL_S_PER_SIM_HOUR", 10.0)
    real_minutes = (86400.0 / (3600.0 / float(scale))) / 60.0
    print(
        f"[sim] 24-hour window → {real_minutes:.0f} real minutes "
        f"(SIM_TIME_SCALE={int(scale)})"
    )
    print("[sim] 10 cooling cycles:")
    cycles = getattr(sensor_module, "COOLING_CYCLES", ())
    for i, (s, e) in enumerate(cycles):
        duration = (e - s) // 60
        print(
            f"      {i + 1:2d}. {s // 3600:02d}:{(s % 3600) // 60:02d}"
            f"–{e // 3600:02d}:{(e % 3600) // 60:02d}  ({duration} min)"
        )
    print("[sim] Per-anode wet profiles:")
    params = getattr(sensor_module, "ANODE_WET_PARAMS", ())
    for ch, (wd, dd) in enumerate(params):
        print(
            f"      CH{ch + 1}: wets {wd // 60} min after cycle start, "
            f"dries {dd // 60} min after cycle stop"
        )
    print()


def _print_table(
    readings: dict,
    faults: list,
    duties: dict,
    latched: bool,
    ch_status: dict[int, str],
    any_wet: bool,
    ref_raw_mv: float,
    ref_shift: float | None,
    ref_band: str,
    ref_hw_line: str,
    temp_f: float | None,
    sim_line: str = "",
    z_median: dict[int, float | None] | None = None,
    live_ch: dict[str, object] | None = None,
    ctrl: object | None = None,
    tick_dt_s: float | None = None,
) -> None:
    try:
        if sim_line:
            print(sim_line)
        shift_str = (
            f"{ref_shift:+.1f} mV"
            if ref_shift is not None
            else "— (commissioning needed for shift)"
        )
        band_disp = ref_band if ref_shift is not None else "—"
        temp_str = f"{temp_f:.1f}°F" if temp_f is not None else "—"
        if ref_hw_line != "disabled":
            print(f"  Ref sensor: {ref_hw_line}")
            print(
                f"    raw={ref_raw_mv:.1f} mV  |  polarization shift={shift_str}  "
                f"|  shift_band={band_disp}    Temp: {temp_str}"
            )
        else:
            print(f"  Temp: {temp_str}")
        import config.settings as _cfg

        ts_disp: str | None = None
        if isinstance(live_ch, dict):
            raw_ts = live_ch.get("ts")
            if raw_ts is not None and str(raw_ts).strip():
                ts_disp = str(raw_ts).replace("T", " ")
        if not ts_disp:
            ts_disp = time.strftime("%Y-%m-%d %H:%M:%S")
        dt_suf = (
            f"  Δt={float(tick_dt_s):.3f}s"
            if tick_dt_s is not None and tick_dt_s >= 0
            else ""
        )
        print(f"[tick] {ts_disp}{dt_suf}")

        i_floor = float(getattr(_cfg, "Z_COMPUTE_I_A_MIN", 1e-6))
        w = 132
        if ctrl is not None and hasattr(ctrl, "channel_target_ma"):
            parts = [
                f"CH{i + 1}={ctrl.channel_target_ma(i):.3f}"
                for i in range(int(_cfg.NUM_CHANNELS))
            ]
            print(
                "  I_target (mA) — setpoint in PROTECTING; "
                "PWM% is actuator only: " + "  ".join(parts)
            )
        print("─" * w)
        print(
            f"{'CH':<4} {'State':<12} {'BusV':<8} {'mA':>8}  {'PWM%':<8} "
            f"{'Ω imp':<10} {'Ω med':<10} {'Vc':<8} {'Wet':<5} "
            f"{'P(W)':<9} {'E(J)':<10} {'η':<10}"
        )
        print("─" * w)
        for i in sorted(readings.keys()):
            r = readings[i]
            st = ch_status.get(i, "?")
            zm = z_median.get(i) if z_median else None
            ch_map = (
                live_ch.get("channels", {})
                if isinstance(live_ch, dict)
                else {}
            )
            chd = ch_map.get(str(i), {}) if isinstance(ch_map, dict) else {}
            if r.get("ok"):
                ma = float(r.get("current", 0))
                bus_v = float(r.get("bus_v", 0))
                duty = float(duties.get(i, 0))
                if ma > 0.01:
                    z_inst = bus_v / max(ma / 1000.0, i_floor)
                    imp_s = f"{z_inst:,.0f}"
                    zmed_s = f"{zm:,.0f}" if zm is not None else "—"
                else:
                    imp_s = "open"
                    zmed_s = "open" if zm is not None else "—"
                vc = round(bus_v * (duty / 100.0), 3)
                pw = chd.get("power_w")
                ej = chd.get("energy_today_j")
                eff = chd.get("efficiency_ma_per_pct")
                p_s = f"{float(pw):.4f}" if isinstance(pw, (int, float)) else "—"
                e_s = f"{float(ej):.2f}" if isinstance(ej, (int, float)) else "—"
                n_s = (
                    f"{float(eff):.3f}"
                    if isinstance(eff, (int, float))
                    else "—"
                )
                print(
                    f"{i + 1:<4} {st:<12} {bus_v:<8.3f} {ma:>8.2f}  {duty:<8.1f} "
                    f"{imp_s:<10} {zmed_s:<10} {vc:<8.3f} {int(st == 'PROTECTING'):<5} "
                    f"{p_s:<9} {e_s:<10} {n_s:<10}"
                )
            else:
                print(
                    f"{i + 1:<4} {st:<12} {'--':<8} {'--':>8}  {'--':<8} "
                    f"{'—':<10} {'—':<10} {'—':<8} {'—':<5} "
                    f"{'—':<9} {'—':<10} {'—':<10}"
                )
        print("─" * w)
        tpw = live_ch.get("total_power_w") if isinstance(live_ch, dict) else None
        tpw_s = f"{float(tpw):.4f}" if isinstance(tpw, (int, float)) else "—"
        pwm_mx = float(getattr(_cfg, "PWM_MAX_DUTY", 80.0))
        probe = float(getattr(_cfg, "DUTY_PROBE", 3.0))
        vsoft = float(getattr(_cfg, "VCELL_SOFT_MAX_V", 0.0) or 0.0)
        print(
            f"  PWM: WEAK/CONDUCTIVE ramp {probe:.0f}%→{pwm_mx:.0f}% "
            f"(no staging cap); Vc≈Bus×PWM/100  (soft Vcell ref {vsoft:.1f} V)"
        )
        print(
            f"  AnyWet={int(any_wet)}  Latch={int(latched)}  "
            f"ΣP={tpw_s} W  "
            f"Faults: {'; '.join(faults) or '—'}"
        )
    except BrokenPipeError:
        raise SystemExit(0) from None


def _print_ref_compact(
    ref_hw_line: str,
    ref_raw_mv: float,
    ref_shift: float | None,
    ref_band: str,
    ref_hint: str,
) -> None:
    """Single-line ref summary for non-verbose mode (same cadence as LOG_INTERVAL_S)."""
    shift_str = (
        f"{ref_shift:+.1f} mV"
        if ref_shift is not None
        else "— (commissioning needed for shift)"
    )
    band_disp = ref_band if ref_shift is not None else "—"
    hint = f"  |  {ref_hint}" if ref_hint else ""
    print(
        f"[ref] {ref_hw_line}  |  raw={ref_raw_mv:.1f} mV  |  shift={shift_str}  "
        f"|  band={band_disp}{hint}"
    )


def main() -> int:
    _default_sim_on_mac()
    args = _parse_args()
    if args.sim:
        os.environ["COILSHIELD_SIM"] = "1"
    if args.real:
        os.environ["COILSHIELD_SIM"] = "0"
    if args.sim_time_scale is not None:
        os.environ["SIM_TIME_SCALE"] = str(args.sim_time_scale)

    import config.settings as cfg

    if args.set_native is not None:
        from reference import ReferenceElectrode
        ref = ReferenceElectrode()
        ref.save_native(args.set_native)
        print(
            f"[main] native_mv set to {args.set_native:.2f} mV "
            f"→ {cfg.PROJECT_ROOT / 'commissioning.json'}"
        )
        return 0

    import commissioning
    import sensors
    import temp as temp_mod
    from control import Controller
    from logger import DataLogger
    from reference import ReferenceElectrode, ref_hw_message, ref_hw_ok, ref_ux_hint

    from leds import StatusLEDs

    sim = sensors.SIM_MODE
    use_hw_gpio = not sim

    if use_hw_gpio:
        try:
            import RPi.GPIO as GPIO  # noqa: N814

            GPIO.setmode(GPIO.BCM)
        except ImportError:
            print(
                "RPi.GPIO not available — use --sim or COILSHIELD_SIM=1",
                file=sys.stderr,
            )
            return 1

    sim_state = sensors.SimSensorState() if sim else None
    ctrl = Controller()
    ref = ReferenceElectrode()
    leds = StatusLEDs(use_hw_gpio)
    log = DataLogger()

    leds.setup()

    print(
        f"CoilShield starting (sim={sim}, TARGET_MA={cfg.TARGET_MA}, "
        f"clear fault: touch {cfg.CLEAR_FAULT_FILE})"
    )
    print(f"[main] Reference path: {ref_hw_message()}")

    if sim:
        _print_sim_schedule(sensors)

    if not args.skip_commission:
        if commissioning.needs_commissioning():
            print("[main] No commissioning data. Starting commissioning sequence...")
            print("[main] (use --skip-commission to bypass for bench testing)")
            commissioned_target = commissioning.run(
                ref, ctrl, sim_state=sim_state, verbose=True
            )
            cfg.TARGET_MA = commissioned_target
        else:
            ref.load_native()
            cfg.TARGET_MA = commissioning.load_commissioned_target()
            print(
                f"[main] Commissioning loaded — native={ref.native_mv:.1f} mV  "
                f"target={cfg.TARGET_MA:.3f} mA"
            )
    else:
        ref.load_native()
        if not commissioning.needs_commissioning():
            cfg.TARGET_MA = commissioning.load_commissioned_target()
        print(
            f"[main] Commissioning skipped. native_mv="
            f"{'set' if ref.native_mv is not None else 'not set'}"
        )

    outer_loop_counter = 0
    outer_loop_interval = max(
        1, int(cfg.LOG_INTERVAL_S / cfg.SAMPLE_INTERVAL_S)
    )
    _ux_tip_shown = False
    _thermal_paused = False
    _prev_verbose_mono: float | None = None

    try:
        while True:
            temp_f = temp_mod.read_fahrenheit()
            if not temp_mod.in_operating_range(temp_f):
                ctrl.thermal_off()
                log.feed_cooling_cycle(
                    in_band=False,
                    ts_unix=time.time(),
                    dt_s=cfg.SAMPLE_INTERVAL_S,
                    ch_status=ctrl.channel_statuses(),
                    temp_f=temp_f,
                )
                if not _thermal_paused:
                    reason = (
                        "too cold — possible freeze"
                        if temp_f is not None and temp_f < temp_mod.TEMP_MIN_F
                        else "too hot — heat mode?"
                    )
                    print(
                        f"[main] THERMAL PAUSE {temp_f}°F outside "
                        f"[{temp_mod.TEMP_MIN_F}–{temp_mod.TEMP_MAX_F}°F]: {reason}"
                    )
                    _thermal_paused = True
                time.sleep(cfg.SAMPLE_INTERVAL_S)
                continue

            if _thermal_paused:
                print(f"[main] Temp restored ({temp_f}°F) — resuming.")
                _thermal_paused = False

            if sim:
                readings = sensors.read_all_sim(sim_state)  # type: ignore[arg-type]
            else:
                readings = sensors.read_all_real()

            faults, fault_latched = ctrl.update(readings)
            duties = ctrl.duties()
            ch_status = ctrl.channel_statuses()
            any_wet = ctrl.any_wet()

            if sim and sim_state is not None:
                sim_state.duties = dict(duties)

            ref_raw_mv, ref_shift = ref.read_raw_and_shift(
                duties=duties, statuses=ch_status
            )
            ref_band = (
                ref.protection_status(ref_shift)
                if ref_shift is not None
                else "—"
            )

            if not _ux_tip_shown:
                tip = ref_ux_hint(
                    baseline_set=ref.native_mv is not None,
                    hw_ok=ref_hw_ok(),
                    skip_commission=args.skip_commission,
                )
                if tip:
                    print(f"[main] {tip}")
                _ux_tip_shown = True

            outer_loop_counter += 1
            ref_log_tick = False
            if outer_loop_counter >= outer_loop_interval:
                ctrl.update_potential_target(ref_shift)
                outer_loop_counter = 0
                ref_log_tick = True

            sim_time_kw: str | None = None
            if sim and sim_state is not None:
                sim_time_kw = sim_state.sim_hhmm()
            ref_hint = ref_ux_hint(
                baseline_set=ref.native_mv is not None,
                hw_ok=ref_hw_ok(),
                skip_commission=args.skip_commission,
            )
            ref_hw_line = ref_hw_message()
            ref_baseline_set = ref.native_mv is not None
            log.feed_cooling_cycle(
                in_band=True,
                ts_unix=time.time(),
                dt_s=cfg.SAMPLE_INTERVAL_S,
                ch_status=ch_status,
                temp_f=temp_f,
            )
            live_snap = log.record(
                readings,
                any_wet,
                faults,
                duties,
                fault_latched,
                ch_status,
                sim_time=sim_time_kw,
                ref_shift_mv=ref_shift,
                ref_status=ref_band if ref_shift is not None else "N/A",
                temp_f=temp_f,
                ref_raw_mv=ref_raw_mv,
                ref_hw_ok=ref_hw_ok(),
                ref_hint=ref_hint or None,
                ref_hw_message=ref_hw_line,
                ref_baseline_set=ref_baseline_set,
            )
            log.maybe_flush()

            if ref_log_tick and not args.verbose:
                _print_ref_compact(
                    ref_hw_line,
                    ref_raw_mv,
                    ref_shift,
                    ref_band,
                    ref_hint,
                )

            leds.set_running_ok(not fault_latched and len(faults) == 0)

            if args.verbose:
                sim_line = ""
                if sim and sim_state is not None:
                    sim_s = sim_state.sim_seconds()
                    cycle = sim_state.active_cycle(sim_s)
                    cycle_str = (
                        f"cycle {cycle}/10 ACTIVE" if cycle else "between cycles"
                    )
                    wet_map = sim_state.wet_map(sim_s)
                    sim_line = (
                        f"[sim {sim_state.sim_hhmm()}] {cycle_str:<24} "
                        f"anodes: {wet_map}  (W=wet  .=dry)"
                    )
                now_v = time.monotonic()
                v_dt = (
                    None
                    if _prev_verbose_mono is None
                    else (now_v - _prev_verbose_mono)
                )
                _prev_verbose_mono = now_v
                _print_table(
                    readings,
                    faults,
                    duties,
                    fault_latched,
                    ch_status,
                    any_wet,
                    ref_raw_mv,
                    ref_shift,
                    ref_band,
                    ref_hw_message(),
                    temp_f,
                    sim_line,
                    z_median={
                        i: ctrl.median_impedance_ohm(i)
                        for i in range(cfg.NUM_CHANNELS)
                    },
                    live_ch=live_snap,
                    ctrl=ctrl,
                    tick_dt_s=v_dt,
                )

            elif faults or fault_latched:
                shift_str = (
                    f"{ref_shift:+.1f} mV"
                    if ref_shift is not None
                    else "— (no shift baseline)"
                )
                band_disp = ref_band if ref_shift is not None else "—"
                print(
                    time.strftime("%H:%M:%S"),
                    "FAULTS:",
                    "; ".join(faults),
                    f"latched={fault_latched}",
                    f"| ref raw={ref_raw_mv:.1f} mV shift={shift_str} band={band_disp}",
                )

            time.sleep(cfg.SAMPLE_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        log.close()
        leds.shutdown()
        ctrl.cleanup()
        if use_hw_gpio:
            try:
                import RPi.GPIO as GPIO  # noqa: N814

                GPIO.cleanup()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
