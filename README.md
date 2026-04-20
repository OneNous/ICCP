# CoilShield (ICCP)

Impressed-current cathodic protection monitor/controller for HVAC-style coils.  
**Defaults:** see `TARGET_MA`, `CHANNEL_WET_THRESHOLD_MA`, and anode limits in `config/settings.py` (values ship tuned for the bench rig; commissioning writes `commissioned_target_ma`).

**How this maps to “standard ICCP”:** the inner loop regulates **shunt current** toward `TARGET_MA`; a **dedicated INA219** on the reference electrode provides **polarization shift** vs a commissioned baseline and **nudges** `TARGET_MA`—still not the same as holding structure potential to an industry criterion (e.g. −0.85 V CSE). See [docs/iccp-comparison.md](docs/iccp-comparison.md) for diagrams, **external standards links**, and [docs/iccp-vs-coilshield.md](docs/iccp-vs-coilshield.md) for a line-by-line mapping to the code.

## Simulator (bench / no hardware)

`main.py` defaults to **hardware** (`COILSHIELD_SIM` unset means `0`). For a laptop or bench run without a Pi, pass **`--sim`** (or export **`COILSHIELD_SIM=1`**) so sensors and GPIO stay simulated.

```bash
cd ~/coilshield
python3 main.py --sim -v
# equivalent:
COILSHIELD_SIM=1 python3 main.py --sim
# Full loop with sim reference + temp, skip commissioning wait:
python3 main.py --sim --verbose --skip-commission
```

On the Raspberry Pi, run without `--sim` (or **`python3 main.py --real`** to force `COILSHIELD_SIM=0` if your shell had sim set).

## Raspberry Pi

If the log line says `sim=True` but you expect hardware, check **`COILSHIELD_SIM`** in your environment or systemd unit (`Environment=COILSHIELD_SIM=1` is easy to copy from a laptop). **`main.py` clears that on a Raspberry Pi** unless you start with **`--sim`**.

1. Enable I2C: `sudo raspi-config` → Interface Options → I2C, or  
   `sudo raspi-config nonint do_i2c 0` then reboot if needed.
2. Install deps:  
   `sudo apt update && sudo apt install -y python3-pip i2c-tools`  
   `sudo pip3 install -r requirements.txt --break-system-packages`  
   (Uses `pi-ina219`, `RPi.GPIO`, etc. from `requirements.txt`.)
3. Verify bus: `sudo i2cdetect -y 1` (expect **four** anode INA219s at **`40` `41` `44` `45`** by default). The **reference** INA219 may be on the same bus (e.g. **`42`**) or on a **second** `i2c-gpio` bus — see `I2C_BUS`, `REF_I2C_BUS`, and `REF_INA219_ADDRESS` in `config/settings.py`; re-strap A0/A1 on breakouts if you use other addresses.
4. If the matrix is all `--`, run **`./scripts/diagnose_i2c.sh`** (lists adapters, scans anode and optional ref buses). First run **`sudo i2cdetect -l`** and scan the **`i2c-N`** that matches the **header** I2C (on many Pis this is **`bcm2835 (i2c@7e804000)` → bus `1`**). Bus **`2`** is often a different controller, not the pins on 3/5—an empty scan there is normal if nothing is wired to it. A full grid of `--` on the **correct** bus means no device acknowledged the bus: check **power**, **SDA/SCL/GND** to each breakout, and **3.3 V** I2C levels.

### Reference electrode (dedicated INA219)

