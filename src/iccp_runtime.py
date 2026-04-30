"""
CoilShield — ICCP runtime coordinator (single control loop).

`main.main()` delegates here after CLI/env setup so tick order, logging, and
optional diagnostics live in one module.
"""

from __future__ import annotations

import atexit
import os
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
_last_native_recapture_attempt: float = 0.0
_last_drift_alert_ts: float = 0.0
_floor_warn_since_mono: float | None = None
_last_floor_runtime_alert_mono: float = 0.0


def run_iccp_forever(args: Namespace) -> int:
    import commissioning
    import sensors
    import temp as temp_mod
    from control import Controller
    from leds import StatusLEDs
    from logger import DataLogger
    from reference import (
        ReferenceElectrode,
        ref_ads_sense_label,
        ref_hw_message,
        ref_hw_ok,
        ref_raw_legend,
        ref_ux_hint,
    )
    import polarization_safety as pol_safe

    import config.settings as cfg
    from cli_events import emit, output_mode

    Path(cfg.LOG_DIR).mkdir(parents=True, exist_ok=True)

    from console_ui import (
        print_ref_compact,
        print_sim_schedule,
        print_status_table,
        print_verbose_tick_line,
        wall_clock_s,
    )

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
            import cloud_worker

            cloud_worker.stop_and_join(timeout_s=5.0)
        except Exception:
            pass
        try:
            ctrl.all_outputs_off()
        except Exception:
            pass
        try:
            log.flush()
        except Exception:
            pass
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _signal_pwm_off)
    signal.signal(signal.SIGINT, _signal_pwm_off)

    def _atexit_failsafe() -> None:
        try:
            import cloud_worker

            cloud_worker.stop_and_join(timeout_s=5.0)
        except Exception:
            pass
        try:
            ctrl.all_outputs_off()
        except Exception:
            pass
        try:
            log.flush()
        except Exception:
            pass

    atexit.register(_atexit_failsafe)

    leds.setup()

    _ac = getattr(cfg, "ACTIVE_CHANNEL_INDICES", None)
    _ac_s = "all" if _ac is None else ",".join(str(i) for i in sorted(_ac))
    _tp = cfg.resolved_telemetry_paths()
    if output_mode() == "jsonl":
        emit(
            {
                "level": "info",
                "cmd": "start",
                "source": "iccp_runtime",
                "event": "start.env",
                "msg": "controller starting",
                "data": {
                    "sim": bool(sim),
                    "target_ma": float(cfg.TARGET_MA),
                    "active_channels": _ac_s,
                    "clear_fault_file": str(cfg.CLEAR_FAULT_FILE),
                    "telemetry_paths": _tp,
                    "ref_hw_message": ref_hw_message(),
                },
            }
        )
    else:
        print(
            f"CoilShield starting (sim={sim}, TARGET_MA={cfg.TARGET_MA}, "
            f"anodes={_ac_s}, clear fault: touch {cfg.CLEAR_FAULT_FILE})"
        )
        print(
            f"[main] Telemetry: latest.json ← {_tp['latest_json']}  "
            f"SQLite ← {_tp['sqlite_db']}  (LOG_DIR via {_tp['log_dir_source']})"
        )
        print(f"[main] Reference path: {ref_hw_message()}")

    if not sim and not sensors.ina219_sensors_ready():
        if output_mode() == "jsonl":
            emit(
                {
                    "level": "warn",
                    "cmd": "start",
                    "source": "iccp_runtime",
                    "event": "start.hw.warn",
                    "msg": "anode INA219 hardware not initialized",
                }
            )
        else:
            print(
                "[main] WARNING: anode INA219 hardware not initialized — no shunt/bus current for "
                "the control loop until I²C is fixed; see docs/ina219-i2c-bringup.md"
            )

    if sim:
        print_sim_schedule(sensors)

    if not args.skip_commission:
        if commissioning.needs_commissioning():
            if output_mode() == "jsonl":
                emit(
                    {
                        "level": "info",
                        "cmd": "start",
                        "source": "iccp_runtime",
                        "event": "start.commissioning.begin",
                        "msg": "starting commissioning sequence",
                    }
                )
            else:
                print("[main] No commissioning data. Starting commissioning sequence...")
                print("[main] (use --skip-commission to bypass for bench testing)")
            commissioned_target = commissioning.run(
                ref, ctrl, sim_state=sim_state, verbose=True
            )
            cfg.TARGET_MA = commissioned_target
            if output_mode() == "jsonl":
                emit(
                    {
                        "level": "info",
                        "cmd": "start",
                        "source": "iccp_runtime",
                        "event": "start.commissioning.end",
                        "msg": "commissioning finished",
                        "data": {"target_ma": float(cfg.TARGET_MA)},
                    }
                )
        else:
            ref.load_native()
            cfg.TARGET_MA = commissioning.load_commissioned_target()
            bl = ref.baseline_mv_for_shift()
            gline = ""
            if ref.galvanic_offset_mv is not None:
                gline = (
                    f"  galv_offset={ref.galvanic_offset_mv:.1f} mV"
                    f"{'  SERVICE' if ref.galvanic_offset_service_recommended else ''}"
                )
            bls = f"{bl:.1f}" if bl is not None else "—"
            if ref.native_oc_anodes_in_mv is not None:
                nat_line = f"true_native(1a)={ref.native_mv:.1f} mV"
            else:
                nat_line = f"native_mv={ref.native_mv:.1f} mV (single baseline)"
            if output_mode() == "jsonl":
                emit(
                    {
                        "level": "info",
                        "cmd": "start",
                        "source": "iccp_runtime",
                        "event": "start.commissioning.loaded",
                        "msg": "commissioning loaded",
                        "data": {
                            "native_line": nat_line,
                            "shift_baseline_mv": bl,
                            "galvanic_offset_mv": ref.galvanic_offset_mv,
                            "galvanic_offset_service_recommended": bool(
                                ref.galvanic_offset_service_recommended
                            ),
                            "target_ma": float(cfg.TARGET_MA),
                        },
                    }
                )
            else:
                print(
                    f"[main] Commissioning loaded — {nat_line}  "
                    f"shift_baseline={bls} mV{gline}  target={cfg.TARGET_MA:.3f} mA"
                )
    else:
        ref.load_native()
        if not commissioning.needs_commissioning():
            cfg.TARGET_MA = commissioning.load_commissioned_target()
        if output_mode() == "jsonl":
            emit(
                {
                    "level": "info",
                    "cmd": "start",
                    "source": "iccp_runtime",
                    "event": "start.commissioning.skipped",
                    "msg": "commissioning skipped",
                    "data": {"native_mv_set": ref.native_mv is not None},
                }
            )
        else:
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
    global _last_native_recapture_attempt, _last_drift_alert_ts
    global _floor_warn_since_mono, _last_floor_runtime_alert_mono

    def _run_reference_startup_stabilize(s: float) -> None:
        """
        Hold 0% drive (no CP) while the reference electrode relaxes toward OCP so shift is not
        dominated by decay from a **previous** run. Same PWM-off path as thermal pause, plus
        :meth:`Controller.set_reference_startup_soak` for clarity in future diagnostics.
        """
        if s <= 0:
            return
        tick = max(0.05, float(getattr(cfg, "SAMPLE_INTERVAL_S", 1.0)))
        print(
            f"[main] Reference startup stabilize: {s:.0f}s (0% drive) — depolarize ref after prior run. "
            "Set ICCP_REFERENCE_STARTUP_STABILIZE_S=0 to skip.",
            flush=True,
        )
        t_end = time.monotonic() + s
        last_log = time.monotonic()
        ctrl.set_reference_startup_soak(True)
        try:
            while time.monotonic() < t_end:
                now = time.monotonic()
                if now - last_log >= 10.0:
                    last_log = now
                    rem = max(0.0, t_end - now)
                    print(
                        f"[main] Reference startup stabilize: {rem:.0f}s remaining",
                        flush=True,
                    )
                temp_f = temp_mod.read_fahrenheit()
                temp_in_band = temp_mod.in_operating_range(temp_f)
                ctrl.set_thermal_pause(not temp_in_band)
                if sim:
                    r = sensors.read_all_sim(sim_state)  # type: ignore[arg-type]
                else:
                    r = sensors.read_all_real()
                ctrl.update(r)
                d = ctrl.duties()
                st = ctrl.channel_statuses()
                ref.read_raw_and_shift(duties=d, statuses=st, temp_f=temp_f)
                rem = t_end - time.monotonic()
                if rem > 0:
                    time.sleep(min(tick, rem))
        finally:
            ctrl.set_reference_startup_soak(False)
        print("[main] Reference startup stabilize — done (entering main loop).", flush=True)

    t_boot = temp_mod.read_fahrenheit()
    band_boot = temp_mod.in_operating_range(t_boot)
    if t_boot is not None:
        print(
            f"[main] Temperature: {t_boot:.1f}°F  "
            f"({'in operating band' if band_boot else 'out of band — outputs held until in band'})  "
            f"[{temp_mod.TEMP_MIN_F:.0f}–{temp_mod.TEMP_MAX_F:.0f}°F]",
            flush=True,
        )
    else:
        miss = "thermal pause" if bool(
            getattr(cfg, "THERMAL_PAUSE_WHEN_SENSOR_MISSING", False)
        ) else "legacy: run without temp gate"
        print(
            f"[main] Temperature: (no DS18B20 reading)  ({miss})  "
            f"band [{temp_mod.TEMP_MIN_F:.0f}–{temp_mod.TEMP_MAX_F:.0f}°F]",
            flush=True,
        )

    _rss = max(0.0, float(getattr(cfg, "REFERENCE_STARTUP_STABILIZE_S", 0.0)))
    if sim and not (os.environ.get("ICCP_REFERENCE_STARTUP_STABILIZE_S", "") or "").strip():
        _rss = 0.0
    _run_reference_startup_stabilize(_rss)
    # After a 0% depolarize window (or a cold start), use DUTY_PROBE (default 0.01%) for CP session start.
    ctrl.seed_session_start_duty()

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
                ref_ads_sense=ref_ads_sense_label(),
                runtime_alerts=[
                    (
                        f"Startup: first telemetry; pan temp {t0:.1f}°F"
                        if t0 is not None
                        else "Startup: first telemetry (pan temp N/A)"
                    )
                ],
                channel_targets={
                    i: ctrl.channel_target_ma(i) for i in range(cfg.NUM_CHANNELS)
                },
                t_to_system_protected_s=ctrl.t_to_system_protected_s(),
                polarization_state="disabled",
                native_mv=ref.baseline_mv_for_shift(),
                native_true_anodes_out_mv=ref.native_mv,
                native_oc_anodes_in_mv=ref.native_oc_anodes_in_mv,
                galvanic_offset_mv=ref.galvanic_offset_mv,
                galvanic_offset_baseline_mv=ref.galvanic_offset_baseline_mv,
                galvanic_offset_service_recommended=ref.galvanic_offset_service_recommended,
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
        import cloud_worker

        cloud_worker.start_background_sync()
    except Exception:
        pass

    if args.verbose:
        _n = float(getattr(cfg, "OUTER_LOOP_POTENTIAL_MIN_S", 5.0) or 0.0)
        nudge_s = (
            f"outer TARGET_MA: live shift nudged each tick if ≥{_n:.0f}s since last; "
            f"instant-off nudge at LOG (every {int(cfg.LOG_INTERVAL_S)}s) bypasses. "
        )
        if _n <= 0.0:
            nudge_s = (
                "outer TARGET_MA: each sample from live ref shift (throttle 0) + LOG tick. "
            )
        print(
            f"[main] Verbose: one line every {float(cfg.SAMPLE_INTERVAL_S):g}s; "
            f"full channel table every {int(cfg.LOG_INTERVAL_S)}s (LOG_INTERVAL_S). "
            f"{nudge_s}"
            "dI=I_target−I_mA · Vc≈Bus×PWM%.",
            flush=True,
        )

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
                    if output_mode() == "jsonl":
                        emit(
                            {
                                "level": "warn",
                                "cmd": "start",
                                "source": "iccp_runtime",
                                "event": "thermal.pause",
                                "msg": "thermal pause — pan temp outside band",
                                "data": {
                                    "temp_f": temp_f,
                                    "min_f": float(temp_mod.TEMP_MIN_F),
                                    "max_f": float(temp_mod.TEMP_MAX_F),
                                    "reason": reason,
                                },
                            }
                        )
                    else:
                        print(
                            f"[main] THERMAL PAUSE {temp_f}°F outside "
                            f"[{temp_mod.TEMP_MIN_F}–{temp_mod.TEMP_MAX_F}°F]: {reason}"
                        )
                    _thermal_paused = True
            elif _thermal_paused:
                if output_mode() == "jsonl":
                    emit(
                        {
                            "level": "info",
                            "cmd": "start",
                            "source": "iccp_runtime",
                            "event": "thermal.resume",
                            "msg": "thermal resume — pan temp back in band",
                            "data": {"temp_f": temp_f},
                        }
                    )
                else:
                    print(f"[main] Temp restored ({temp_f}°F) — resuming.")
                _thermal_paused = False

            if sim:
                readings = sensors.read_all_sim(sim_state)  # type: ignore[arg-type]
            else:
                readings = sensors.read_all_real()

            duties_before = ctrl.duties()
            status_before = ctrl.channel_statuses()
            polarization_state = "disabled"
            abs_ok = pol_safe.absolute_potential_safety_enabled(cfg) and (
                (not sim) or bool(getattr(cfg, "CATHODE_ABSOLUTE_SAFETY_IN_SIM", False))
            )
            if abs_ok and ref_hw_ok():
                ref_pre = float(
                    ref.read(
                        duties=duties_before,
                        statuses=status_before,
                        temp_f=temp_f,
                    )
                )
                if pol_safe.trips_hard_polarization_cutoff(ref_pre, cfg):
                    lim = float(getattr(cfg, "POLARIZATION_HARD_CUTOFF_MV", -1080.0))
                    ev = pol_safe.cathode_mv_for_absolute_limits(ref_pre, cfg)
                    ctrl.latch_polarization_cutoff_all(
                        f"POLARIZATION CUTOFF: cathode mV ({ev:.1f}) past hard limit "
                        f"({lim:.0f} mV vs Ag/AgCl scale) — touch clear_fault to reset"
                    )
                    polarization_state = "hard_cutoff"
                else:
                    polarization_state = (
                        "in_window"
                        if pol_safe.instant_off_raw_in_protection_window(ref_pre, cfg)
                        else "outside_window"
                    )
            elif abs_ok and not ref_hw_ok():
                polarization_state = "no_ref_hw"

            faults, fault_latched = ctrl.update(readings)
            duties = ctrl.duties()
            ch_status = ctrl.channel_statuses()
            any_wet = ctrl.any_wet()

            if sim and sim_state is not None:
                sim_state.duties = dict(duties)

            ref_raw_mv, ref_shift = ref.read_raw_and_shift(
                duties=duties, statuses=ch_status, temp_f=temp_f
            )
            if (
                abs_ok
                and ref_hw_ok()
                and polarization_state not in ("disabled", "hard_cutoff", "no_ref_hw")
            ):
                polarization_state = (
                    "in_window"
                    if pol_safe.instant_off_raw_in_protection_window(ref_raw_mv, cfg)
                    else "outside_window"
                )
            ref_valid, ref_valid_reason = ref.ref_valid()
            _eff_shift_t = ref.effective_shift_target_mv()
            _eff_shift_m = ref.effective_max_shift_mv()
            ctrl.advance_shift_fsm(
                readings,
                shift_mv=ref_shift,
                ref_valid=ref_valid,
                ref_valid_reason=ref_valid_reason,
                shift_target_mv=_eff_shift_t,
                shift_max_mv=_eff_shift_m,
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
                            ctrl.update_potential_target(
                                io_shift,
                                shift_target_mv=_eff_shift_t,
                                shift_max_mv=_eff_shift_m,
                                force=True,
                            )
                        except Exception as exc:
                            print(
                                f"[main] outer-loop instant-off failed: {exc}",
                                file=sys.stderr,
                            )
                            traceback.print_exc()
                            ctrl.update_potential_target(
                                ref_shift,
                                shift_target_mv=_eff_shift_t,
                                shift_max_mv=_eff_shift_m,
                                force=True,
                            )
                    else:
                        ctrl.update_potential_target(
                            ref_shift,
                            shift_target_mv=_eff_shift_t,
                            shift_max_mv=_eff_shift_m,
                            force=True,
                        )
                    outer_loop_counter = 0
                    ref_log_tick = True
                elif ref_shift is not None:
                    # Live shift: nudge TARGET_MA toward the mV band (rate-limited; LOG uses force).
                    ctrl.update_potential_target(
                        ref_shift,
                        shift_target_mv=_eff_shift_t,
                        shift_max_mv=_eff_shift_m,
                    )

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
            wet_path = False
            for _ch in range(cfg.NUM_CHANNELS):
                _r = readings.get(_ch, {})
                if _r.get("ok") and float(_r.get("current", 0.0) or 0.0) >= float(
                    cfg.CHANNEL_DRY_MA
                ):
                    wet_path = True
                    break
            if abs_ok and temp_in_band and ref_hw_ok() and wet_path:
                if pol_safe.below_unprotected_floor_warning(ref_raw_mv, cfg):
                    if _floor_warn_since_mono is None:
                        _floor_warn_since_mono = tick_mono
                    elif (
                        tick_mono - _floor_warn_since_mono
                        >= float(
                            getattr(cfg, "POLARIZATION_FLOOR_WARNING_DURATION_S", 300.0)
                        )
                    ):
                        if tick_mono - _last_floor_runtime_alert_mono >= 300.0:
                            fl = float(
                                getattr(cfg, "POLARIZATION_FLOOR_WARNING_MV", -900.0)
                            )
                            ev = pol_safe.cathode_mv_for_absolute_limits(ref_raw_mv, cfg)
                            runtime_alerts.append(
                                f"Cathode potential {ev:.0f} mV less negative than "
                                f"floor {fl:.0f} mV for "
                                f">{float(getattr(cfg, 'POLARIZATION_FLOOR_WARNING_DURATION_S', 300)):.0f}s "
                                "while wet — increase current / verify reference (manual review)."
                            )
                            polarization_state = "floor_warn_sustained"
                            _last_floor_runtime_alert_mono = tick_mono
                else:
                    _floor_warn_since_mono = None
            else:
                _floor_warn_since_mono = None

            if not temp_in_band:
                tf_s = f"{temp_f}°F" if temp_f is not None else "N/A"
                runtime_alerts.append(
                    f"Thermal pause: {tf_s} outside operating band "
                    f"[{temp_mod.TEMP_MIN_F}–{temp_mod.TEMP_MAX_F}°F] (outputs held off)"
                )

            # --- Scheduled native re-capture (docs/iccp-requirements.md §2.3) ---
            # Only run when the baseline is actually past its due time, temperature is in
            # band, and we are not already in a fault storm. Cool-down between attempts
            # prevents tight retry loops on persistent reference issues.
            recap_cooldown_s = 10.0 * 60.0
            if (
                temp_in_band
                and not fault_latched
                and ref.native_mv is not None
                and commissioning.native_recapture_due()
                and (time.time() - _last_native_recapture_attempt) > recap_cooldown_s
            ):
                _last_native_recapture_attempt = time.time()
                print("[main] Native re-capture due — pausing CP for Phase 1 native-only.")
                try:
                    new_mv, reason = commissioning.run_native_only(
                        ref, ctrl, sim_state=sim_state, verbose=True
                    )
                except Exception as exc:  # pragma: no cover
                    new_mv, reason = None, f"exception:{exc!r}"
                    traceback.print_exc()
                if new_mv is None:
                    runtime_alerts.append(
                        f"Native re-capture failed: {reason} (keeping previous native_mv)"
                    )
                else:
                    runtime_alerts.append(
                        f"Native re-captured: {new_mv:.2f} mV ({reason})"
                    )

            # --- Drift warning while sustained Protected (docs/iccp-requirements.md §2.3) ---
            drift_trigger = float(getattr(cfg, "NATIVE_DRIFT_TRIGGER_MV", 40.0))
            _bl_drift = ref.baseline_mv_for_shift()
            if (
                _bl_drift is not None
                and ctrl.all_protected()
                and abs(ref_raw_mv - _bl_drift) > drift_trigger
                and (time.time() - _last_drift_alert_ts) > 300.0
            ):
                _last_drift_alert_ts = time.time()
                runtime_alerts.append(
                    f"Reference drift: |ref {ref_raw_mv:.1f} − shift_baseline {_bl_drift:.1f}| "
                    f"> {drift_trigger:.0f} mV while all_protected"
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
                    ref_ads_sense=ref_ads_sense_label(),
                    ref_depol_rate_mv_s=ref_depol_rate_mv_s,
                    diag_extra=diag_extra,
                    runtime_alerts=runtime_alerts or None,
                    channel_targets={
                        i: ctrl.channel_target_ma(i)
                        for i in range(cfg.NUM_CHANNELS)
                    },
                    state_v2=ctrl.channel_state_v2(),
                    channel_fault_reasons=ctrl.channel_fault_reasons(),
                    channel_t_in_state_s={
                        i: ctrl.t_in_state_v2_s(i)
                        for i in range(cfg.NUM_CHANNELS)
                    },
                    channel_t_in_polarizing_s={
                        i: ctrl.t_in_polarizing_s(i)
                        for i in range(cfg.NUM_CHANNELS)
                    },
                    all_protected=ctrl.all_protected(),
                    any_active=ctrl.any_active(),
                    any_overprotected=ctrl.any_overprotected(),
                    native_mv=ref.baseline_mv_for_shift(),
                    native_true_anodes_out_mv=ref.native_mv,
                    native_oc_anodes_in_mv=ref.native_oc_anodes_in_mv,
                    galvanic_offset_mv=ref.galvanic_offset_mv,
                    galvanic_offset_baseline_mv=ref.galvanic_offset_baseline_mv,
                    galvanic_offset_service_recommended=ref.galvanic_offset_service_recommended,
                    native_age_s=ref.native_age_s(),
                    next_native_recapture_s=ref.next_native_recapture_s(),
                    ref_valid=ref_valid,
                    ref_valid_reason=ref_valid_reason,
                    t_to_system_protected_s=ctrl.t_to_system_protected_s(),
                    polarization_state=polarization_state,
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
                if ref_log_tick:
                    _ch_rows = cfg.active_channel_indices_list()
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
                        include_pwm_path_caption=False,
                        channels=_ch_rows,
                    )
                elif temp_in_band:
                    print_verbose_tick_line(
                        readings,
                        faults,
                        fault_latched,
                        ch_status,
                        any_wet,
                        ref_raw_mv,
                        ref_shift,
                        ref_band,
                        temp_f,
                        duties,
                        sim_line=sim_line,
                        channels=cfg.active_channel_indices_list(),
                    )

            elif faults or fault_latched:
                shift_str = (
                    f"{ref_shift:+.1f} mV"
                    if ref_shift is not None
                    else "— (no shift baseline)"
                )
                band_disp = ref_band if ref_shift is not None else "—"
                rleg = ref_raw_legend()
                print(
                    f"{wall_clock_s()}  FAULTS: "
                    f"{'; '.join(faults)}  latched={fault_latched}  "
                    f"| {rleg}={ref_raw_mv:.1f} mV shift={shift_str} band={band_disp}"
                )

            time.sleep(cfg.SAMPLE_INTERVAL_S)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try:
            import cloud_worker

            cloud_worker.stop_and_join(timeout_s=5.0)
        except Exception:
            pass
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
