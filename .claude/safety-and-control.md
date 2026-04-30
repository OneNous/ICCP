# Safety & Control Loop

> **The most important sub-rule file in this repo.** Any change to the control loop, the safety cutoff, the reference electrode reading, or the per-channel state machine requires reading this file in full. No exceptions.

## The Safety Law

The polarization hard cutoff at **−1080 mV vs Ag/AgCl** is the single most important rule in the entire system.

**Why:** Aluminum is amphoteric. Push it cathodic past the alkaline etching threshold (Watkins/Davie's −1.1 V vs SCE, equivalent to −1069 mV vs Ag/AgCl 3M KCl) and the protective oxide dissolves. The fins corrode cathodically — fast, irreversible, expensive. The cutoff sits 11 mV inside that boundary as a safety margin. Below this value (more negative), the channel must gate OFF immediately.

**Where it's enforced:** `control.py`, every inner loop iteration (every 0.5 seconds), for every channel, regardless of state.

**What "enforced" means in code:**

```python
# Pseudocode — actual implementation in control.py
if reference_reading_mv < POLARIZATION_HARD_CUTOFF_MV:
    self.gate_off_immediately()
    self.transition_to_fault(reason="POLARIZATION_CUTOFF")
    self.latch_until_manual_clear()
    return  # Skip all other loop logic this cycle
```

**Rules around the cutoff:**

1. The cutoff check runs FIRST in every inner loop iteration, before any other logic.
2. The cutoff cannot be configured at runtime. It's a constant. Changing it requires a code change, code review, and a DECISIONS entry.
3. The cutoff applies in DORMANT, PROBING, PROTECTING, and FAULT states equally. Even a channel that's "off" gets checked because the reference reading reflects the entire bonded cathode, not just one channel.
4. If the reference electrode itself is malfunctioning (NaN reading, communication error, sustained positive readings during confirmed wet conditions), all channels gate OFF. The system fails safe.
5. A cutoff-triggered fault does NOT auto-clear. Manual operator review is required. This is intentional — a cutoff means something went badly wrong, and we need a human to understand why before re-enabling.

## The Two-Loop Architecture

The control system has two nested feedback loops with different time constants:

### Inner Loop — Current Control (every 0.5 seconds)

- Reads INA3221 to get actual current per channel
- Compares to `target_ma` (set by the outer loop)
- Adjusts PWM duty cycle to hit target
- Caps at `MAX_MA` (5.0 mA) regardless
- Writes the new duty cycle to GPIO
- **Always checks the safety cutoff first**

### Outer Loop — Potential Control (every 60 seconds)

- Reads ADS1115 → reference electrode → polarization in mV
- Compares to `PROTECTION_TARGET_MV` (−1019 mV vs Ag/AgCl, the center of the aluminum protection window)
- If reading is too positive (less protected), increases `target_ma`
- If reading is too negative (overprotected), decreases `target_ma`
- Has a deadband of ±30 mV around the target — no adjustment within that range
- Step size per cycle: ±0.05 mA — slow and stable, no oscillation

### Why Two Loops?

Current control alone diverges. The relationship between current density and polarization depends on coil area, electrolyte conductivity, anode condition, and temperature — none of which are constant. Hardcoding a current target is wrong because the right current depends on conditions.

Closing the loop on potential (via the reference electrode) is the only way to actually maintain protection across varying conditions. This matches industrial ICCP practice for pipelines and tanks.

## Per-Channel State Machine

Each of the 4 channels has an independent FSM with these states:

- **DORMANT** — channel is off. No current flowing. Periodically probes to detect re-wetting.
- **PROBING** — brief low-duty pulse (3% duty for 2 seconds) to test if condensate is present.
- **PROTECTING** — wet conditions detected, channel is actively delivering protection current.
- **FAULT** — something went wrong. Channel is off. Requires manual or auto-recovery to clear.

### Transitions

```
DORMANT --[60s elapsed]--> PROBING
PROBING --[current > 0.15 mA observed]--> PROTECTING
PROBING --[current ≤ 0.15 mA]--> DORMANT
PROTECTING --[current ≤ 0.15 mA for 30s]--> DORMANT (channel went dry)
PROTECTING --[current > MAX_MA]--> FAULT (overcurrent)
PROTECTING --[INA3221 read failure]--> FAULT
ANY --[polarization < CUTOFF]--> FAULT (latched, manual clear only)
ANY --[reference electrode failure]--> FAULT (latched)
FAULT --[60s elapsed AND retry count < 3 AND not latched]--> PROBING
FAULT --[manual clear OR retry count exceeded]--> stays FAULT
```

### Auto-Recovery vs Latched Faults

Some faults auto-recover after a wait period:

- Transient overcurrent (one-time spike, channel still healthy after retry)
- Brief sensor read failure (I2C glitch)
- Channel went dry then re-wet

Some faults latch and require manual clearance:

- Polarization cutoff fired (safety event)
- Reference electrode malfunction
- 3 consecutive auto-recovery failures
- Sustained overcurrent

The distinction matters because auto-recovery handles real-world flakiness, while latched faults require human attention because they indicate something genuinely wrong.

## Configuration Values (settings.py)

These are the calibrated values. Don't change without bench testing and a DECISIONS entry.

```python
# Channel count
NUM_CHANNELS = 4

# I2C addresses
INA3221_ADDRESSES = [0x40, 0x41]  # Two boards, channels 0-2 and 3 (only 4 used)
ADS1115_ADDRESS = 0x48

# Current targets and limits
TARGET_MA_DEFAULT = 0.5     # Outer loop adjusts from this baseline
MAX_MA = 5.0                # Hard cap regardless of outer loop request
WET_DETECT_THRESHOLD_MA = 0.15

# Polarization (vs Ag/AgCl 3M KCl)
PROTECTION_TARGET_MV = -1019         # Center of aluminum window
PROTECTION_WINDOW_MIN_MV = -969      # Pitting threshold (less negative = unprotected)
PROTECTION_WINDOW_MAX_MV = -1069     # Alkaline etching threshold (more negative = damage)
POLARIZATION_HARD_CUTOFF_MV = -1080  # 11 mV margin past damage threshold — gates channel off
POLARIZATION_FLOOR_WARNING_MV = -900 # Warn if above this for >5 min

# Loop timing
INNER_LOOP_INTERVAL_S = 0.5
OUTER_LOOP_INTERVAL_S = 60.0
OUTER_LOOP_DEADBAND_MV = 30
OUTER_LOOP_STEP_MA = 0.05

# Probing
PROBE_INTERVAL_S = 60.0
PROBE_DUTY = 0.03
PROBE_DURATION_S = 2.0

# Fault recovery
FAULT_AUTO_CLEAR = True
FAULT_RETRY_INTERVAL_S = 60.0
FAULT_RETRY_MAX = 3
```

## The Reference Electrode

The Ag/AgCl saturated KCl reference electrode (Stonylab 6×65mm) is the system's eyes. If it lies, the safety cutoff is meaningless.

Health checks performed by `reference.py`:

1. **Read consistency:** A spike of >100 mV in 1 second is suspicious. Average over 1 second of samples.
2. **Range check:** Readings outside −1300 mV to +500 mV indicate the electrode is broken or disconnected.
3. **Sign sanity:** During confirmed wet conditions (any channel reading current >0.15 mA), the polarization should be in negative territory. Sustained positive readings during wet = electrode failure.
4. **Drift detection:** Compare to a backup electrode monthly; >50 mV drift triggers a service alert.

If any health check fails, all channels gate OFF and `REFERENCE_FAULT` is logged. This is a latched fault.

## Wet Detection Without a Wet Sensor

There is no separate "is it wet?" sensor. Instead, current draw is the wetness indicator:

- Channel pulses at 3% duty (PROBING)
- INA3221 reads the resulting current
- If current > 0.15 mA, condensate is present (electrolyte path closed). Transition to PROTECTING.
- If current ≤ 0.15 mA, channel stays DORMANT.

This is elegant: every channel self-detects its own wet state. Bottom-corner channels (where condensate pools first due to gravity) wake up before center channels. The system adapts to whatever wetting pattern the coil exhibits without configuration.

## Anti-Patterns That Are Banned

These have all been considered and rejected. Don't reintroduce them:

- **Hardcoded current targets per "installation profile"** — abandoned because the outer loop self-regulates better than any profile
- **Master wet sensor** — abandoned because per-channel current detection is sufficient and adds no hardware
- **Skipping the outer loop "for simplicity"** — abandoned because current-only control diverges in real conditions
- **Disabling the cutoff during commissioning** — banned because commissioning on a real coil is exactly when overprotection risk is highest
- **Software bypass for testing** — use `COILSHIELD_SIM=1` simulator instead
- **Using a different reference electrode "to save cost"** — Ag/AgCl is the calibrated choice; zinc was the previous design and had drift issues
- **Adjusting calibration values without bench validation** — every config change in `settings.py` requires a bench test pass

## Testing Strategy

Three levels, in increasing risk order:

### 1. Simulator Mode (Safe)

```bash
COILSHIELD_SIM=1 python3 src/main.py --verbose
```

Uses simulated sensors with 5 anode profiles (clean, fouled, broken, etc.). Runs the full control logic. No hardware risk. Default for development.

### 2. Bench Hardware (Limited Risk)

Real Pi, real INA3221, real MOSFETs, real Ag/AgCl. But cathode is a steel pliers in condensate, not an aluminum coil.

- Steel can tolerate −1.7 V vs CSE without damage. Coupon-style failures (overprotection) won't manifest because steel is robust.
- Useful for verifying I2C bus, GPIO, sensor readings, and overall system function.
- NOT useful for verifying safety cutoff effectiveness on aluminum.

### 3. Coupon Test (Moderate Risk)

Real Pi + real hardware + small piece of aluminum HVAC fin material as cathode. Simulated condensate.

- This is the canonical test for verifying the safety cutoff works on aluminum.
- 7-day continuous run at production settings.
- Inspect fin surface afterward for pitting or alkaline etching.
- Required before any real-coil install.

### 4. Real Coil (High Risk)

Don't deploy to a real coil until coupon tests pass cleanly. See `.claude/deployment.md` for the deployment gate.

## Common Cursor Pitfalls in Control Code

When Cursor produces control loop code, watch for:

- Suggesting `asyncio` patterns for what is fundamentally a synchronous polling loop
- Adding "smart" features that try to predict optimal current (the outer loop already does this — don't second-guess it)
- Removing the safety cutoff check "because it's redundant" (it's not)
- Combining the inner and outer loops "for elegance" (they're separate for good reasons)
- Suggesting PID tuning libraries (the simple step-based outer loop is intentional and works)
- Catching exceptions broadly and continuing (a sensor read failure should fault the channel, not silently skip)

## When the Safety Cutoff Fires in Production

If the cutoff fires on a real customer coil, the response is:

1. Don't auto-clear. The channel stays latched in FAULT.
2. Owner is alerted via Supabase event → email.
3. Owner reviews data: was it a transient sensor glitch or a real overprotection event?
4. If transient, manually clear the fault (touch a flag file via SSH or use a dedicated tech-app endpoint — TBD).
5. If real, investigate why the outer loop overshot. Likely root causes: sudden change in coil area (e.g., thermal expansion exposing more wetted surface), reference electrode drift, calibration error.
6. Document in `docs/DECISIONS.md` what happened and what was changed in response.

The cutoff firing once is informative. The cutoff firing repeatedly is a sign that something fundamental is wrong with the calibration for that specific install — the outer loop's step size or deadband may need adjustment for that customer's conditions.
