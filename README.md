# CoilShield (ICCP)

Impressed-current cathodic protection monitor/controller for HVAC-style coils.  
**Aluminum-safe default:** `TARGET_MA = 0.5` per channel (see `config/settings.py`).

## Simulator (macOS / no hardware)

On macOS, if `COILSHIELD_SIM` is unset, it defaults to `1` so `board` / `RPi.GPIO` are not imported.

```bash
cd ~/coilshield
python3 main.py --sim -v
# or explicitly:
COILSHIELD_SIM=1 python3 main.py --sim
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
   (Same line as in the project plan; needed for `board` / `adafruit_ina219` / `RPi.GPIO` on the system Python.)
3. Verify bus: `sudo i2cdetect -y 1` (expect `40` `41` `44` `45` when INA219s are wired).

## Fault latch

When safety trips, output stays off until you clear the latch:

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
