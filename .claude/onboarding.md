# Onboarding — Firmware Repo

You're a fresh agent in `coilshield-firmware`. The owner doesn't have time to re-explain. This file gets you operational fast.

## Read In This Order

1. **`claude.md`** at repo root (you've read it if you're here)
2. **This file**
3. **`docs/DECISIONS.md`** — last 10 entries
4. **`coilshield-session-summary.md`** if you need historical context on what's already built
5. **The relevant sub-rule file** for the task at hand

Don't load everything. The repo isn't huge but the schemas, generated files, and tests can blow your context if you read them all.

## What This Codebase Does

Python firmware. Runs on Raspberry Pi 3. Already substantially built and bench-validated:

- **Bench-confirmed working:** control loop, INA219-based current sensing, polarization detection via simulated reference, simulator with 5 anode profiles, SQLite + CSV + JSON logging, Flask dashboard, fault auto-recovery
- **Hardware on the bench:** 4× INA219 at addresses 0x40/0x41/0x44/0x45, 4× IRLZ44N MOSFETs, DS18B20 temp sensor, Ag/AgCl reference electrode (Stonylab 6×65mm with 3M KCl)
- **In progress:** migration from INA219 to INA3221 (replaces 4 boards with 2), ADS1115 for proper reference reading, BLE provisioning, Supabase cloud sync
- **Critical bench result:** 844 mV polarization shift achieved on steel cathode (8.4× the NACE 100 mV minimum). Confirmed the concept works.

## What You Are NOT Allowed to Do Without Asking

- **Disable the polarization safety cutoff.** Period. Even for testing. Use `COILSHIELD_SIM=1` instead.
- **Push code to a Pi connected to a real coil** without explicit owner confirmation. Bench testing only by default.
- **Refactor `control.py`, `sensors.py`, `commissioning.py`, or `logger.py`** — these work and have been bench-tested. Touch them only when there's a concrete bug or feature to add. Don't "modernize" or "clean up."
- **Change schemas.** Schemas come from the monorepo. Modify there, sync here.
- **Add a dependency.** Every package on a Pi is a maintenance burden. Justify before adding.
- **Replace systemd with anything else.** It works.
- **Remove the Flask dashboard.** It's the local debugging surface and the owner uses it.

## What You ARE Expected to Do

- **Read `.claude/safety-and-control.md`** before any change to the control loop or sensors. The cutoff rules are non-negotiable.
- **Test in simulator mode first.** Set `COILSHIELD_SIM=1` and run on your laptop. Catch obvious bugs before pushing to a Pi.
- **Use the existing logger.** Don't add a new one. Don't print().
- **Write unit tests for new logic.** Tests live in `tests/`. Use `pytest`. Mock GPIO/I2C with the simulator infrastructure that already exists.
- **Document any new hardware in `docs/HARDWARE.md`.** Pin assignments, I2C addresses, wiring notes.
- **Update `docs/DECISIONS.md`** for architectural choices.

## The Operating Vocabulary

Specific words mean specific things:

- **The cutoff** — polarization hard cutoff at −1080 mV vs Ag/AgCl. Single most important safety rule.
- **The window** — the aluminum protection range, −969 mV to −1069 mV vs Ag/AgCl. Inside is good. Below (more negative) triggers the cutoff. Above (less negative) means underprotected.
- **Inner loop** — the 0.5-second control cycle. Adjusts PWM duty to hit target current.
- **Outer loop** — the 60-second control cycle. Reads reference electrode, adjusts target current to keep polarization in window.
- **Wet event** — period when condensate is present and a channel is actively protecting. Bracketed by current rising above 0.15 mA and falling below.
- **Probing** — the brief low-duty pulse used to detect re-wetting on dormant channels. 3% duty for 2 seconds every 60 seconds.
- **Coupon test** — small piece of HVAC fin material used as cathode for safe testing. Use this instead of a real coil for risky changes.
- **The bench** — the test rig with simulated electrolyte. Owner has graphite anodes, steel pliers as cathode, condensate samples.
- **The chain** — device → backend → app → command center. End-to-end data path.
- **Real coil** — an actual aluminum-fin/copper-tube HVAC evaporator. NEVER attached to firmware that hasn't passed bench + coupon tests.

## How the Owner Works

- Solo founder. HVAC tech background, engineering instincts, not a software engineer by trade.
- Uses Claude (the planning AI you might be talking to) for architecture and research.
- Uses Cursor for code editing.
- Has SSH access to all bench Pi devices and (eventually) all 10 validation devices.
- Hates being talked down to. Hates over-engineered solutions. Likes simple, working code.
- Gets impatient with restarts. If the conversation says "keep going," resume — don't restart.
- Pushes back on shortcuts that compromise the product. (See: BLE provisioning, where the owner correctly rejected hardcoded WiFi.)

## When You're Genuinely Unsure

The right answer is: "I don't know — let me read `.claude/[file].md` and the recent DECISIONS entries."

Better to wait 30 seconds and ground yourself than spend 30 minutes on wrong work that has to be undone.

## Common First-Day Mistakes Agents Make

- Suggesting `asyncio` for the control loop — the existing synchronous loop works fine for this hardware
- Suggesting FastAPI to replace Flask — Flask is the existing choice, dashboard works
- Adding type hints inconsistently — pick one approach and stick with it
- Forgetting to handle I2C NACK errors (sensors can fail mid-read; return safe defaults, log, don't crash)
- Suggesting a "cleaner architecture" — if it's not broken, leave it
- Trying to make the firmware "platform-independent" — it runs on a Pi, that's it
- Adding cloud-side logic to firmware — keep firmware focused on hardware control
