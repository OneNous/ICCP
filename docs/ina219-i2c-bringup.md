# INA219 / (optional) TCA9548A I²C bring-up (anode shunt path)

**Repo default** in [`config/settings.py`](../config/settings.py) is **no multiplexer** — all **INA219** and **ADS1115** on the same **SDA/SCL** (unique 7-bit addresses: default INAs **0x40, 0x41, 0x44, 0x45**, ADS **0x48**). A **TCA9548A** is optional: set `I2C_MUX_ADDRESS` and the `I2C_MUX_CHANNEL_*` fields, or `COILSHIELD_MUX_ADDRESS=0x70` plus matching channel config, only if your PCB still uses a mux.

**Related:** [architecture-channel-i2c-reference.md](architecture-channel-i2c-reference.md) (channel index ↔ address ↔ mux, ref vs anode code paths, import order). If import-time INA init fails, [`sensors.py`](../src/sensors.py) leaves `_sensors` empty until I²C is healthy; the runtime also **retries** full INA init on a throttle (`INA219_REINIT_MIN_INTERVAL_S` in `config/settings.py`) on each `read_all_real` tick — see also [`ina219-datasheet-notes.md`](ina219-datasheet-notes.md).

**Read timeout / hang:** The Python `pi-ina219` path has no per-call wall-clock timeout. Stuck SCL, a wedged device, or a bad mux can block `read_all_real` until the kernel I²C layer returns (Linux exposes `i2c` adapter timeout via module params / `i2c_timeout` on some drivers — check `dmesg` and your kernel docs). Firmware already **reopens** `/dev/i2c-N` on some mux EIO paths (`I2C_MUX_SMBUS_REOPEN_ON_SELECT_EIO`); a full bus hang may still require process restart or power cycle.

## 1) Align `config/settings.py` with the board

- `I2C_BUS` = Pi I²C (often `1` on 40‑pin header).
- **Direct to header (no mux)** — all four anode INA219s + ref ADS on the same SDA/SCL, unique straps per module:
  - `I2C_MUX_ADDRESS` = `None`
  - `I2C_MUX_CHANNEL_ADS1115` = `None`
  - `I2C_MUX_CHANNEL_INA219` = `None`
  - `I2C_MUX_CHANNELS_INA219` = `None`
- **TCA9548A on the PCB (muxed):** e.g. `I2C_MUX_ADDRESS = 0x70`, `I2C_MUX_CHANNELS_INA219 = (0, 1, 2, 3)`, `I2C_MUX_CHANNEL_ADS1115 = 4` (adjust to your straps). If the ref path works on **port 4** but anodes on **0–3** fail, that isolates a downstream / INA / power issue, not “ref vs anode” code in isolation.
- `INA219_ADDRESSES` and **A0/A1** straps on each INA board must match the list (default **0x40, 0x41, 0x44, 0x45**).
- If you see sporadic `Errno 5` (EIO) or **`Errno 110` (Connection timed out)** on mux/INA/ADS, the firmware **retries** the same class of errors; persistent 110 usually means the bus is **hung** (SCL stuck, no ACK, bad wiring, or another process holding the bus). On a muxed rig, try `I2C_MUX_POST_SELECT_DELAY_S` in the **0.001–0.002** s range and confirm nothing else is using I²C.
- **Do not** apply a no-mux `settings` profile to a muxed PCB, or the reverse — addresses and `STEP 1` / `STEP 1b` in `iccp probe` will not match hardware.

## 2) Muxed PCB only — TCA first (skip if `I2C_MUX_ADDRESS` is `None`)

1. **Power / GND:** common ground between Pi, TCA9548A, INA219, and ADS1115; **3.3 V** logic on SDA/SCL to the Pi (not 5 V I²C).
2. **Select a port** — until this works with **exit 0**, downstream devices are undefined:
   - `sudo i2ctransfer -y 1 w1@0x70 0x01` (port 0)
   - or: `sudo i2cset -y 1 0x70 0x01 c` (one byte; the `c` form writes a full byte; avoid sending only one nibble in interactive mistakes).