The firmware reads the reference node through a **fifth [INA219](https://www.ti.com/product/INA219)** (`REF_INA219_ADDRESS`, `REF_INA219_SHUNT_OHMS`, `REF_INA219_SOURCE`, **`REF_I2C_BUS`** in `config/settings.py`). By default **`REF_I2C_BUS`** matches **`I2C_BUS`** (one shared header I2C). Default **`REF_INA219_SOURCE = "bus_v"`** uses bus voltage in volts × 1000 as the scalar stored in commissioning as **`native_mv`** / shift — match this to your front-end wiring, or use **`"shunt_mv"`** if the useful signal appears across the shunt sense.

**Zinc / reference as bus voltage (no separate ADC):** You can scale a biased zinc node into the INA219 **bus voltage** inputs so the chip acts as a voltmeter (no shunt-based current path required for that use). Example divider: zinc sense node through **10 kΩ** to **VIN+**; **100 kΩ** from that node to **3.3 V**; **VIN−** and **GND** to common ground; **VCC** / I2C as on the breakout datasheet. Re-commission after resistor or topology changes so **`native_mv`** matches the new scale.

**Optional second I2C (`i2c-gpio`, kernel):** To move only the reference module off the header bus, add a bit-banged adapter in `/boot/firmware/config.txt` (Bookworm) or `/boot/config.txt`, reboot, set **`REF_I2C_BUS`** to the new adapter number (see `sudo i2cdetect -l` → `/dev/i2c-N`). **Adopted CoilShield gpio pins:** **SDA = BCM 20**, **SCL = BCM 12** (they do not overlap PWM pins `17, 27, 22, 23` or status LED **25** in `config/settings.py`):

```text
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=20,i2c_gpio_scl=12
```

Pick **`bus=3`** (or another free index) so it does not collide with existing adapters. After reboot, `sudo i2cdetect -y 3` should show the reference INA219. On a **gpio-only** bus with no anode boards, **`REF_INA219_ADDRESS = 0x40`** is allowed; on a **shared** bus with anodes at `0x40`–`0x45`, use a free strap such as **`0x46`** or **`0x47`**.

**Do not** use random web examples that put SDA on **BCM 23** — on this firmware **BCM 23 is PWM** for channel 4.

**Adafruit Blinka alternative:** You can use `busio.I2C(board.D12, board.D20)` (**SCL**, **SDA**) with `adafruit_ina219` instead of the kernel overlay **on the same pins** — pick **one** approach per wire pair (overlay **or** Blinka bitbang, not both).

**Noise:** For long leads or gpio I2C, increase **`REF_INA219_MEDIAN_SAMPLES`** (e.g. `9` or `16`) so each reference read uses the median of several samples.

The outer loop compares **shift (mV)** against `TARGET_SHIFT_MV` / `MAX_SHIFT_MV` and nudges `TARGET_MA` over time.

### DS18B20 temperature

**Drain-pan / sump air** temperature uses a **DS18B20** on the Pi **1-Wire** bus (no extra Python package — reads `/sys/bus/w1/devices/28-*/w1_slave`). Wiring: **VCC 3.3 V**, **GND**, **DATA → GPIO4** with a **4.7 kΩ** pull-up to 3.3 V. Enable 1-Wire: `sudo raspi-config` → Interface Options → 1-Wire, or add `dtoverlay=w1-gpio` to `/boot/firmware/config.txt` (reboot). Load modules if needed: `sudo modprobe w1-gpio && sudo modprobe w1-therm`.

### Anode PWM frequency (`PWM_FREQUENCY_HZ`)

The Pi drives each anode MOSFET with **RPi.GPIO software PWM** at **`PWM_FREQUENCY_HZ`** (see `config/settings.py`). **Default is 100 Hz** so switching sits low in frequency: that usually **reduces coupled noise** on long leads, shared **I2C**, and the **ADS1115 / reference** path compared with **~1 kHz**, at the cost of **larger low-frequency cell ripple** (the interface relaxes partly between pulses) and possible **faint audible buzz** on wiring or the coil stack. **~1 kHz** tends to **smooth** the time-average cell voltage but often **injects harmonics** where analog front-ends and jumpers pick up interference. **≥20 kHz** is **inaudible** and can push switching **above** much of the ADC’s effective averaging band (layout and gate charge still matter); on a Pi, soft-PWM at very high frequency is not always as clean as a dedicated timer—**scope the gate** if you change it. For commissioning-only experiments without retuning the whole run, use **`COMMISSIONING_PWM_HZ`** (see Commissioning below).

### Commissioning

On first start (no `commissioning.json` in the project root), `main.py` runs **self-commissioning**: **Phase 1** turns all channels off, waits `COMMISSIONING_SETTLE_S`, then averages **`COMMISSIONING_NATIVE_SAMPLE_COUNT`** reference INA219 samples spaced by **`COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S`** (default **30 × 2 s**) → saves **`native_mv`**. **Phase 2** ramps per-channel target current (`COMMISSIONING_RAMP_STEP_MA` per step): after each **`COMMISSIONING_RAMP_SETTLE_S`** regulate segment, **per-channel PWM duties are saved**, outputs are cut to **0 %** (all channels together, or one channel at a time if **`COMMISSIONING_OC_SEQUENTIAL_CHANNELS`**), an **INA219 “off” gate** runs (`COMMISSIONING_OCBUS_CONFIRM_MODE` / **`COMMISSIONING_OC_CONFIRM_I_MA`**) before trusting the reference ADC, then an **open-circuit decay curve** is sampled on the ADS1115 at **`COMMISSIONING_ADS1115_DR`** (max rate by default) and the **inflection mV** (`find_oc_inflection_mv`) is used as the OC reading (when **`COMMISSIONING_OC_CURVE_ENABLED`**; otherwise legacy dwell uses **`COMMISSIONING_INSTANT_OFF_S`** + one read). For **slow OC knees** (e.g. tap water), set **`COMMISSIONING_OC_DURATION_MODE`** and use **`COMMISSIONING_OC_CURVE_DURATION_S`** / **`COMMISSIONING_OC_CURVE_POLL_S`** instead of the fixed burst count. **Duties are restored only via `set_duty`**, not by ramping through `update()`, then **one** control tick runs for coherence. **Shift** = `native_mv − that reading`. When shift reaches `TARGET_SHIFT_MV` **five** consecutive times, **`commissioned_target_ma`** and timestamps are written to `commissioning.json`. **Reference noise:** ADS1115 path uses **`REF_ADS_MEDIAN_SAMPLES`** rapid medians per `read()`; optional **`ADS1115_ALRT_GPIO`** (default **BCM 24** when ALERT/RDY is wired) uses `wait_for_edge` instead of blind sleeps for conversion-ready. Optional **`COMMISSIONING_PWM_HZ`** overrides **`PWM_FREQUENCY_HZ`** (default **100 Hz**) only during **Phase 1** and each **instant-off / OC curve** window if you need a different frequency for those steps only. Longer ramp soak helps high-Z bench water; tune shorter on real coil + condensate.

**Bench / dev without waiting on hardware:** run with **`--skip-commission`** so the controller starts immediately (native baseline will not be set until you commission for real).

**Force re-commissioning** (e.g. after replacing the zinc rod or major rewiring): from the repo root, run:

```bash
python3 -c "import commissioning; commissioning.reset()"
```

…or delete `commissioning.json` manually, then restart `main.py`.

### Bench series resistor vs this firmware

On a **bench rig** with a fixed supply, no feedback, and no PWM, a **series resistor** is often the only current limiter: it trades voltage for safety and cannot adapt when cell impedance changes.

**This controller does not need that resistor in the electrochemical path.** The **INA219** on each anode channel measures real shunt current; the Pi adjusts **PWM duty** every sample toward `TARGET_MA` in `config/settings.py`. Changing condensate impedance is handled by the loop (more or less duty), not by burning headroom in a fixed ballast. Software still enforces `MAX_MA` and bus voltage limits per channel.

**Do keep a small gate resistor** (order ~100 Ω) from each GPIO to its MOSFET gate to protect the driver output—that is standard practice and is **not** the same as series cell current limiting.

## Web dashboard (live + history + benchmarks)

Run the controller and dashboard from the repo root (e.g. `~/coilshield`):

```bash
# Terminal 1 — controller (sim on Mac / Pi without wiring)
COILSHIELD_SIM=1 python3 main.py --sim --verbose

# Terminal 2 — dashboard (LAN: http://<pi-ip>:8080)
python3 dashboard.py --host 0.0.0.0 --port 8080
```

**Terminal monitor (SSH, no browser):** the same `logs/latest.json` snapshot drives a Textual TUI:

```bash
python3 tui.py
# optional: python3 tui.py --poll-interval 0.5
```

Install Flask on the Pi if needed: `python3 -m pip install flask --break-system-packages` (see `requirements.txt`). Install Textual for the TUI: `python3 -m pip install textual --break-system-packages` (also listed in `requirements.txt`).

**Telemetry files** (under repo `logs/`):

| File | Role |
|------|------|
| `latest.json` | Atomic snapshot every tick — low-latency UI |
| `coilshield.db` | SQLite WAL: `readings` (per tick), `wet_sessions` (each PROTECTING episode), `daily_totals` (per-day mA·s + wet seconds) |
| `iccp_YYYY-MM-DD.csv` | Buffered CSV (lags the DB by `LOG_INTERVAL_S`; normal) |
| `iccp_faults.log` | Deduped fault lines + `fsync` on new fault signature |

**First deploy / DB upgrade:** start `main.py` once before relying on the dashboard so `DataLogger` can run SQLite migrations (adds impedance columns on older DBs).

**Primary benchmark metrics (logged every tick when sensors OK):**

- **Cell impedance (Ω)** per channel: `bus_v / max(current_mA/1000, 1e-6)` — coil chemistry / scaling / anode contact trends.
- **Cell voltage estimate (V)** per channel: `bus_v × (duty%/100)` — compare to ~1.6 V aluminum context alongside `MAX_MA`.
- **Cumulative charge:** `daily_totals.chN_ma_s` is mA·s while PROTECTING; **coulombs** = `ma_s / 1000`.
- **Wet sessions:** `wet_sessions` table — duration, total mA·s, avg mA, avg impedance, peak mA per episode; export JSON from `GET /api/sessions?hours=720&limit=5000` or download the whole DB.

Telemetry includes **`logs/latest.json`** fields every tick: **`ref_raw_mv`**, **`ref_shift_mv`** (JSON `null` until baseline exists), **`ref_status`** (shift band or `N/A`), **`ref_hw_ok`**, **`ref_hw_message`**, **`ref_hint`**, **`ref_baseline_set`**, plus **temperature (°F)**. The web dashboard header mirrors raw / shift / band, hardware line, and banner. **Console:** `--verbose` prints the full table with a two-line ref block; **without `--verbose`**, a **`[ref] …`** summary prints on each **`LOG_INTERVAL_S`** tick (same cadence as the outer loop), and fault lines append a short ref summary.

## Fault latch

When a channel trips (overcurrent, bus over/undervoltage), that channel stays off until you clear the latch:

```bash
touch ~/coilshield/clear_fault
```

(Uses `config/settings.CLEAR_FAULT_FILE`.)

## ICCP command on the Raspberry Pi (`~/coilshield`)

The `iccp` entry point is declared in `pyproject.toml` (`[project.scripts]`). It only exists **after** `pip install -e .` into an environment whose `bin` directory is on your `PATH` (or when you call the script by full path).

**Why `iccp: command not found`:** the console script is installed as `.venv/bin/iccp` (or `~/.local/bin/iccp` with `--user`). A plain shell does not see it until you **activate** that venv or **prefix** the path.

**Recommended (venv on the Pi):**

```bash
cd ~/coilshield
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
```

On a Mac or other non-Pi machine, `pip install -e .` may fail while building **RPi.GPIO** — that is expected. Use a Raspberry Pi for the full install, or run `PYTHONPATH=~/coilshield python3 iccp_cli.py --help` from the repo root to sanity-check the CLI without installing GPIO wheels.

Verify:

```bash
.venv/bin/iccp --help
```

Use every session either:

```bash
source .venv/bin/activate
iccp -start
```

or without activating:

```bash
~/coilshield/.venv/bin/iccp probe
```

**PEP 668 / “externally managed”:** avoid `sudo pip install` into system Python; keep using the venv commands above (same idea as the Tests section).

**If `pip install -e .` fails:** try `.venv/bin/pip install -U pip setuptools wheel` first. Hardware deps (`RPi.GPIO`, `smbus2`, etc.) install into the same venv as `iccp`.

**Optional — user install without a venv:**

```bash
cd ~/coilshield
python3 -m pip install --user -e .
```

Ensure `~/.local/bin` is on your `PATH` for login shells (many Pi images already include it). Then run `~/.local/bin/iccp --help` to confirm.

## Development workflow (Mac → Pi)

- **Git:** commit on Mac, `git push`; on Pi `cd ~/coilshield && git pull`.
- **rsync (fast iteration):**  
  `rsync -avz ~/coilshield/ user@pi:~/coilshield/`  
  Optional shell alias, e.g. `alias push-coilshield='rsync -avz ~/coilshield/ user@pi:~/coilshield/'`.

## Tests

On Raspberry Pi OS (PEP 668), use a project venv for pytest:

```bash
cd ~/coilshield
python3 -m venv .venv
.venv/bin/pip install pytest textual
COILSHIELD_SIM=1 .venv/bin/python -m pytest tests/ -q
```

## Optional: auto-rsync on save (Mac)

If you use [fswatch](https://github.com/emcrisostomo/fswatch) on the Mac:

```bash
fswatch -o ~/coilshield | xargs -n1 -I{} rsync -avz ~/coilshield/ user@pi-ip:~/coilshield/
```

Throttle as needed; many editors also have “save & upload” extensions.

## Near-term product TODO

- **systemd unit** so the controller starts after reboot (not included in this repo iteration).

## Remote

GitHub: `https://github.com/OneNous/ICCP.git`
