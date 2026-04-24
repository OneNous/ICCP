# ICCP command-line reference

After installing the package from the repo root (`pip install -e .`), you get **one** console script: **`iccp`**. Every subcommand has exactly one canonical spelling — no dash-prefixed or alias variants.

Every subcommand changes the working directory to the project root and applies `--log-dir` the same way (see `config/argv_log_dir.py`).

Use **`iccp --help`** (or **`iccp -h`**) for the built-in short summary.

---

## `iccp start`

**What it does:** Runs the full ICCP controller by driving `main.main()` with **`--real --verbose --skip-commission`** plus any extra arguments you pass.

**When to use it:** Foreground controller on a bench or Pi when you want to run from a shell (not via systemd), or when debugging with extra controller flags.

**Notes:**

- **`--force`** (stripped before dispatch): overrides the check that refuses start if the **`iccp`** systemd unit is already **`active`** (unsafe if two processes really own PWM/I2C).
- **`--sim`**: simulated sensors (passed through to `main.main()`).
- **`--log-dir PATH`**: same telemetry directory as the dashboard; use an absolute path.
- On a Pi, **`ICCP_SYSTEMD_SYNC`** (default on) triggers **`sudo systemctl daemon-reload`** only — **no** `restart`, so you do not bounce the service before foreground start.
- Anode **PWM** behavior is configured in `config/settings.py` (default **`SHARED_RETURN_PWM` = False**: independent duty per gate; set **`True`** for one shared duty on all MOSFET gates; see [hardware-shared-anode-bank.md](hardware-shared-anode-bank.md)).
- **Subset of anodes:** `--channels 0,2` or `--channel 0` (0-based), or `--anodes 1,3` or **`--anode 1`** (1-based; **singular** = a single anode, e.g. only the first cell). Others stay at 0% PWM. Incompatible with **`SHARED_RETURN_PWM` = True** (validation error at startup). Or **`COILSHIELD_ACTIVE_CHANNELS=0`**. The INA219 on **idle** anodes is still read: **mA and Ω** may look noisy or “open” on unconnected paths. Only the **active** anode should **REGULATE** and get non-zero PWM; if every row still shows `REGULATE`, the selector did not apply (wrong flag, or all channels active).

---

## `iccp commission`

**What it does:** Runs the full commissioning flow (`commissioning.run()`): writes **`commissioning.json`**, uses real hardware on a Raspberry Pi unless **`--sim`**.

**When to use it:** First bring-up, after replacing the zinc reference, major rewiring, or when you need to re-establish native baseline and per-channel calibration without going through a full controller first-boot path.

**Flags:**

- **`--sim`**: simulator (also used automatically off-Pi).
- **`--force`**: skip the guard that aborts if **`latest.json`** was updated very recently (another controller may still own PWM). Use only when you are sure nothing else is driving the stack.
- **`--native-only`**: run Phase 1 only — re-capture the native baseline via the new `reference.capture_native` primitive (static gate off, rest-current gate, stability + slope gates, median of samples) and persist it to **`commissioning.json`** with a fresh `native_measured_unix` / `native_recapture_due_unix`. Does not touch `commissioned_target_ma`. Per docs/iccp-requirements.md §3.4 / §8.1 Phase 1.
- **Default (interactive):** two **Press Enter** pauses on a TTY — confirm anodes are **out** of the bath before open-circuit native (Phase 1), then **in** before the CP ramp (Phase 2). Skipped in **`--sim`**, when stdin is not a TTY, or with **`--no-anode-prompts`**, or set **`COMMISSIONING_ANODE_PLACEMENT_PROMPTS = False`** in `config.settings`, or env **`ICCP_COMMISSION_NO_ANODE_PROMPTS=1`**.

**Notes:** On Pi, commission stops the **`iccp`** systemd unit first (`stop`, not `restart`) so PWM is not left running by the service. Requires **`RPi.GPIO`** on real hardware.

---

## `iccp probe …`

**What it does:** Runs the hardware probe (`hw_probe.main()`) — I2C scan, INA219 raw reads, ADS1115, DS18B20, optional PWM GPIO walk. No control loop, no commissioning.

**When to use it:** After wiring changes, mux/address changes, or when you see NACKs / wrong readings and want to isolate bus and sensors from the main loop.

**Common flags** (see **`iccp probe --help`**): **`--init`**, **`--ads1115`**, **`--ads1115-only`**, **`--continuous`** / **`--live`** (stream all INA + ADS AIN0..3; use **`--interval SEC`**), **`--skip-pwm`**, etc.

**Notes:** On Pi, systemd runs **`stop`** on the **`iccp`** unit before probe so I2C/PWM are free. **STEP 1** is a flat “idle” I²C address sweep. With **no mux** in `config.settings` (default), that sweep should list all INA and ADS addresses. If a **TCA9548A** is configured (`I2C_MUX_ADDRESS` and `I2C_MUX_CHANNEL_*`), only the mux (often **0x70**) may show on **STEP 1**; **STEP 1b** then selects each configured downstream port and pings the expected INA219 and ADS1115—same model as the controller. A raw `i2cdetect` without per-port select does not see devices behind the mux. Datasheet notes: [tca9548a-datasheet-notes.md](knowledge-base/components/tca9548a-datasheet-notes.md) (mux) · [ina219-datasheet-notes.md](ina219-datasheet-notes.md) (INA219) · [ads1115-datasheet-notes.md](knowledge-base/components/ads1115-datasheet-notes.md) (ADS1115).

