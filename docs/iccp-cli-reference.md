# ICCP command-line reference

After installing the package from the repo root (`pip install -e .`), you get console scripts **`iccp`** and **`coilshield-tui`**. All **`iccp …`** commands change working directory to the project root and apply `--log-dir` the same way as `main.py` (see `config/argv_log_dir.py`).

Use **`iccp --help`** for the built-in short summary.

---

## `iccp -start` · `iccp --start` · `iccp start`

**What it does:** Runs the full ICCP controller by delegating to `main.py` with **`--real --verbose --skip-commission`** plus any extra arguments you pass.

**When to use it:** Foreground controller on a bench or Pi when you want to run from a shell (not via systemd), or when debugging with extra `main.py` flags.

**Notes:**

- **`--force`** (stripped before dispatch): overrides the check that refuses start if the **`iccp`** systemd unit is already **active** (unsafe if two processes really own PWM/I2C).
- **`--sim`**: simulated sensors (if passed through to `main.py`).
- **`--log-dir PATH`**: same telemetry directory as the dashboard; use an absolute path.
- On a Pi, **`ICCP_SYSTEMD_SYNC`** (default on) triggers **`sudo systemctl daemon-reload`** only — **no** `restart`, so you do not bounce the service before foreground start.

---

## `iccp commission` · `iccp --commission` · `iccp -commission`

**What it does:** Runs the full commissioning flow (`commissioning.run()`): writes **`commissioning.json`**, uses real hardware on a Raspberry Pi unless **`--sim`**.

**When to use it:** First bring-up, after replacing the zinc reference, major rewiring, or when you need to re-establish native baseline and per-channel calibration without going through a full `main.py` first-boot path.

**Flags:**

- **`--sim`**: simulator (also used automatically off-Pi).
- **`--force`**: skip the guard that aborts if **`latest.json`** was updated very recently (another controller may still own PWM). Use only when you are sure nothing else is driving the stack.

**Notes:** On Pi, commission stops the **`iccp`** systemd unit first (`stop`, not `restart`) so PWM is not left running by the service. Requires **`RPi.GPIO`** on real hardware.

---

## `iccp probe …`

**What it does:** Runs **`hw_probe.py`** with the same arguments — I2C scan, INA219 raw reads, ADS1115, DS18B20, optional PWM GPIO walk. No control loop, no commissioning.

**When to use it:** After wiring changes, mux/address changes, or when you see NACKs / wrong readings and want to isolate bus and sensors from the main loop.

**Common flags** (see **`iccp probe --help`** / `hw_probe.py` docstring): **`--init`**, **`--ads1115`**, **`--ads1115-only`**, **`--continuous`**, **`--skip-pwm`**, etc.

**Notes:** On Pi, systemd runs **`stop`** on the **`iccp`** unit before probe so I2C/PWM are free.

---

## `iccp clear-fault` · `iccp clear_fault` · `iccp clear-faults`

**What it does:** Creates or truncates the clear-fault file configured in **`config.settings.CLEAR_FAULT_FILE`** (typically under your log/project tree).

**When to use it:** After an overcurrent or other latched fault, when the main loop is running and you want to clear the latch without using the TUI/web “clear fault” action.

**Notes:** On Pi, default **`ICCP_SYSTEMD_SYNC`** also **`restart`s** the **`iccp`** service after **`daemon-reload`** so the running controller picks up unit file changes if any (same class as `version`).

---

## `iccp version` · `iccp -V` · `iccp --version`

**What it does:** Prints **`coilshield-iccp`** version from installed package metadata.

**When to use it:** Confirm which build is on the Pi or in CI.

**Notes:** On Pi, default sync includes **`daemon-reload`** + **`restart iccp`**.

---

## `iccp live`

**What it does:** Prints one pretty-printed JSON snapshot of **`latest.json`** (resolved path is printed first).

**When to use it:** Quick copy/paste or scriptable read of current telemetry while the controller is running.

**Notes:** Read-only from the controller’s perspective; on Pi, systemd sync is **`daemon-reload`** only (no service restart).

---

## `iccp diag` · `iccp diag --request`

**Without `--request`:** Prints **`diagnostic_snapshot.json`** from the log directory if it exists.

**With `--request`:** Touches the diagnostic request file so the **running** main loop (when configured) writes a new snapshot (rate-limited).

**When to use it:** Deep field diagnosis when you want the controller’s own snapshot bundle instead of only `latest.json`.

**Notes:** Read-only path for display; **`--request`** only touches a trigger file. On Pi, **`daemon-reload`** only (no restart).

---

## `iccp tui` · `iccp watch` · `iccp monitor`

**What it does:** Launches the Textual terminal UI (`tui.py`) — live tab from **`latest.json`**, diagnostics, commands, trends from SQLite. **`watch`** and **`monitor`** are aliases for **`tui`**.

**When to use it:** SSH sessions or any terminal where the web dashboard is awkward; same mental model as the web UI for live data.

**Common options:** **`--poll-interval SEC`**, **`--log-dir PATH`** (must match the controller).

**Notes:** On Pi, **`daemon-reload`** only (no restart). Same app as **`coilshield-tui`** (see below).

---

## `coilshield-tui`

**What it does:** Same as **`iccp tui`** — entry point is **`tui:main`** with **`sys.argv`** as passed (no leading `iccp tui` token).

**When to use it:** Habit or scripts that call **`coilshield-tui`** directly. Prefer **`iccp tui`** if you want consistent **`iccp`** help and systemd sync behavior on the Pi.

**Note:** **`coilshield-tui`** is not listed inside **`iccp --help`**; it is a sibling script from the same package.

---

## `iccp --help` · `iccp help` · `iccp -h`

**What it does:** Prints the static help text from **`iccp_cli._print_help()`**.

**When to use it:** Quick reminder of subcommands and Pi/systemd behavior without opening this doc.

---

## Raspberry Pi: systemd auto-sync (`ICCP_SYSTEMD_SYNC`)

On a Raspberry Pi, recognized **`iccp`** subcommands run **`sudo systemctl daemon-reload`** by default (unless **`ICCP_SYSTEMD_SYNC=0`**).

| Commands | After `daemon-reload` |
|----------|------------------------|
| **`-start` / `start`** | No further `systemctl` (no `restart`) |
| **`commission`**, **`probe`** | `systemctl stop <unit>` |
| **`tui`**, **`watch`**, **`monitor`**, **`live`**, **`diag`** | No further `systemctl` (read-only; no `restart`) |
| **`version`**, **`clear-fault`**, … | `systemctl restart <unit>` |

Override unit name with **`ICCP_SYSTEMD_UNIT`** (default **`iccp`**). Disable all of this with **`ICCP_SYSTEMD_SYNC=0`** (CI, laptops, or no passwordless sudo).

More context: README (Commissioning → **CLI vs systemd**), [mosfet-off-verification.md](mosfet-off-verification.md) §1.

---

## Direct `main.py` / `hw_probe.py` (optional)

You can still run **`python3 main.py …`** or **`python3 hw_probe.py …`** from the repo root. **`iccp -start`** and **`iccp probe`** are the supported wrappers that set cwd, log dir argv handling, and (on Pi) systemd sync.
