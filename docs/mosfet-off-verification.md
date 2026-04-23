# Verifying MOSFETs are fully off (high shunt at “0% PWM”)

## 0. Pi powered, firmware not running — “software off” does not exist yet

GPIO lines used for the MOSFET gates are **not configured** until a Python process runs `RPi.GPIO` (or another driver). Officially, after **power-on reset** the SoC leaves header pins as **inputs** with **default pulls** until software reconfigures them — see [raspberry-pi-gpio.md](raspberry-pi-gpio.md) and the long-form [knowledge-base/components/raspberry-pi-gpio-header.md](knowledge-base/components/raspberry-pi-gpio-header.md) (from Raspberry Pi [gpio-on-raspberry-pi.adoc](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/gpio-on-raspberry-pi.adoc)). On an **N-channel** low-side switch, a **floating gate** can sit undefined or high enough that the FET **conducts**, so you can measure **full bus voltage (~4.8–5 V)** on the anode path with the script stopped and only the Pi powered. That is **normal** for a gate network without a defined power-up default — and it **will** make commissioning Phase 1 fail (high shunt mA, “not at rest”) until either:

1. **Hardware (required for safety):** **Gate-to-source** pull-downs (**tens of kΩ** from **gate to MOSFET source**, not a few Ω from “some” node to GND). A **100 Ω** path from a rail to ground does **not** turn off an N-FET and does **not** remove **VIN** on the INA219 breakout — that pin is your **stack / bus feed**; if the FET is on, you will still read ~**4.8 V** there until the gate is held **low vs source** and the channel is off.
2. **Software (hold until iccp runs):** Run **`scripts/anode_gates_hold_low.py`** before the controller:
   - **Recommended:** `deploy/iccp.service` uses **`ExecStartPre=`** so systemd runs the script **immediately before every** `iccp start` (boot and restarts).
   - **Optional:** `deploy/iccp-anode-gpio-init.service` as a separate **boot oneshot** if you need gates LOW even when the main `iccp` unit is **disabled**.

The script sets BCM pins **OUTPUT LOW** and skips `GPIO.cleanup()` so pins may stay latched until `iccp` reopens them. Pull-downs still define behavior when the SoC is not driving the line.

Firmware normally turns channels “off” with RPi.GPIO **soft-PWM** at **0% duty** (`ChangeDutyCycle(0)`). Shutdown uses **`pwm.stop()`** plus **`GPIO.output(LOW)`**, which can behave differently on the gate than soft-PWM at 0. Commissioning Phase 1 can optionally use **static LOW** (see `COMMISSIONING_PHASE1_STATIC_GATE_LOW` in `config/settings.py`).

## 1. Single process owns PWM

Only one of **`iccp start`** (foreground or systemd unit) or **`iccp commission`** should control the Pi's PWM GPIO at a time.

- Stop the service: `sudo systemctl stop iccp`
- Confirm nothing else: `ps aux | grep -E 'iccp'`
- If `latest.json` was updated seconds ago, `iccp commission` aborts unless you pass **`--force`** (unsafe if a controller is still running).
- On the Pi, **`iccp tui`**, **`iccp dashboard`**, **`iccp live`**, and **`iccp diag`** run **`daemon-reload`** only (they do not **`restart iccp`**). Set **`ICCP_SYSTEMD_SYNC=0`** to skip all automatic **`systemctl`** calls from the CLI. See README (Commissioning → CLI vs systemd).

If shunt current **drops to near zero** after stopping the service, the earlier tens-of-mA reading was **another process driving PWM**, not weak FETs.

## 2. DMM checks (power limited, one channel at a time)

Use your normal **E-stop / lab limits**. Reference design is **N-channel** MOSFETs with gates on BCM pins from `config.settings.PWM_GPIO_PINS`.

**Example logic-level power MOSFET (optional BOM reference):** Vishay **IRLZ44** — key limits, **3.3 V vs 4–5 V** RDS(on) caveat, and gate-charge / PWM notes are summarized in [anode-mosfet-irlz44.md](anode-mosfet-irlz44.md) with a link to the official PDF.

- **Gate–source (Vgs):** With controller **stopped** and after **`cleanup()`** (or power-off with gates held low by your hardware), Vgs should be **≈ 0 V** for an enhancement-mode device that is off.
- **Compare** the same measurement while the app reports **0% duty** (soft-PWM) vs after enabling **Phase 1 static gate** (stop + static LOW) if you still suspect the gate.

## 3. INA219 and wiring

TI **INA219** pin roles (shunt differential vs bus measurement from **IN−** to GND), PGA / bus range, and accuracy limits are summarized in [ina219-datasheet-notes.md](ina219-datasheet-notes.md) with a link to the official PDF.

- Confirm **bus** and **shunt** polarities match the breakout datasheet for your wiring.
- Rule out a **second current path** (bench supply, another driver) through the same shunt or cell loop.

## 4. Oscilloscope (optional)

Probe the **GPIO pin** (or gate through your series resistor):

- **Soft-PWM at 0%** — may still show narrow pulses or switching residue depending on RPi.GPIO and layout.
- **Static LOW** (after `stop()` + `GPIO.output(LOW)`) — should be a flat low if the pin is driven.

If static LOW looks clean but shunt current stays high, treat it as **cell chemistry / leakage / wrong branch**, not GPIO timing alone.

## 5. Settings knobs (weaker software guarantees)

Only after hardware and concurrency are ruled out:

- `COMMISSIONING_PHASE1_NATIVE_ABORT_I_MA` / `COMMISSIONING_OC_CONFIRM_I_MA` — relax the “at rest” shunt threshold (weaker native baseline guarantee).
- `COMMISSIONING_PHASE1_STATIC_GATE_LOW` — set **False** only if static mode causes problems on your board (default **True**).

## 6. Commissioning log: which anode is which?

Phase 1 shunt messages list **only anodes that fail** the |I| gate. Any anode not named in that line had **|I| below the threshold** for that check (it was still read).

Firmware uses **0-based indices** internally (`idx 0 … NUM_CHANNELS-1`). Logs and faults use **`Anode N (idx …)`** from `channel_labels.py` (`anode_hw_label` / `anode_label`): **Anode 1 = idx 0**, **Anode 4 = idx 3**, plus GPIO, INA219 address, and TCA9548A port from `config/settings.py`. The web dashboard and TUI use the same **Anode** wording. If you expected a “CH4” line in old output, that was **idx 3** — look for **`Anode 4 (idx 3, …)`**.

## 7. Automated tests vs. the Raspberry Pi

Unit tests run with **`COILSHIELD_SIM=1`**: `enter_static_gate_off` / `leave_static_gate_off` are **no-ops** so CI never touches `RPi.GPIO`. To compare **soft-PWM at 0%** vs **stop + static LOW** on real hardware, use the steps above on the Pi (scope or DMM), or a short throwaway script that toggles between the two and probes the pin.