3. If writes to **0x70** fail, fix levels, pull-ups, and wiring before debugging INA addresses.
4. On failure, check: `dmesg -T | tail -40` for `i2c-1` (or your bus) errors.

## 3) Per-port INA (muxed rigs: ports 0–3; skip on no-mux)

- After selecting **one** port, `i2cdetect -y 1` should show the INA at the **expected 7-bit address** for that branch.
- Re-seat modules; confirm **A0/A1** match the intended `INA219_ADDRESSES` list.
- **Port 4 + 0x48** can be healthy while **0–3** fail: treat that as **downstream of the mux** (port wiring, INA power, or a bad INA), not as “ref code vs INA code” in isolation.

## 4) Software verification (on the Pi)

1. Stop the service if it holds the bus: e.g. `sudo systemctl stop iccp` (frees I²C / PWM as applicable).
2. Run **`iccp probe`** (or `python -m hw_probe`): on **no-mux**, **STEP 1** idle scan should list your INA and ADS addresses. With a **mux** configured, **STEP 1b** should be green for each anode port and the ADS port. For **ongoing** confirmation, use **`iccp probe --continuous`** or **`iccp probe --live --interval 0.5`** — it streams all four INA channels and **ADS AIN0..3** (marks **`ADS1115_CHANNEL`** as ref) until Ctrl+C.
3. Start: **`iccp start`** (or your normal foreground command) and confirm the log line:  
   **`[sensors] INA219 initialized on 4 channels`** (with your address list; use **3** if running a three-INA profile), and **no** `Hardware init failed` / empty `_sensors` follow-up.

**After restoring four anodes in software** (`NUM_CHANNELS=4`, full `INA219_ADDRESSES`, four `PWM_GPIO_PINS` in `config/settings.py`): run `sudo i2cdetect -y 1` (or your `I2C_BUS`) to confirm **40 41 44 45** (and **48** for ADS), then `iccp probe` and `iccp start` as above until the log shows **`INA219 initialized on 4 channels`**. In probe **STEP 5**, the summary distinguishes **~3.3 V gate** (Pi) from the **5 V** switched rail — trust **`bus_v`** on the INA for the latter.
4. TUI / dashboard: anode rows should show real **BusV** / **mA**, not `no hardware` / `--` when the cell is powered and gated.

### `iccp probe` green but `iccp start` / `iccp commission` still sees no hardware

**Same `config` as probe (for a given `iccp` install):** The `iccp` CLI always `chdir`s to the package root and prepends it to `sys.path` before subcommands load, so `iccp probe` and `iccp commission` read the **same** [`config/settings.py`](../config/settings.py) (`I2C_MUX_ADDRESS`, `I2C_MUX_CHANNELS_INA219`, `I2C_MUX_CHANNEL_ADS1115`, `INA219_ADDRESSES`, etc.) — not a different mux map in another file. Real mismatches are usually a **different `iccp` on `PATH`** (another venv, `sudo` picking system Python instead of `sudo $(which iccp) ...`), a **non-editable** install in `site-packages` vs a checkout you are editing, or a **different** Python loading `config` (two checkouts on one Pi).

**`iccp commission` Phase 1 passes, then Phase 2 crashes with `OSError: [Errno 5] Input/output error` in `mux_select` / `write_byte` to 0x70:** Phase 1 used the ref on **port 4**; Phase 2 steps **all anode INA** ports 0–3. That is still the **TCA9548A control write** (same as probe), not Python “logic.” Firmware already **retries** mux writes and can **re-open** `/dev/i2c-N` once after a stuck select (see `I2C_MUX_SELECT_MAX_ATTEMPTS`, `I2C_MUX_SMBUS_REOPEN_ON_SELECT_EIO` in settings). If it still fails, treat it as **hardware / electrical** until `iccp probe --live` can cycle **0–3 and 4** for minutes without EIO:

