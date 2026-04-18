# CoilShield (ICCP)

Impressed-current cathodic protection monitor/controller for HVAC-style coils.  
**Aluminum-safe default:** `TARGET_MA = 0.5` per channel (see `config/settings.py`).

**How this maps to “standard ICCP”:** this firmware regulates **shunt current** toward a fixed mA target; it does not measure structure potential vs a reference electrode (e.g. industry protection criteria like −0.85 V CSE). See [docs/iccp-vs-coilshield.md](docs/iccp-vs-coilshield.md) for a full factual comparison.

## Simulator (macOS / no hardware)

On macOS, if `COILSHIELD_SIM` is unset, it defaults to `1` so `board` / `RPi.GPIO` are not imported.

```bash
cd ~/coilshield
python3 main.py --sim -v
# or explicitly:
COILSHIELD_SIM=1 python3 main.py --sim
# Full loop with sim reference + temp, skip commissioning wait:
COILSHIELD_SIM=1 python3 main.py --sim --verbose --skip-commission
```

Force real hardware path (Linux/Pi only):

```bash
COILSHIELD_SIM=0 python3 main.py --real
```

## Raspberry Pi

1. Enable I2C: `sudo raspi-config` → Interface Options → I2C, or  
   `sudo raspi-config nonint do_i2c 0` then reboot if needed.
2. Install deps:  
   `sudo apt update && sudo apt install -y python3-pip i2c-tools`  
   `sudo pip3 install -r requirements.txt --break-system-packages`  
   (Same line as in the project plan; needed for `board` / `adafruit_ina3221` / `RPi.GPIO` on the system Python.)
3. Verify bus: `sudo i2cdetect -y 1` (expect `40` and `41` when two INA3221 chips are wired for five channels; expect `48` when a PCF8591 reference ADC is present).
4. If the matrix is all `--`, run **`./scripts/diagnose_i2c.sh`** (lists adapters, scans the configured bus, prints expected addresses). First run **`sudo i2cdetect -l`** and scan the **`i2c-N`** that matches the **header** I2C (on many Pis this is **`bcm2835 (i2c@7e804000)` → bus `1`**). Bus **`2`** is often a different controller, not the pins on 3/5—an empty scan there is normal if nothing is wired to it. A full grid of `--` on the **correct** bus means no device acknowledged the bus: check **power**, **SDA/SCL/GND** to each breakout, and **3.3 V** I2C levels.

### Reference electrode (zinc + PCF8591)

The firmware reads **zinc-rod to circuit ground** potential through an **I2C ADC** (default **PCF8591** at `0x48`, channel **AIN0** — see `config/settings.py`: `ADC_CHIP`, `ZINC_REF_ADC_CHANNEL`). Wire the zinc reference rod to **AIN0**, share **SDA/SCL** and **GND** with the Pi, and use the ADC **VREF** wiring per your breakout (typically 3.3 V). The outer loop compares polarization **shift (mV)** against `TARGET_SHIFT_MV` / `MAX_SHIFT_MV` and nudges `TARGET_MA` over time.

### DS18B20 temperature

**Drain-pan / sump air** temperature uses a **DS18B20** on the Pi **1-Wire** bus (no extra Python package — reads `/sys/bus/w1/devices/28-*/w1_slave`). Wiring: **VCC 3.3 V**, **GND**, **DATA → GPIO4** with a **4.7 kΩ** pull-up to 3.3 V. Enable 1-Wire: `sudo raspi-config` → Interface Options → 1-Wire, or add `dtoverlay=w1-gpio` to `/boot/firmware/config.txt` (reboot). Load modules if needed: `sudo modprobe w1-gpio && sudo modprobe w1-therm`.

### Commissioning

On first start (no `commissioning.json` in the project root), `main.py` runs **self-commissioning**: **Phase 1** turns all channels off, waits `COMMISSIONING_SETTLE_S`, averages zinc ADC samples → saves **`native_mv`**. **Phase 2** ramps per-channel target current until the zinc shift reaches `TARGET_SHIFT_MV` **five** consecutive confirmations, then writes **`commissioned_target_ma`** and timestamps into `commissioning.json`.

**Bench / dev without waiting on hardware:** run with **`--skip-commission`** so the controller starts immediately (native baseline will not be set until you commission for real).

**Force re-commissioning** (e.g. after replacing the zinc rod or major rewiring): from the repo root, run:

```bash
python3 -c "import commissioning; commissioning.reset()"
```

…or delete `commissioning.json` manually, then restart `main.py`.

### Bench series resistor vs this firmware

On a **bench rig** with a fixed supply, no feedback, and no PWM, a **series resistor** is often the only current limiter: it trades voltage for safety and cannot adapt when cell impedance changes.

**This controller does not need that resistor in the electrochemical path.** The INA3221 measures real channel current; the Pi adjusts **PWM duty** every sample toward `TARGET_MA` in `config/settings.py`. Changing condensate impedance is handled by the loop (more or less duty), not by burning headroom in a fixed ballast. Software still enforces `MAX_MA` and bus voltage limits per channel.

**Do keep a small gate resistor** (order ~100 Ω) from each GPIO to its MOSFET gate to protect the driver output—that is standard practice and is **not** the same as series cell current limiting.

## Web dashboard (live + history + benchmarks)

Run the controller and dashboard from the repo root (e.g. `~/coilshield`):

```bash
# Terminal 1 — controller (sim on Mac / Pi without wiring)
COILSHIELD_SIM=1 python3 main.py --sim --verbose

# Terminal 2 — dashboard (LAN: http://<pi-ip>:8080)
python3 dashboard.py --host 0.0.0.0 --port 8080
```

Install Flask on the Pi if needed: `python3 -m pip install flask --break-system-packages` (see `requirements.txt`).

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

Telemetry includes **reference shift (mV)**, **protection status**, and **temperature (°F)** in `latest.json`, SQLite, CSV, and the web dashboard when the sensors are present (sim mode fills realistic placeholders).

## Fault latch

When a channel trips (overcurrent, bus over/undervoltage), that channel stays off until you clear the latch:

```bash
touch ~/coilshield/clear_fault
```

(Uses `config/settings.CLEAR_FAULT_FILE`.)

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
.venv/bin/pip install pytest
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
