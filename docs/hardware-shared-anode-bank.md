# Shared anode return and unified PWM (`SHARED_RETURN_PWM`)

## Physics

- Multiple anodes can share the **same electrolyte** and a **common return** (cathode path). The channels are not independent: current paths and overpotential interact.
- A **low-side MOSFET** on the return controls average drive via **PWM** (effective current ∝ duty). You are not regulating supply voltage; you are shaping **time-averaged** conduction. Bus voltage and cell impedance still cause channel-by-channel shunt current differences; **sensing** remains per-INA219.
- **INA219** is measurement-only (shunt + bus/branch context). It is not a protection controller; software limits and faults still apply.

## Software: per-anode vs bank mode (default: `SHARED_RETURN_PWM = False`)

The **stock** setting in [`config/settings.py`](../config/settings.py) is **`SHARED_RETURN_PWM = False`**: each MOSFET gate can have a **different** software duty; path FSM (OPEN / REGULATE / PROTECTING) runs per channel. A **single** channel with non-zero duty and others at 0% is **often** normal (only one condensate path is wet/strong enough to leave OPEN for that anode) — it is not by itself a sign that the firmware is driving “only one anode.”

### Enabling bank mode (shared return)

Set **`SHARED_RETURN_PWM = True`**. Then:

- All `PWM_GPIO_PINS` receive the **same** duty every tick. Regulation compares **sum of shunt-reported branch currents** to the **sum of per-channel `CHANNEL_TARGET_MA` / `TARGET_MA` targets** (as many channels as `NUM_CHANNELS`).
- Ramps use only the global `PWM_STEP_*` keys; `CHANNEL_PWM_STEP_*` dict overrides are ignored in bank mode.
- The path FSM and `state_v2` still run **per channel** for telemetry; **drive** is unified. If any channel is in FAULT, duty is held at 0% for the whole bank.
- If **only one** shunt shows mA while duty is non-zero on **all** gates, that is **per-path conduction** (one wet cell), not “only one GPIO on” in software.

## RPi.GPIO quirk: phase vs duty

- Setting the same **numeric duty** on each GPIO’s software PWM does **not** **phase-align** the carriers. Each `PWM` instance is independent. If you need one aligned waveform to all gate networks, use **one GPIO** fanout or external buffering.

## Verifying you did not “lose” anodes (runtime checklist)

On the **Raspberry Pi** with the controller **running**:

1. **Config alignment** — In `config/settings.py`, **`len(INA219_ADDRESSES) == NUM_CHANNELS == len(PWM_GPIO_PINS)`** (e.g. four of each by default, or **three** of each on a 3-INA repair layout; see [ina219-i2c-bringup.md](ina219-i2c-bringup.md)). The controller **fails fast** on startup with `ValueError` if these counts disagree.
2. **Telemetry** — Open **`latest.json`**. For each channel key `"0"` … `"N-1"`, compare **`duty`** and **`ma`**. With **`SHARED_RETURN_PWM` False**, duties **may** differ. With **`SHARED_RETURN_PWM` True**, logged duties should be the **same**; shunt mA can still differ per channel.
3. **I²C / probe** — Run **`iccp probe`** (or **`hw_probe`**) and confirm every expected INA219 **7-bit** address is present. A missing board yields **`"no hardware"`** on that index, not a specific anode only.