- **Cabling:** short, twisted SDA/SCL, solid common **GND**; 2.2k–4.7k **pull-ups** to 3.3V if the run is long or the mux is under-fed.
- **Power:** 3.3V on the mux VCC and each INA during CP activity (loads and PWM can expose marginal supplies).
- **Narrow the fault:** if only certain ports EIO, suspect that **downstream** branch, strap, or INA, not the Pi alone.

**Why probe can work while the controller/commissioning does not:** `iccp probe` uses **smbus2** for raw I²C (mux select, INA/ADS pokes) via [`hw_probe.py`](../src/hw_probe.py). `iccp start` and `iccp commission` **import** [`sensors.py`](../src/sensors.py), which runs **`pi-ina219`** `INA219.configure()` at import time. If that throws, you get **`[sensors] Hardware init failed: ...`**, an empty in-memory sensor list, and anode paths report **`no hardware`** even when probe’s STEP 1/1b was green. That is a **sensors** import / library init path — not a second, hidden config file with different mux values.

**Narrow the failure (address vs mux vs config source vs import):**

1. **Wrong 7-bit address** — compare probe STEP 1b to `INA219_ADDRESSES` and the **A0/A1** straps on each breakout.
2. **Wrong mux channel** — compare `I2C_MUX_CHANNELS_INA219` / `I2C_MUX_CHANNEL_ADS1115` to the board and probe’s per-port ping.
3. **Wrong config source** — from the **same shell** you use for the failing command (ideally the same `python3` as the `iccp` entry point’s interpreter):
   - `which iccp` and `readlink -f "$(which iccp 2>/dev/null)"` (or `python3 -c "import iccp_cli; print(iccp_cli.__file__)"`).
   - `python3 -c "import config.settings as c; print('PROJECT_ROOT', c.PROJECT_ROOT); print('MUX', c.I2C_MUX_ADDRESS, c.I2C_MUX_CHANNELS_INA219, c.I2C_MUX_CHANNEL_ADS1115); print('INA', c.INA219_ADDRESSES)"`  
4. **Import init vs I²C poke** — look for **`[sensors] INA219 initialized on N channels ...`** vs **`[sensors] Hardware init failed: ...`** (and the follow-up about no anode INA objects) at `sensors` import time. Those lines are definitive for the controller/commissioning process, not the probe.

**For support, paste the full `iccp commission` transcript:** from `[iccp] systemctl stop ...` through `[iccp commission] Reference path: ...`, all **`[sensors] ...`** lines, **`[commission ...]`** lines, and the final `Done` / `Native capture failed` / `ERROR: ...` block.

## 5) Fewer than four INA219s (failed board, one address shorted, etc.)

- Set **`NUM_CHANNELS`** to the number of **working** INA modules (1–4).
- Set **`INA219_ADDRESSES`** to a list of **that length** — one 7-bit address per row, same order as **Anode 1, 2, …** in the UI (idx 0, 1, …). Example: first anode / INA dead → `NUM_CHANNELS = 3`, `INA219_ADDRESSES = [0x41, 0x44, 0x45]`, and **`PWM_GPIO_PINS`** with the same length (e.g. drop BCM **17** if anode 1’s gate is unused).
- With a **TCA9548A**, set **`I2C_MUX_CHANNELS_INA219`** to a tuple of **length `NUM_CHANNELS`** (only the ports that still have INAs). Do not leave a fourth tuple entry for a missing device.
- Commissioning / TUI / `latest.json` use **0..NUM_CHANNELS−1** only. Labels **“Anode 1”** in the UI are **firmware** channel 0 (first address in the list), not necessarily “harness anode 1” if you removed a cell from the front of the list.
- For a **minimal** bring-up, you can set **`NUM_CHANNELS = 1`** and a **single** address to validate the rest of the stack, then add channels as you repair hardware.

## Success criteria

- `iccp start` logs successful INA init for **all** configured anode channels.
- `read_all_real` does not fill `error: no hardware` for every channel.
- TUI shows plausible `bus_v` and `mA` with power applied.

## Optional: simulation only

- `COILSHIELD_SIM=1` bypasses INA hardware (for software/bench work); it does not fix the bus.
