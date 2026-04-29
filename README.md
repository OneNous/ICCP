# CoilShield (ICCP)

Impressed-current cathodic protection monitor/controller for HVAC-style coils.  
**Defaults:** see `TARGET_MA` (default **0.5 mA** aluminum-conservative; raise for bench), `CHANNEL_WET_THRESHOLD_MA`, and anode limits in `config/settings.py` (commissioning writes `commissioned_target_ma`). **INA219 shunt:** v1 hardware uses **1.0** Ω in `INA219_SHUNT_OHMS` (legacy **0.1** Ω breakouts still work). Override without editing the file: **`COILSHIELD_INA219_SHUNT_OHMS`**. Wrong shunt → wrong mA and LSB-based floors.

**How this maps to “standard ICCP”:** the inner loop regulates **shunt current** toward `TARGET_MA`; the **reference** path defaults to **ADS1115** (legacy: **INA219**) for **polarization shift** vs a commissioned baseline and **nudges** `TARGET_MA`—still not the same as holding structure potential to an industry criterion (e.g. −0.85 V CSE). See [docs/iccp-comparison.md](docs/iccp-comparison.md) for diagrams, **external standards links**, and [docs/iccp-vs-coilshield.md](docs/iccp-vs-coilshield.md) for a line-by-line mapping to the code. **Field design \(R_a\)** (textbook/soil) vs **logged `impedance_ohm`:** [docs/field-ra-and-telemetry.md](docs/field-ra-and-telemetry.md). **Field install (bond, liquid line, reference, CLI):** [docs/installation-field-wiring.md](docs/installation-field-wiring.md). **Post–v1 / fleet software roadmap** (adaptive loops, EQI, field temp comp, etc.): [docs/post-v1-software-waves.md](docs/post-v1-software-waves.md).

## One CLI, one way

Every supported operation goes through the single `iccp` console script (installed by `pip install -e .`). Direct execution of `python3 main.py`, `python3 tui.py`, `python3 hw_probe.py`, or `python3 dashboard.py` prints a redirect and exits. The `coilshield-tui` console script has been removed — use **`iccp tui`**.

| You want to… | Run |
|--------------|-----|
| Start the controller (foreground) | `iccp start` |
| Commission (write `commissioning.json`) | `iccp commission` |
| Hardware probe (I2C / INA219 / ADS1115 / PWM) | `iccp probe` |
| Terminal UI | `iccp tui` |
| Web dashboard | `iccp dashboard` |
| Pretty-print current telemetry | `iccp live` |
| Print or request diagnostic snapshot | `iccp diag [--request]` |
| Clear fault latch | `iccp clear-fault` |
| Package version | `iccp version` |

Full reference: [docs/iccp-cli-reference.md](docs/iccp-cli-reference.md).

**External desktop / Command Center apps:** the dashboard exposes read-only JSON under **`/api/*`**
(including **`/api/meta`** and **`/api/live`**) with **CORS** enabled for cross-origin `fetch` to an
SSH-forwarded `http://127.0.0.1:<port>`. Control actions stay on **`iccp`** / **`systemctl`** over
SSH. Monitoring from off-LAN is the same flow: make **SSH** reachable (e.g. Tailscale on the Pi,
or a hardened WAN forward to port 22 only — **do not** expose dashboard **8080** publicly without
extra auth). Details: [docs/desktop-app-integration.md](docs/desktop-app-integration.md).

**Hardware knowledge base** (part datasheets + **Raspberry Pi GPIO** tables): [docs/knowledge-base/README.md](docs/knowledge-base/README.md) · short Pi GPIO note: [docs/raspberry-pi-gpio.md](docs/raspberry-pi-gpio.md).

## Simulator (bench / no hardware)

The controller defaults to **hardware** (`COILSHIELD_SIM` unset means `0`). For a laptop or bench run without a Pi, pass **`--sim`** (or export **`COILSHIELD_SIM=1`**) so sensors and GPIO stay simulated.

```bash
cd ~/coilshield
iccp start --sim -v
# equivalent:
COILSHIELD_SIM=1 iccp start --sim
# Full loop with sim reference + temp, skip commissioning wait:
iccp start --sim --verbose --skip-commission
```

