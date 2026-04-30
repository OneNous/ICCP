# Tier 2 “headquarters” — where it lives in code

The product narrative describes a **fast inner loop** (per-channel current / path FSM) and a **slower outer loop** that uses the reference, commissioning-style instant-off, and system health. There is no separate `supervisor.py` module; the **outer loop** is implemented as follows.

## Fast loop (Tier 1)

- **Cadence:** `SAMPLE_INTERVAL_S` (default 0.5 s) in [`config/settings.py`](../config/settings.py).
- **Code:** [`control.py`](../src/control.py) `Controller.update()` (path FSM, PWM) and `advance_shift_fsm()` (shift-based `state_v2`: Off, Probing, Polarizing, Protected, Overprotected, Fault).
- **Sensors:** [`sensors.py`](../src/sensors.py) `read_all_real` / sim.

## Slow loop (Tier 2)

- **Cadence:** `LOG_INTERVAL_S` (default 120 s), expressed in [`iccp_runtime.py`](../src/iccp_runtime.py) as `outer_loop_interval = LOG_INTERVAL_S / SAMPLE_INTERVAL_S` control ticks.
- **Reference + instant-off (when enabled):** `commissioning.instant_off_ref_measurement(...)` for depolarization rate and shift at the longer interval.
- **Target trim:** `Controller.update_potential_target(shift_mv)` — nudges `TARGET_MA` from measured shift (see `tests/test_update_potential_target.py`).
- **Drift / native re-capture / thermal:** same runtime file: scheduled `run_native_only`, drift alerts vs `NATIVE_DRIFT_TRIGGER_MV`, `set_thermal_pause` from temperature band.

So **Tier 2 =** [`iccp_runtime.py`](../src/iccp_runtime.py) outer-loop block + `ReferenceElectrode` + `Controller` methods, **not** a separate process. Requirements language for `all_protected` and `T_SYSTEM_STABLE` is in [`docs/iccp-requirements.md`](iccp-requirements.md) §2.2.

## Related

- Commissioning (Phases 1–3, native + ramp): [`commissioning.py`](../src/commissioning.py)
- Logging / `latest.json`: [`logger.py`](../src/logger.py)
