# Verifying MOSFETs are fully off (high shunt at “0% PWM”)

Firmware normally turns channels “off” with RPi.GPIO **soft-PWM** at **0% duty** (`ChangeDutyCycle(0)`). Shutdown uses **`pwm.stop()`** plus **`GPIO.output(LOW)`**, which can behave differently on the gate than soft-PWM at 0. Commissioning Phase 1 can optionally use **static LOW** (see `COMMISSIONING_PHASE1_STATIC_GATE_LOW` in `config/settings.py`).

## 1. Single process owns PWM

Only one of **`iccp -start`**, **`main.py`**, or **`iccp commission`** should control the Pi’s PWM GPIO at a time.

- Stop the service: `sudo systemctl stop iccp`
- Confirm nothing else: `ps aux | grep -E 'iccp|main.py'`
- If `latest.json` was updated seconds ago, `iccp commission` aborts unless you pass **`--force`** (unsafe if a controller is still running).

If shunt current **drops to near zero** after stopping the service, the earlier tens-of-mA reading was **another process driving PWM**, not weak FETs.

## 2. DMM checks (power limited, one channel at a time)

Use your normal **E-stop / lab limits**. Reference design is **N-channel** MOSFETs with gates on BCM pins from `config.settings.PWM_GPIO_PINS`.

- **Gate–source (Vgs):** With controller **stopped** and after **`cleanup()`** (or power-off with gates held low by your hardware), Vgs should be **≈ 0 V** for an enhancement-mode device that is off.
- **Compare** the same measurement while the app reports **0% duty** (soft-PWM) vs after enabling **Phase 1 static gate** (stop + static LOW) if you still suspect the gate.

## 3. INA219 and wiring

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

## 6. Automated tests vs. the Raspberry Pi

Unit tests run with **`COILSHIELD_SIM=1`**: `enter_static_gate_off` / `leave_static_gate_off` are **no-ops** so CI never touches `RPi.GPIO`. To compare **soft-PWM at 0%** vs **stop + static LOW** on real hardware, use the steps above on the Pi (scope or DMM), or a short throwaway script that toggles between the two and probes the pin.