On the Raspberry Pi, just run **`iccp start`** (or **`iccp start --real`** to force `COILSHIELD_SIM=0` if your shell had sim set).

## Raspberry Pi

If the log line says `sim=True` but you expect hardware, check **`COILSHIELD_SIM`** in your environment or systemd unit (`Environment=COILSHIELD_SIM=1` is easy to copy from a laptop). **The controller clears that on a Raspberry Pi** unless you start with **`--sim`**.

1. Enable I2C: `sudo raspi-config` → Interface Options → I2C, or  
   `sudo raspi-config nonint do_i2c 0` then reboot if needed.
2. Install deps:  
   `sudo apt update && sudo apt install -y python3-pip i2c-tools`  
   `sudo pip3 install -r requirements.txt --break-system-packages`  
   (Uses `pi-ina219`, `RPi.GPIO`, etc. from `requirements.txt`.)

**Pi OS Bookworm / kernel 6.x and ADS1115 ALRT:** Stock `RPi.GPIO` often raises *Error waiting for edge* on `GPIO.wait_for_edge` even with correct wiring. **`ADS1115_ALRT_USE_WAIT_FOR_EDGE`** defaults to **`False`** in `config/settings.py` so the ADS1115 path uses conversion-register polling (same accuracy). To use the ALRT pin for edges, install the drop-in **`rpi-lgpio`** package (same `import RPi.GPIO` name) and set **`ADS1115_ALRT_USE_WAIT_FOR_EDGE = True`**.

**TI ADS1115 ALERT/RDY (conversion-ready):** The firmware builds the config word with **`COMP_QUE = 0b00`** (see `i2c_bench._ads1115_config_word`) and, on init, programs Lo/Hi threshold registers so the open-drain ALERT line can pulse when a conversion completes (`reference._init_ref_ads1115`). Successful threshold programming logs **`ADS1115 ALERT/RDY threshold registers OK`**; if you see **`threshold init skipped`**, ALRT pulsing may be unreliable—check I2C to the ADS1115.

**ADS1115 reference calibration:** Default **`ADS1115_FSR_V = 2.048`** (±2.048 V PGA) matches many Ag/AgCl divider rigs against a handheld meter; raise to **4.096** only if the AIN node can exceed ±2.048 V. At a steady PWM state, compare a **DMM (V DC)** at **AIN** to logged `ref_raw_mv` — set **`REF_ADS_SCALE`** (or env **`COILSHIELD_REF_ADS_SCALE`**) so `ref_raw_mv/1000` matches the meter if the divider still disagrees. Optional numeric **`ref_ads_scale`** in `commissioning.json` overrides `REF_ADS_SCALE` at runtime after commissioning loads. **TI SBAS444E digest** (register map, noise, ALERT/RDY, I²C): [docs/knowledge-base/components/ads1115-datasheet-notes.md](docs/knowledge-base/components/ads1115-datasheet-notes.md).