---

## `iccp tui`

**What it does:** Launches the Textual terminal UI (`tui.main()`) — live tab from **`latest.json`**, diagnostics, commands, trends from SQLite.

**When to use it:** SSH sessions or any terminal where the web dashboard is awkward; same mental model as the web UI for live data.

**Common options:** **`--poll-interval SEC`**, **`--log-dir PATH`** (must match the controller).

**Notes:** On Pi, **`daemon-reload`** only (no restart).

---

## `iccp dashboard`

**What it does:** Launches the Flask web dashboard (`dashboard.main()`). Reads the same **`latest.json`** and SQLite the controller writes.

**When to use it:** Browser-based live view / history. Open **`http://<pi-ip>:8080`** (default port).

**Common options:** **`--host 0.0.0.0`**, **`--port 8080`**, **`--log-dir PATH`** (must match the controller).

**Notes:** On Pi, **`daemon-reload`** only (no restart — the dashboard is read-only from the controller's perspective).

---

## `iccp live`

**What it does:** Prints one pretty-printed JSON snapshot of **`latest.json`** (resolved path is printed first).

**When to use it:** Quick copy/paste or scriptable read of current telemetry while the controller is running.

**Notes:** Read-only from the controller's perspective; on Pi, systemd sync is **`daemon-reload`** only (no service restart).

---

## `iccp diag [--request]`

**Without `--request`:** Prints **`diagnostic_snapshot.json`** from the log directory if it exists.

**With `--request`:** Touches the diagnostic request file so the **running** controller (when configured) writes a new snapshot (rate-limited).

**When to use it:** Deep field diagnosis when you want the controller's own snapshot bundle instead of only `latest.json`.

**Notes:** Read-only path for display; **`--request`** only touches a trigger file. On Pi, **`daemon-reload`** only (no restart).

---

## `iccp clear-fault`

**What it does:** Creates or truncates the clear-fault file configured in **`config.settings.CLEAR_FAULT_FILE`** (typically under your log/project tree). With **`--channel N`**, writes a small JSON side file at **`config.settings.CLEAR_FAULT_CHANNEL_FILE`** that is consumed by `Controller.update()` on its next tick to clear only channel **N** (0-based: `0..N_CHANNELS-1`) — `polarize_retry_count` and `state_v2` fault state are reset for that channel alone.

**When to use it:** After an overcurrent or other latched fault, when the main loop is running and you want to clear the latch without using the TUI/web "clear fault" action. Use **`--channel N`** to clear a single channel while leaving siblings untouched (per docs/iccp-requirements.md §6.2).

**Flags:**

- **`--channel N`**: clear only channel **N** (0-based). Without this flag, all channels are cleared.

**Notes:** On Pi, default **`ICCP_SYSTEMD_SYNC`** also **`restart`s** the **`iccp`** service after **`daemon-reload`** so the running controller picks up unit file changes if any.

---

## `iccp version`

**What it does:** Prints **`coilshield-iccp`** version from installed package metadata.

**When to use it:** Confirm which build is on the Pi or in CI.

**Notes:** On Pi, default sync includes **`daemon-reload`** + **`restart iccp`**.

---

## `iccp --help` · `iccp -h`

**What it does:** Prints the static help text from **`iccp_cli._print_help()`**.

**When to use it:** Quick reminder of subcommands and Pi/systemd behavior without opening this doc.

---

## Raspberry Pi: systemd auto-sync (`ICCP_SYSTEMD_SYNC`)

On a Raspberry Pi, recognized **`iccp`** subcommands run **`sudo systemctl daemon-reload`** by default (unless **`ICCP_SYSTEMD_SYNC=0`**).

| Subcommand | After `daemon-reload` |
|----------|------------------------|
| **`start`** | No further `systemctl` (no `restart`) |
| **`commission`**, **`probe`** | `systemctl stop <unit>` |
| **`tui`**, **`dashboard`**, **`live`**, **`diag`** | No further `systemctl` (read-only; no `restart`) |
| **`version`**, **`clear-fault`** | `systemctl restart <unit>` |

Override unit name with **`ICCP_SYSTEMD_UNIT`** (default **`iccp`**). Disable all of this with **`ICCP_SYSTEMD_SYNC=0`** (CI, laptops, or no passwordless sudo).

More context: README (Commissioning → **CLI vs systemd**), [mosfet-off-verification.md](mosfet-off-verification.md) §1.

---

## Direct script execution is not supported

`python3 main.py`, `python3 tui.py`, `python3 hw_probe.py`, and `python3 dashboard.py` each print a redirect and exit with status 2. The only supported way to run the project is through `iccp`. The Python modules remain importable — that is how the CLI drives them — but they are not user-facing entry points.

The previous `coilshield-tui` console script has also been removed. Use **`iccp tui`**.

---

## Upgrading an existing Pi install

After pulling this change on a Pi where the old `iccp -start` systemd unit is installed:

```bash
sudo cp deploy/iccp.service /etc/systemd/system/iccp.service
sudo systemctl daemon-reload
sudo systemctl restart iccp
```

Without this step, the unit will fail because `iccp -start` is no longer a recognized subcommand.
