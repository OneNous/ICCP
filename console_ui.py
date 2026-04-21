"""Console output helpers shared by main and iccp_runtime (no argparse / entry logic)."""

from __future__ import annotations

import time


def print_sim_schedule(sensor_module: object) -> None:
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


def print_status_table(
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
    path_tags: dict[int, str] | None = None,
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
        w = 152
        if ctrl is not None and hasattr(ctrl, "channel_target_ma"):
            parts = [
                f"CH{i + 1}={ctrl.channel_target_ma(i):.3f}"
                for i in range(int(_cfg.NUM_CHANNELS))
            ]
            print(
                "  I_target (mA) — PROTECTING servos to this; REGULATE ramps toward it; "
                "OPEN holds 0% PWM: " + "  ".join(parts)
            )
        print("─" * w)
        print(
            f"{'CH':<4} {'State':<12} {'Path':<6} {'dI':>7}  {'BusV':<8} {'mA':>8}  "
            f"{'PWM%':<8} {'Ω imp':<10} {'Ω med':<10} {'Vc':<8} {'Prot':<5} "
            f"{'P(W)':<9} {'E(J)':<10} {'η':<10}"
        )
        print("─" * w)
        for i in sorted(readings.keys()):
            r = readings[i]
            st = ch_status.get(i, "?")
            ptag = (path_tags or {}).get(i, "—")
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
                if ctrl is not None and hasattr(ctrl, "channel_target_ma"):
                    di = float(ctrl.channel_target_ma(i)) - ma
                    di_s = f"{di:+7.2f}"
                else:
                    di_s = "    —  "
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
                    f"{i + 1:<4} {st:<12} {ptag:<6} {di_s}  {bus_v:<8.3f} {ma:>8.2f}  "
                    f"{duty:<8.1f} {imp_s:<10} {zmed_s:<10} {vc:<8.3f} "
                    f"{int(st == 'PROTECTING'):<5} {p_s:<9} {e_s:<10} {n_s:<10}"
                )
            else:
                print(
                    f"{i + 1:<4} {st:<12} {'—':<6} {'    —':>7}  {'--':<8} {'--':>8}  "
                    f"{'--':<8} {'—':<10} {'—':<10} {'—':<8} {'—':<5} "
                    f"{'—':<9} {'—':<10} {'—':<10}"
                )
        print("─" * w)
        tpw = live_ch.get("total_power_w") if isinstance(live_ch, dict) else None
        tpw_s = f"{float(tpw):.4f}" if isinstance(tpw, (int, float)) else "—"
        pwm_mx = float(getattr(_cfg, "PWM_MAX_DUTY", 80.0))
        probe = float(getattr(_cfg, "DUTY_PROBE", 3.0))
        vhard = float(getattr(_cfg, "VCELL_HARD_MAX_V", 0.0) or 0.0)
        print(
            f"  PWM: REGULATE ramps from {probe:.0f}% up; max duty "
            f"min({pwm_mx:.0f}%, 100×{vhard:.1f}V/Bus); Vc≈Bus×PWM/100"
        )
        print(
            "  Path=conduction (weak|strong|open); dI=I_target−I_mA "
            "(PROTECTING needs strong path + near-target hold). "
            "Prot=1 only in PROTECTING."
        )
        print(
            f"  AnyWet={int(any_wet)}  Latch={int(latched)}  "
            f"ΣP={tpw_s} W  "
            f"Faults: {'; '.join(faults) or '—'}"
        )
    except BrokenPipeError:
        raise SystemExit(0) from None


def print_ref_compact(
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