**Live data:** While **`iccp start`** is running, **`logs/latest.json`** is updated every tick (same JSON the dashboard and TUI read). Paths come from **`config.settings`** (`PROJECT_ROOT/logs` by default). To put telemetry elsewhere (and keep dashboard + controller aligned), set the same environment on both processes: **`COILSHIELD_LOG_DIR`** or **`ICCP_LOG_DIR`** to an **absolute** directory (relative paths are resolved under the project root), or pass **`--log-dir /abs/path/logs`** to **`iccp start`**, **`iccp dashboard`**, or **`iccp tui`** (parsed before `config.settings` loads). The dashboard **System health → Telemetry files** card and **`GET /api/live`** field **`telemetry_paths`** show the resolved paths this instance is using. Optional: set **`LATEST_JSON_INCLUDE_DIAG = True`** in `config/settings.py` for a throttled **`diag`** object (mux map, ref ALRT latch flags). For a **deep I2C snapshot** (INA219 registers, ADS config), touch **`logs/request_diag`** once per minute (see **`DIAGNOSTIC_MIN_INTERVAL_S`**) or run **`iccp diag --request`** while the controller is running; read **`logs/diagnostic_snapshot.json`** or **`GET /api/diagnostic`** on the dashboard. **`iccp live`** prints the path it reads, then the current `latest.json`.
3. Verify bus: `sudo i2cdetect -y 1` (expect **four** anode INA219s at **`40` `41` `44` `45`** by default). The **reference** INA219 may be on the same bus (e.g. **`42`**) or on a **second** `i2c-gpio` bus — see `I2C_BUS`, `REF_I2C_BUS`, and `REF_INA219_ADDRESS` in `config/settings.py`; re-strap A0/A1 on breakouts if you use other addresses. Datasheet highlights (PGA, bus range, wiring vs **IN±**): [docs/ina219-datasheet-notes.md](docs/ina219-datasheet-notes.md).
4. If the matrix is all `--`, run **`./scripts/diagnose_i2c.sh`** (lists adapters, scans anode and optional ref buses). First run **`sudo i2cdetect -l`** and scan the **`i2c-N`** that matches the **header** I2C (on many Pis this is **`bcm2835 (i2c@7e804000)` → bus `1`**). Bus **`2`** is often a different controller, not the pins on 3/5—an empty scan there is normal if nothing is wired to it. A full grid of `--` on the **correct** bus means no device acknowledged the bus: check **power**, **SDA/SCL/GND** to each breakout, and **3.3 V** I2C levels.

### Reference electrode (dedicated INA219)

**Field placement (no anode current through the sense cell, pan geometry):** see [docs/reference-electrode-placement.md](docs/reference-electrode-placement.md).

