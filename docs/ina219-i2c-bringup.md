# INA219 / TCA9548A I²C bring-up (anode shunt path)

This checklist matches the default mux layout in [`config/settings.py`](../config/settings.py): **TCA9548A @ 0x70**, **ports 0–3** to each anode INA219 (`INA219_ADDRESSES` **0x40, 0x41, 0x44, 0x45**), **port 4** to the reference **ADS1115** @ 0x48. If the reference path works but every anode shows `READ ERROR: no hardware`, the bus and mux may be fine; **ch4 vs ch0–3** often isolates a wiring, power, or address issue on the INA side.

**Related:** root cause in [`sensors.py`](../sensors.py) (empty `_sensors` after import-time init failure) — see also [`docs/ina219-datasheet-notes.md`](ina219-datasheet-notes.md).

## 1) Align `config/settings.py` with the board

- `I2C_BUS` = Pi I²C (often `1` on 40‑pin header).
- `I2C_MUX_ADDRESS` = **0x70** (or `None` on a no-mux rig).
- `I2C_MUX_CHANNELS_INA219` = **(0, 1, 2, 3)** (one port per anode, or adjust to straps).
- `I2C_MUX_CHANNEL_ADS1115` = **4** (or your ADS branch).
- `INA219_ADDRESSES` and **A0/A1** straps on each INA board must match (default **0x40, 0x41, 0x44, 0x45**).
- If you see sporadic `Errno 5` (EIO) on mux/INA, try `I2C_MUX_POST_SELECT_DELAY_S` in the **0.001–0.002** s range.
- **Do not** mix a no-mux `settings` profile with a muxed PCB (or the opposite).

## 2) Mux first (must succeed before relying on INA inits)

1. **Power / GND:** common ground between Pi, TCA9548A, INA219, and ADS1115; **3.3 V** logic on SDA/SCL to the Pi (not 5 V I²C).
2. **Select a port** — until this works with **exit 0**, downstream devices are undefined:
   - `sudo i2ctransfer -y 1 w1@0x70 0x01` (port 0)
   - or: `sudo i2cset -y 1 0x70 0x01 c` (one byte; the `c` form writes a full byte; avoid sending only one nibble in interactive mistakes).
3. If writes to **0x70** fail, fix levels, pull-ups, and wiring before debugging INA addresses.
4. On failure, check: `dmesg -T | tail -40` for `i2c-1` (or your bus) errors.

## 3) Per-port INA (ports 0–3)

- After selecting **one** port, `i2cdetect -y 1` should show the INA at the **expected 7-bit address** for that branch.
- Re-seat modules; confirm **A0/A1** match the intended `INA219_ADDRESSES` list.
- **Port 4 + 0x48** can be healthy while **0–3** fail: treat that as **downstream of the mux** (port wiring, INA power, or a bad INA), not as “ref code vs INA code” in isolation.

## 4) Software verification (on the Pi)

1. Stop the service if it holds the bus: e.g. `sudo systemctl stop iccp` (frees I²C / PWM as applicable).
2. Run **`iccp probe`** (or `python -m hw_probe`): every **STEP 1b** line for anodes and for ADS should be green.
3. Start: **`iccp start`** (or your normal foreground command) and confirm the log line:  
   **`[sensors] INA219 initialized on 4 channels`** (with your address list), and **no** `Hardware init failed` / empty `_sensors` follow-up.
4. TUI / dashboard: anode rows should show real **BusV** / **mA**, not `no hardware` / `--` when the cell is powered and gated.

## 5) If one channel is bad

- Temporarily set `INA219_ADDRESSES` / `NUM_CHANNELS` in config to a **single** good channel to validate the rest of the stack, then re-enable as you repair each port.

## Success criteria

- `iccp start` logs successful INA init for **all** configured anode channels.
- `read_all_real` does not fill `error: no hardware` for every channel.
- TUI shows plausible `bus_v` and `mA` with power applied.

## Optional: simulation only

- `COILSHIELD_SIM=1` bypasses INA hardware (for software/bench work); it does not fix the bus.
