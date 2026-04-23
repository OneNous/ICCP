# Shared anode return and unified PWM (`SHARED_RETURN_PWM`)

## Physics

- Multiple anodes can share the **same electrolyte** and a **common return** (cathode path). The channels are not independent: current paths and overpotential interact.
- A **low-side MOSFET** on the return controls average drive via **PWM** (effective current ∝ duty). You are not regulating supply voltage; you are shaping **time-averaged** conduction. Bus voltage and cell impedance still cause channel-by-channel shunt current differences; **sensing** remains per-INA219.
- **INA219** is measurement-only (shunt + bus/branch context). It is not a protection controller; software limits and faults still apply.
- A **high-side 5V relay** (optional, see `ANODE_RELAY_GPIO_PINS` in [`config/settings.py`](../config/settings.py)) disconnects the anode feed for a **true** “anode not powered” off state, complementing the MOSFET on the return. Relays are de-energized on `Controller.all_outputs_off` when pins are configured.

## Software: bank mode (default: `SHARED_RETURN_PWM = True`)

- All `PWM_GPIO_PINS` receive the **same** duty every tick when bank mode is on. Regulation compares **sum of shunt-reported branch currents** to the **sum of per-channel `CHANNEL_TARGET_MA` / `TARGET_MA` targets** (as many channels as `NUM_CHANNELS`).
- Ramps use only the global `PWM_STEP_*` keys; `CHANNEL_PWM_STEP_*` dict overrides are ignored in bank mode.
- The legacy path FSM (OPEN / REGULATE / PROTECTING) and `state_v2` shift logic still run **per channel** for telemetry; **drive** is unified. If any channel is in FAULT, duty is held at 0% for the whole bank.

## RPi.GPIO quirk: phase vs duty

- Setting the same **numeric duty** on each GPIO’s software PWM does **not** **phase-align** the carriers. Each `PWM` instance is independent. If you need one aligned waveform to all gate networks, use **one GPIO** fanout or external buffering.

## Reverting to per-anode software PWM (bench / legacy)

- Set `SHARED_RETURN_PWM = False` in `config/settings.py` (or override in a test; the default test suite sets this to `False` in `tests/conftest.py` to preserve per-channel test expectations).