The firmware reads the reference node through a **fifth [INA219](https://www.ti.com/product/INA219)** (`REF_INA219_ADDRESS`, `REF_INA219_SHUNT_OHMS`, `REF_INA219_SOURCE`, **`REF_I2C_BUS`** in `config/settings.py`). By default **`REF_I2C_BUS = 1`** matches **`I2C_BUS`** (shared header I2C); use a gpio bit-bang bus only after adding the overlay and setting **`REF_I2C_BUS`** to that adapter number. Default **`REF_INA219_SOURCE = "bus_v"`** uses bus voltage in volts × 1000 as the scalar stored in commissioning as **`native_mv`** / shift — match this to your front-end wiring, or use **`"shunt_mv"`** if the useful signal appears across the shunt sense.

**Zinc / reference as bus voltage (no separate ADC):** You can scale a biased zinc node into the INA219 **bus voltage** inputs so the chip acts as a voltmeter (no shunt-based current path required for that use). Example divider: zinc sense node through **10 kΩ** to **VIN+**; **100 kΩ** from that node to **3.3 V**; **VIN−** and **GND** to common ground; **VCC** / I2C as on the breakout datasheet. Re-commission after resistor or topology changes so **`native_mv`** matches the new scale.

**Optional second I2C (`i2c-gpio`, kernel):** To move only the reference module off the header bus, add a bit-banged adapter in `/boot/firmware/config.txt` (Bookworm) or `/boot/config.txt`, reboot, set **`REF_I2C_BUS`** to the new adapter number (see `sudo i2cdetect -l` → `/dev/i2c-N`). **Adopted CoilShield gpio pins:** **SDA = BCM 20**, **SCL = BCM 12** (they do not overlap PWM pins `17, 27, 22, 23` or status LED **25** in `config/settings.py`):

```text
dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=20,i2c_gpio_scl=12
```

Pick **`bus=3`** (or another free index) so it does not collide with existing adapters. After reboot, `sudo i2cdetect -y 3` should show the reference INA219. On a **gpio-only** bus with no anode boards, the default ref strap is flexible; on a **shared** bus with anodes at `0x40`–`0x45`, `REF_INA219_ADDRESS` in `config/settings.py` must be a **free** 7-bit address (default **`0x42`** when using legacy `REF_ADC_BACKEND=ina219`; re-strap if that collides).

**Do not** use random web examples that put SDA on **BCM 23** — on this firmware **BCM 23 is PWM** for channel 4.

**Adafruit Blinka alternative:** You can use `busio.I2C(board.D12, board.D20)` (**SCL**, **SDA**) with `adafruit_ina219` instead of the kernel overlay **on the same pins** — pick **one** approach per wire pair (overlay **or** Blinka bitbang, not both).

**Noise:** For long leads or gpio I2C, increase **`REF_INA219_MEDIAN_SAMPLES`** (e.g. `9` or `16`) so each reference read uses the median of several samples.

The outer loop compares **shift (mV)** against `TARGET_SHIFT_MV` / `MAX_SHIFT_MV` and nudges `TARGET_MA` over time.

### DS18B20 temperature

**Drain-pan / sump air** temperature uses a **DS18B20** on the Pi **1-Wire** bus (no extra Python package — reads `/sys/bus/w1/devices/28-*/w1_slave`). Wiring: **VCC 3.3 V**, **GND**, **DATA → GPIO4** with a **4.7 kΩ** pull-up to 3.3 V. Enable 1-Wire: `sudo raspi-config` → Interface Options → 1-Wire, or add `dtoverlay=w1-gpio` to `/boot/firmware/config.txt` (reboot). Load modules if needed: `sudo modprobe w1-gpio && sudo modprobe w1-therm`. **`latest.json`** can include **`wet_onset_temp_f`** (drain-pan **°F** at the tick a channel first enters protection stats) for long-term refrigerant / airflow trend analysis. **`readings`** SQLite rows can be **batched** to reduce SD wear: **`SQLITE_FLUSH_INTERVAL_S`** and **`SQLITE_FLUSH_MAX_ROWS`** in `config/settings.py`.

**Production hardening (watchdog, GPIO, commissioning):** [docs/watchdog-failsafe.md](docs/watchdog-failsafe.md) · long-term ref **temp** slope (not short commissioning) · [docs/field-temp-comp-selfcal.md](docs/field-temp-comp-selfcal.md).

### Anode PWM frequency (`PWM_FREQUENCY_HZ`)

The Pi drives each anode MOSFET with **RPi.GPIO software PWM** at **`PWM_FREQUENCY_HZ`** (see `config/settings.py`). That is **3.3 V** logic on the **gate** only. The **switched anode / 5 V rail** (and **INA219 bus voltage** in logs) is a **separate** node, typically **~4.85–5.0 V** under load — not 3.3 V. See [docs/raspberry-pi-gpio.md](docs/raspberry-pi-gpio.md). GPIO **VOH** / drive limits vary by Pi generation. **Default is 100 Hz** so switching sits low in frequency: that usually **reduces coupled noise** on long leads, shared **I2C**, and the **ADS1115 / reference** path compared with **~1 kHz**, at the cost of **larger low-frequency cell ripple** (the interface relaxes partly between pulses) and possible **faint audible buzz** on wiring or the coil stack. **~1 kHz** tends to **smooth** the time-average cell voltage but often **injects harmonics** where analog front-ends and jumpers pick up interference. **≥20 kHz** is **inaudible** and can push switching **above** much of the ADC’s effective averaging band (layout and gate charge still matter); on a Pi, soft-PWM at very high frequency is not always as clean as a dedicated timer—**scope the gate** if you change it. For commissioning-only experiments without retuning the whole run, use **`COMMISSIONING_PWM_HZ`** (see Commissioning below). Example logic-level FET datasheet context (gate charge, **3.3 V** drive caveat): [docs/anode-mosfet-irlz44.md](docs/anode-mosfet-irlz44.md).

**PWM duty ramp (per control tick):** Inner-loop duty moves in steps aligned with **`PWM_DUTY_QUANTUM`** (default **0.01%** per hardware step; defaults: **`PWM_STEP`** 0.01% base, **REGULATE** 0.02% up / 0.01% down, **PROTECTING** 0.01% per direction). **Gate output** is rounded to **`PWM_DUTY_QUANTUM`**; raise to `0.1` for coarser steps only. Override per mode with **`PWM_STEP_UP_REGULATE`**, **`PWM_STEP_DOWN_REGULATE`**, **`PWM_STEP_UP_PROTECTING`**, and **`PWM_STEP_DOWN_PROTECTING`** in `config/settings.py` (each is % duty added or removed once per tick). **Per-anode tuning:** optional dicts **`CHANNEL_PWM_STEP_UP_REGULATE`**, **`CHANNEL_PWM_STEP_DOWN_REGULATE`**, **`CHANNEL_PWM_STEP_UP_PROTECTING`**, and **`CHANNEL_PWM_STEP_DOWN_PROTECTING`** use **0-based channel keys** (same style as **`CHANNEL_TARGET_MA`**); a channel with no entry uses the global scalar for that direction and mode, so each output can ramp faster or slower than the others without linking them. Those dicts are **ignored** when **`SHARED_RETURN_PWM = True`** (one duty for all gates, aggregate I vs sum of targets). **Stock `settings` default is `SHARED_RETURN_PWM = False`** (independent per-gate duty); set **`True`** for bank mode — see [docs/hardware-shared-anode-bank.md](docs/hardware-shared-anode-bank.md). Effective change in % per second is roughly **step ÷ `SAMPLE_INTERVAL_S`**. On real hardware, `PWMBank` calls **`ChangeDutyCycle`** with a **float** (0.0–100.0) after **`PWM_DUTY_QUANTUM`** rounding — RPi.GPIO soft-PWM supports sub-integer %; use larger **`PWM_DUTY_QUANTUM`** (e.g. `1`) if you want whole-percent only.

### Commissioning

On first start (no `commissioning.json` in the project root), the controller runs **self-commissioning**: **Phase 1** turns all channels off, then (when **`COMMISSIONING_PHASE1_OFF_VERIFY`**) confirms **software PWM is 0%** on every channel and **INA219 shunt \|I\|** is below **`COMMISSIONING_OC_CONFIRM_I_MA`** within **`COMMISSIONING_PHASE1_OFF_CONFIRM_TIMEOUT_S`** (logged immediately so you see gates-off before the long settle), **then** waits **`COMMISSIONING_SETTLE_S`**. Shunts may still be decaying right after `all_off()`; a pre-settle INA warning is possible on slow rigs. The **same off-check runs again** immediately after settle and **before** the native averaging window. After that, one control tick snapshots statuses; the native loop **does not call `update()`** between reads — **`all_off()`** each sample with **zero duties** passed into **`reference.read()`** so **probe / regulate duty** never runs during averaging. It then averages **`COMMISSIONING_NATIVE_SAMPLE_COUNT`** reference samples spaced by **`COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S`** (default **30 × 2 s**) → saves **`native_mv`**. **Phase 2** ramps per-channel target current (`COMMISSIONING_RAMP_STEP_MA` per step): after each **`COMMISSIONING_RAMP_SETTLE_S`** regulate segment, **per-channel PWM duties are saved**, outputs are cut to **0 %** (all channels together, or one channel at a time if **`COMMISSIONING_OC_SEQUENTIAL_CHANNELS`**), an **INA219 “off” gate** runs (`COMMISSIONING_OCBUS_CONFIRM_MODE` / **`COMMISSIONING_OC_CONFIRM_I_MA`**) before trusting the reference ADC, then an **open-circuit decay curve** is sampled on the ADS1115 at **`COMMISSIONING_ADS1115_DR`** (max rate by default) and the **inflection mV** (`find_oc_inflection_mv`) is used as the OC reading (when **`COMMISSIONING_OC_CURVE_ENABLED`**; otherwise legacy dwell uses **`COMMISSIONING_INSTANT_OFF_S`** + one read). For **slow OC knees** (e.g. tap water), set **`COMMISSIONING_OC_DURATION_MODE`** and use **`COMMISSIONING_OC_CURVE_DURATION_S`** / **`COMMISSIONING_OC_CURVE_POLL_S`** instead of the fixed burst count. **Duties are restored only via `set_duty`**, not by ramping through `update()`, then **one** control tick runs for coherence. **Shift** = `native_mv − that reading`. When shift reaches `TARGET_SHIFT_MV` **five** consecutive times, **`commissioned_target_ma`** and timestamps are written to `commissioning.json`. **Reference noise:** ADS1115 path uses **`REF_ADS_MEDIAN_SAMPLES`** rapid medians per `read()`; optional **`ADS1115_ALRT_GPIO`** (default **BCM 24** when ALERT/RDY is wired) can use **`GPIO.wait_for_edge`** when **`ADS1115_ALRT_USE_WAIT_FOR_EDGE`** is true (default is **false** on new installs — see Bookworm note above). If RPi.GPIO raises **RuntimeError** (e.g. *Error waiting for edge*), firmware **falls back to polled conversion timing** for the rest of the process. Set **`ADS1115_ALRT_GPIO = None`** to skip ALRT setup entirely. **`OVERCURRENT_LATCH_TICKS`** (default **1**) requires that many consecutive over-max current samples before an **OVERCURRENT** fault — raise to **2** or **3** if a channel spuriously faults on single-sample glitches during commissioning. Optional **`COMMISSIONING_PWM_HZ`** overrides **`PWM_FREQUENCY_HZ`** (default **100 Hz**) only during **Phase 1** and each **instant-off / OC curve** window if you need a different frequency for those steps only. Longer ramp soak helps high-Z bench water; tune shorter on real coil + condensate.

**Bench / dev without waiting on hardware:** run with **`--skip-commission`** so the controller starts immediately (native baseline will not be set until you commission for real).

**`iccp` CLI vs systemd (Pi):** the CLI runs **`sudo systemctl daemon-reload`** on recognized commands. **`iccp tui`**, **`iccp dashboard`**, **`iccp live`**, and **`iccp diag`** stop there — they do **not** restart the `iccp` service. **`iccp commission`** / **`iccp probe`** then run **`sudo systemctl stop iccp`** (not `restart`, so PWM is not left running). Other subcommands use **`daemon-reload` + `restart iccp`** unless you set **`ICCP_SYSTEMD_SYNC=0`**. Foreground **`iccp start`** only **`daemon-reload`** — **it does not stop** the `iccp` unit. If that unit is already **`active`**, **`iccp start` exits** unless you pass **`--force`** (unsafe if two controllers are really running). Prefer **either** the service **or** foreground `iccp start`, not both, to avoid **GPIO "channel already in use"** and I2C contention. **`iccp commission`** also refuses to run if **`latest.json`** was updated within a few seconds (another writer may still be alive) unless **`iccp commission --force`** — after **Ctrl+C** on foreground `iccp start`, **wait ~5 s** or ensure the process is gone. High shunt current at "off" is often a **second process** or gate drive — see [docs/mosfet-off-verification.md](docs/mosfet-off-verification.md). Phase 1 can use **static gate LOW** (`COMMISSIONING_PHASE1_STATIC_GATE_LOW`) instead of soft-PWM-at-0 alone.

**I2C / INA219 sanity check (especially after wiring or mux changes):** run **`iccp probe`** on the Pi; use **`--init`** if you want to force INA219 CONFIG writes before raw reads. This uses **smbus2** only and is the quickest way to see NACKs, wrong addresses, or bus errors separate from the main control loop. **No mux** (default in `config.settings`: all INA + ADS on the same SDA/SCL with unique 7-bit addresses): **STEP 1** idle scan should show every device. If **TCA9548A** is enabled (`I2C_MUX_ADDRESS` / `I2C_MUX_CHANNEL_*`), **STEP 1** is often only the mux at **0x70**, then **STEP 1b** selects each downstream port and pings the expected INA/ADS. A bare `sudo i2cdetect` without port select will not list chips behind a mux. TI part context: [docs/knowledge-base/components/tca9548a-datasheet-notes.md](docs/knowledge-base/components/tca9548a-datasheet-notes.md) (mux) · [docs/ina219-datasheet-notes.md](docs/ina219-datasheet-notes.md) (INA219).

**Force re-commissioning** (e.g. after replacing the zinc rod or major rewiring): from the repo root, run:

```bash
python3 -c "import commissioning; commissioning.reset()"
```

…or delete `commissioning.json` manually, then restart **`iccp start`**.

### Bench series resistor vs this firmware

On a **bench rig** with a fixed supply, no feedback, and no PWM, a **series resistor** is often the only current limiter: it trades voltage for safety and cannot adapt when cell impedance changes.

**This controller does not need that resistor in the electrochemical path.** The **INA219** on each anode channel measures real shunt current; the Pi adjusts **PWM duty** every sample toward `TARGET_MA` in `config/settings.py`. Changing condensate impedance is handled by the loop (more or less duty), not by burning headroom in a fixed ballast. Software still enforces `MAX_MA` and bus voltage limits per channel.

**Do keep a small gate resistor** (order ~100 Ω) from each GPIO to its MOSFET gate to protect the driver output—that is standard practice and is **not** the same as series cell current limiting. Optional part-class notes (Vishay IRLZ44, not required by firmware): [docs/anode-mosfet-irlz44.md](docs/anode-mosfet-irlz44.md).

**Power-up / no controller yet:** Until Python configures the BCM lines, gate pins may **float**; an N-FET can then conduct and put **full bus voltage** on the anode path (commissioning Phase 1 will always see high shunt current). Add **gate-to-source pull-downs** (**tens of kΩ** gate→**source**, not a low-Ω bleed from **VIN** on the INA219 — that rail stays at stack voltage while the FET is on). You can add your own **ExecStartPre** or oneshot to drive gates LOW before `iccp start`; the repo does not ship a default script. Details: [docs/mosfet-off-verification.md](docs/mosfet-off-verification.md) §0.

## Web dashboard (live + history + benchmarks)

Run the controller and dashboard from the repo root (e.g. `~/coilshield`):

```bash
# Terminal 1 — controller (sim on Mac / Pi without wiring)
iccp start --sim --verbose

# Terminal 2 — dashboard (LAN: http://<pi-ip>:8080)
iccp dashboard --host 0.0.0.0 --port 8080
```

**Terminal monitor (SSH, no browser):** the same `logs/latest.json` snapshot drives a Textual TUI.

```bash
iccp tui
# optional:  iccp tui --poll-interval 0.5 --log-dir /abs/path/logs
```

Inside the TUI: **`d`** request a diagnostic snapshot (touches `request_diag`; the controller must be running), **`D`** re-read `diagnostic_snapshot.json` only, **`f`** clear fault latch, **`t`** show resolved telemetry paths, **`p`** run an allowlisted `iccp probe --skip-pwm` in a modal, **`1` / `2`** switch Live vs Diagnostics tab, **`q`** quit.

SSH: use a capable `TERM` (e.g. `xterm-256color`) for full colors. Optional: run inside **tmux** so the session survives disconnect.

Install Flask on the Pi if needed: `python3 -m pip install flask --break-system-packages` (see `requirements.txt`). Textual is required for the TUI: `python3 -m pip install textual --break-system-packages` (also listed in `requirements.txt`).

**Telemetry files** (under repo `logs/`):

| File | Role |
|------|------|
| `latest.json` | Atomic snapshot every tick — low-latency UI |
| `coilshield.db` | SQLite WAL: `readings` (per tick), `wet_sessions` (each PROTECTING episode), `daily_totals` (per-day mA·s + wet seconds) |
| `iccp_YYYY-MM-DD.csv` | Buffered CSV (lags the DB by `LOG_INTERVAL_S`; normal) |
| `iccp_faults.log` | Deduped fault lines + `fsync` on new fault signature |

**First deploy / DB upgrade:** run **`iccp start`** once before relying on the dashboard so `DataLogger` can run SQLite migrations (adds impedance columns on older DBs).

**Dashboard vs hardware (accuracy):** The UI reads `latest.json` (and SQLite for trends) — it is not a second measurement path. Use the **same** `COILSHIELD_LOG_DIR` / `ICCP_LOG_DIR` (or `iccp dashboard --log-dir`) as **`iccp start`**; the live API exposes `telemetry_paths` and feed age so you can spot a mismatched directory or a stopped controller. If the feed stays stale, follow [docs/stale-dashboard-feed.md](docs/stale-dashboard-feed.md). **Proxies:** cell voltage ≈ bus×duty%, impedance ≈ bus/I, power ≈ bus×I (see [docs/iccp-vs-coilshield.md](docs/iccp-vs-coilshield.md)). **PROTECTING:** the “any channel wet” style flag in telemetry is true when any anode FSM is **PROTECTING**, not merely shunt current above a wet threshold. **Targets:** each channel row includes **`target_ma`** (effective setpoint that tick: `CHANNEL_TARGET_MA` or runtime `TARGET_MA` after commissioning/outer loop); the overview still reports **`target_ma`** from settings for reference, plus **`target_ma_avg_live`** when per-channel values are present. If `log.record()` fails, **`recovery_touch_latest`** still updates `ts` / `ts_unix` and merges a writer error into `system_alerts` so the feed age does not lie stale for hours. History charts downsample rows and plot **average** stored target per tick (`avg_target_ma`).

**Primary benchmark metrics (logged every tick when sensors OK):**

- **Cell impedance (Ω)** per channel: `bus_v / max(current_mA/1000, 1e-6)` — coil chemistry / scaling / anode contact trends.
- **Cell voltage estimate (V)** per channel: `bus_v × (duty%/100)` — compare to ~1.6 V aluminum context alongside `MAX_MA`.
- **Cumulative charge:** `daily_totals.chN_ma_s` is mA·s while PROTECTING; **coulombs** = `ma_s / 1000`.
- **Wet sessions:** `wet_sessions` table — duration, total mA·s, avg mA, avg impedance, peak mA per episode; export JSON from `GET /api/sessions?hours=720&limit=5000` or download the whole DB.

Telemetry includes **`logs/latest.json`** fields every tick: **`ref_raw_mv`**, **`ref_shift_mv`** (JSON `null` until baseline exists), **`ref_status`** (shift band or `N/A`), **`ref_hw_ok`**, **`ref_hw_message`**, **`ref_hint`**, **`ref_baseline_set`**, plus **temperature (°F)**. The web dashboard header mirrors raw / shift / band, hardware line, and banner. **Console:** **`--verbose`** prints a one-line **`[tick]`** summary on each control tick and the **full** channel table every **`LOG_INTERVAL_S`** (default **120 s**; not every tick, avoids console spam). **Without `--verbose`**, a **`[ref] …`** line prints on each **`LOG_INTERVAL_S`** tick, and fault lines add a short ref summary.

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

**CLI output:** By default, `iccp` prints **human** text (tags, sections) for SSH and scripts. For **JSONL** events (schema `iccp.cli.event.v1`), use `iccp --jsonl <subcommand>`, or set **`ICCP_OUTPUT=jsonl`** for that process (ICCP-APP Pi Console does this for preset runs). **`iccp --human`** forces plain text even if the environment asked for JSONL. If both `--human` and `--jsonl` are passed, **`--human` wins**.

Use every session either:

```bash
source .venv/bin/activate
iccp start
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

### Upgrading an existing Pi install

The `iccp -start` / `iccp watch` / `iccp monitor` / `coilshield-tui` entry points and direct `python3 main.py` / `tui.py` / `hw_probe.py` / `dashboard.py` invocations have been removed. Use `iccp <subcommand>` for everything. After pulling this change on a Pi where the systemd unit was installed from an older `deploy/iccp.service`:

```bash
sudo cp deploy/iccp.service /etc/systemd/system/iccp.service
sudo systemctl daemon-reload
sudo systemctl restart iccp
```

Without this step, the unit will fail because its `ExecStart` still points at the now-removed `iccp -start` alias.

## Development workflow (Mac → Pi)

- **Git:** commit on Mac, `git push`; on Pi `cd ~/coilshield && git pull`.
- **rsync (fast iteration):**  
  `rsync -avz ~/coilshield/ onenous@0neNous-pi:~/coilshield/`
  Optional shell alias, e.g. `alias push-coilshield='rsync -avz ~/coilshield/ onenous@0neNous-pi:~/coilshield/'`.

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
fswatch -o ~/coilshield | xargs -n1 -I{} rsync -avz ~/coilshield/ onenous@0neNous-pi:~/coilshield/
```

Throttle as needed; many editors also have “save & upload” extensions.

## Near-term product TODO

- **systemd unit** so the controller starts after reboot (not included in this repo iteration).

## Remote

GitHub: `https://github.com/OneNous/ICCP.git`
