"""
CoilShield — ICCP runtime coordinator (single control loop).

`main.main()` delegates here after CLI/env setup so tick order, logging, and
optional diagnostics live in one module.
"""

from __future__ import annotations

import signal
import sys
import time
import traceback
from argparse import Namespace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_last_runtime_diag_ts: float = 0.0
_last_deep_snapshot_ts: float = 0.0


def run_iccp_forever(args: Namespace) -> int:
    import commissioning
    import sensors
    import temp as temp_mod
    from control import Controller
    from leds import StatusLEDs
    from logger import DataLogger
    from reference import ReferenceElectrode, ref_hw_message, ref_hw_ok, ref_ux_hint

    import config.settings as cfg

    Path(cfg.LOG_DIR).mkdir(parents=True, exist_ok=True)

    from console_ui import print_ref_compact, print_sim_schedule, print_status_table

    sim = sensors.SIM_MODE
    use_hw_gpio = not sim

    sim_state = sensors.SimSensorState() if sim else None
    ctrl = Controller()
    ref = ReferenceElectrode()
    leds = StatusLEDs(use_hw_gpio)
    log = DataLogger()

    def _signal_pwm_off(signum: int, _frame) -> None:
        """Best-effort: drive anodes off before exit (SIGKILL cannot be caught)."""
        try:
            ctrl.all_outputs_off()
        except Exception:
            pass
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _signal_pwm_off)
    signal.signal(signal.SIGINT, _signal_pwm_off)

    leds.setup()

    print(
        f"CoilShield starting (sim={sim}, TARGET_MA={cfg.TARGET_MA}, "
        f"clear fault: touch {cfg.CLEAR_FAULT_FILE})"
    )
    _tp = cfg.resolved_telemetry_paths()
    print(
        f"[main] Telemetry: latest.json ← {_tp['latest_json']}  "
        f"SQLite ← {_tp['sqlite_db']}  (LOG_DIR via {_tp['log_dir_source']})"
    )
    print(f"[main] Reference path: {ref_hw_message()}")

    if sim:
        print_sim_schedule(sensors)

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
    outer_loop_interval = max(1, int(cfg.LOG_INTERVAL_S / cfg.SAMPLE_INTERVAL_S))
    _ux_tip_shown = False
    _thermal_paused = False
    _prev_verbose_mono: float | None = None

    global _last_runtime_diag_ts, _last_deep_snapshot_ts

    def _bootstrap_latest() -> None:
        """One telemetry write so dashboards are not stuck on a pre-reboot latest.json."""
        try:
            t0 = temp_mod.read_fahrenheit()
            band0 = temp_mod.in_operating_range(t0)
            ctrl.set_thermal_pause(not band0)
            r0 = (
                sensors.read_all_sim(sim_state)
                if sim
                else sensors.read_all_real()
            )
            f0, lat0 = ctrl.update(r0)
            d0 = ctrl.duties()
            st0 = ctrl.channel_statuses()
            w0 = ctrl.any_wet()
            raw0, sh0 = ref.read_raw_and_shift(
                duties=d0, statuses=st0, temp_f=t0
            )
            bd0 = (
                ref.protection_status(sh0) if sh0 is not None else "—"
            )
            sim_kw0: str | None = None
            if sim and sim_state is not None:
                sim_kw0 = sim_state.sim_hhmm()
            hint0 = ref_ux_hint(
                baseline_set=ref.native_mv is not None,
                hw_ok=ref_hw_ok(),
                skip_commission=args.skip_commission,
            )
            log.feed_cooling_cycle(
                in_band=band0,
                ts_unix=time.time(),
                dt_s=cfg.SAMPLE_INTERVAL_S,
                ch_status=st0,
                temp_f=t0,
            )
            log.record(
                r0,
                w0,
                f0,
                d0,
                lat0,
                st0,
                sim_time=sim_kw0,
                ref_shift_mv=sh0,
                ref_status=bd0 if sh0 is not None else "N/A",
                temp_f=t0,
                ref_raw_mv=raw0,
                ref_hw_ok=ref_hw_ok(),
                ref_hint=hint0 or None,
                ref_hw_message=ref_hw_message(),
                ref_baseline_set=ref.native_mv is not None,
                runtime_alerts=["Startup: first telemetry snapshot after init"],
                channel_targets={
                    i: ctrl.channel_target_ma(i) for i in range(cfg.NUM_CHANNELS)
                },
            )
            log.maybe_flush()
        except Exception as e:
            print(f"[main] startup latest.json snapshot failed: {e}", file=sys.stderr)
            traceback.print_exc()
            try:
                log.recovery_touch_latest("Startup telemetry failed", e)
            except OSError:
                pass

    _bootstrap_latest()

    try:
        while True:
            tick_mono = time.monotonic()
            v_dt = (
                None
                if _prev_verbose_mono is None
                else (tick_mono - _prev_verbose_mono)
            )
            _prev_verbose_mono = tick_mono

            temp_f = temp_mod.read_fahrenheit()
            temp_in_band = temp_mod.in_operating_range(temp_f)
            ctrl.set_thermal_pause(not temp_in_band)

            if not temp_in_band:
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
            elif _thermal_paused:
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
                duties=duties, statuses=ch_status, temp_f=temp_f
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

            ref_depol_rate_mv_s: float | None = None
            ref_log_tick = False
            if temp_in_band:
                outer_loop_counter += 1
                if outer_loop_counter >= outer_loop_interval:
                    if (
                        bool(getattr(cfg, "OUTER_LOOP_INSTANT_OFF", True))
                        and ref.native_mv is not None
                    ):
                        try:
                            io_raw, io_shift, ref_depol_rate_mv_s = (
                                commissioning.instant_off_ref_measurement(
                                    ctrl,
                                    ref,
                                    sim_state=sim_state,
                                    log=None,
                                    temp_f=temp_f,
                                )
                            )
                            ref_raw_mv = io_raw
                            ref_shift = io_shift
                            ctrl.update_potential_target(io_shift)
                        except Exception as exc:
                            print(
                                f"[main] outer-loop instant-off failed: {exc}",
                                file=sys.stderr,
                            )
                            traceback.print_exc()
                            ctrl.update_potential_target(ref_shift)
                    else:
                        ctrl.update_potential_target(ref_shift)
                    outer_loop_counter = 0
                    ref_log_tick = True

            ref_band = (
                ref.protection_status(ref_shift)
                if ref_shift is not None
                else "—"
            )

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
                in_band=temp_in_band,
                ts_unix=time.time(),
                dt_s=cfg.SAMPLE_INTERVAL_S,
                ch_status=ch_status,
                temp_f=temp_f,
            )

            diag_extra = None
            now = time.time()
            if bool(getattr(cfg, "LATEST_JSON_INCLUDE_DIAG", False)):
                throttle = float(getattr(cfg, "DIAG_THROTTLE_S", 60.0))
                if now - _last_runtime_diag_ts >= throttle:
                    import diagnostics

                    diag_extra = diagnostics.build_runtime_diag()
                    _last_runtime_diag_ts = now

            runtime_alerts: list[str] = []
            if not temp_in_band:
                tf_s = f"{temp_f}°F" if temp_f is not None else "N/A"
                runtime_alerts.append(
                    f"Thermal pause: {tf_s} outside operating band "
                    f"[{temp_mod.TEMP_MIN_F}–{temp_mod.TEMP_MAX_F}°F] (outputs held off)"
                )

            try:
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
                    ref_depol_rate_mv_s=ref_depol_rate_mv_s,
                    diag_extra=diag_extra,
                    runtime_alerts=runtime_alerts or None,
                    channel_targets={
                        i: ctrl.channel_target_ma(i)
                        for i in range(cfg.NUM_CHANNELS)
                    },
                )
            except Exception as e:
                print(f"[main] log.record failed: {e}", file=sys.stderr)
                traceback.print_exc()
                try:
                    log.recovery_touch_latest(
                        "Telemetry write failed — merged alert into latest.json",
                        e,
                    )
                except OSError:
                    pass
                live_snap = None

            req_path = Path(cfg.LOG_DIR) / str(
                getattr(cfg, "DIAGNOSTIC_REQUEST_FILE", "request_diag")
            )
            out_path = Path(cfg.LOG_DIR) / str(
                getattr(cfg, "DIAGNOSTIC_SNAPSHOT_JSON", "diagnostic_snapshot.json")
            )
            min_iv = float(getattr(cfg, "DIAGNOSTIC_MIN_INTERVAL_S", 60.0))
            if req_path.exists() and (now - _last_deep_snapshot_ts >= min_iv):
                try:
                    from diagnostics import write_diagnostic_snapshot_atomic

                    write_diagnostic_snapshot_atomic(out_path)
                    print(f"[main] diagnostic snapshot → {out_path}")
                except Exception as e:
                    print(f"[main] diagnostic snapshot failed: {e}")
                finally:
                    try:
                        req_path.unlink()
                    except OSError:
                        pass
                _last_deep_snapshot_ts = now

            log.maybe_flush()

            if ref_log_tick and not args.verbose and live_snap is not None:
                print_ref_compact(
                    ref_hw_line,
                    ref_raw_mv,
                    ref_shift,
                    ref_band,
                    ref_hint,
                )

            leds.set_running_ok(not fault_latched and len(faults) == 0)

            if args.verbose and live_snap is not None:
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
                print_status_table(
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
                    path_tags=ctrl.channel_path_tags(),
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
