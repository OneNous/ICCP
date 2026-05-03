# Firmware decisions (hub)

Per [`claude.md`](../claude.md): log **architectural** choices here with date, author/agent, reasoning, and consequences.

## Template

```markdown
### YYYY-MM-DD — Short title

**Decision:** …

**Context:** …

**Consequences:** …
```

### 2026-05-02 — Per-anode INA219 shunt Ω (mixed R100 vs 1 Ω)

**Decision:** Support optional **per-channel** shunt resistance aligned with ``INA219_ADDRESSES``: env ``COILSHIELD_INA219_SHUNT_OHMS_PER_CHANNEL`` as comma-separated Ω (same length as the address list). ``ina219_shunt_ohms_for_channel(ch)`` drives each ``INA219(...)`` construction in ``sensors``, diag snapshots, and sim shunt mV. ``INA219_CURRENT_LSB_MA`` / ``ina219_nominal_current_lsb_ma()`` use the **maximum** mA-per-LSB across channels (coarsest = smallest physical R) for commissioning binary floors and target LSB semantics.

**Context:** Bench units may upgrade anodes one at a time (e.g. A1 already 1 Ω while A2–A4 still use R100 0.1 Ω). A single global ``INA219_SHUNT_OHMS`` mis-calibrates pi-ina219 and can yield ``DeviceRangeError`` or wrong mA on the mismatched rows.

**Consequences:** Operators set either the global shunt env (all rows match) or the per-channel list. After changing shunts on hardware, restart the process so INA objects re-init with the new Ω.

### 2026-05-02 — `COILSHIELD_ADS1115_CHANNEL` (reference not on AIN0)

**Decision:** Optional env **`COILSHIELD_ADS1115_CHANNEL=0..3`** overrides **`ADS1115_CHANNEL`** at import when the Ag/AgCl divider is wired to **AIN1–AIN3** instead of default **AIN0** (single-ended mode only). Documented in **`docs/ina219-i2c-bringup.md`** with restart reminder.

**Context:** Bench message “no AIN0 on the ADS1115” — default firmware was still sampling AIN0 vs GND.

**Consequences:** Differential rigs continue to use **`ADS1115_DIFFERENTIAL`** + mux pair settings; invalid env values fail fast at import.

### 2026-05-02 — INA219 `max_expected_amps` + Phase 1 diag + probe shunt decode

**Decision:** Pass **`max_expected_amps`** into `pi-ina219` `INA219(...)` for anodes (per-channel from **`ina219_max_expected_amps_for_channel`**, derived from **`MAX_MA` / `CHANNEL_MAX_MA`** × headroom unless **`COILSHIELD_INA219_MAX_EXPECTED_AMPS`** is set) and for the optional ref INA path (**`REF_INA219_MAX_EXPECTED_AMPS`**, env **`COILSHIELD_REF_INA219_MAX_EXPECTED_AMPS`**). On Phase 1 off-check failure, log compact **INA219 diag** lines from the existing `read_all_real` **`diag`** snapshot. **`iccp probe`** STEP 2 / continuous / PWM ladder: decode shunt with **`ina219_shunt_ohms_for_channel`** per row when **`--shunt`** matches global `INA219_SHUNT_OHMS` (else uniform **`--shunt`** override). Document triage in **`docs/ina219-i2c-bringup.md`** §6 and link from **`docs/HARDWARE.md`**.

**Context:** Bench `DeviceRangeError` / overflow triage and operator checklist (1 Ω calibration, DMM, harness, optional Vin− bias).

**Consequences:** Operators set env and **restart** after shunt changes; probe matches runtime Ω selection; commissioning transcripts carry register hints without a second `iccp probe` pass.

### 2026-05-02 — ADS1115 differential + ALRT single-shot mux

**Decision:** When ``ADS1115_DIFFERENTIAL`` is True, the ALRT/conversion-ready path in ``reference._read_ads_mv_scaled_once`` must start conversions with the **same differential MUX** as the polled read (``ads1115_start_single_shot_differential`` in ``i2c_bench``). Previously the edge path always called ``ads1115_start_single_shot`` (single-ended ``ADS1115_CHANNEL``), which mis-triggered differential rigs. Init probe uses differential read when the flag is set. Document **AIN1−AIN3** (reference on AIN3) in ``config/settings.py`` comments.

**Context:** TI ADS1115 only supports four differential pairings; “coil − reference” with ref on AIN3 uses mux (1,3) or (2,3).

**Consequences:** Operators rewire per settings; second differential pair is not yet a second telemetry field in ``ReferenceElectrode.read``. Follow-up: ``_read_raw_mv_hw`` (normal ``read()`` path) now honors ``ADS1115_DIFFERENTIAL`` — it previously only single-ended read despite the flag.

### 2026-05-02 — Remove two-step Phase 1 commissioning (1a / 1b)

**Decision:** Commissioning always runs **one** open-circuit ``capture_native`` (``T_RELAX`` median) with MOSFETs off; result is ``native_mv`` and the shift baseline. **Removed** the bench sequence “anodes out → capture (1a) → install → second capture (1b)” and related Enter pause for 1b. ``COMMISSIONING_FIELD_MODE`` now only means **skip the single optional Phase 1 anode Enter pause** (headless / automation). ``COMMISSIONING_GALVANIC_1B_ENABLED`` / ``ICCP_SKIP_GALVANIC_1B`` are **removed** from settings and code paths. ``reference`` still **loads** ``native_oc_anodes_in_mv`` / ``galvanic_offset_mv`` from older ``commissioning.json`` for runtime math and service hints; ``save_native`` continues to clear those keys on re-baseline.

**Context:** Operators wanted a simpler flow aligned with field installs (anodes stay mounted).

**Consequences:** Docs that described the two-phase bench procedure (e.g. ``docs/galvanic-offset-calibration.md``) are historical for old JSON; no second capture from current firmware.

### 2026-05-02 — Phase 1 native capture: commissioning slope gate vs median

**Decision:** Add **`COMMISSIONING_NATIVE_CAPTURE_SLOPE_MV_PER_MIN`** (`float | None`). **`None`** → use **`NATIVE_SLOPE_MV_PER_MIN`** (spec drift gate). **`0`** → **skip** the first-third vs last-third slope check in **`reference.capture_native()`**; still require peak-to-peak ≤ **`COMMISSIONING_NATIVE_CAPTURE_STABILITY_MV`** (or global stability when unset) and return the **median** of all samples over **`T_RELAX`**. Default **`0.0`** so Phase 1 does not loop forever on slow OCP / thermal drift that exceeds **2 mV/min** but does not invalidate the window median. Log **`[reference] capture_native: discard relax window (reason)`** on stderr when a window is discarded (p2p or slope).

**Context:** After loosening peak-to-peak for commissioning, traces still filled **~60 s** then reset to **0 samples** because the **slope** gate (~20 mV drift over ~1 min → tens of mV/min) failed every attempt.

**Consequences:** Stricter benches can set **`None`** to inherit **`NATIVE_SLOPE_MV_PER_MIN`**, or a positive cap (e.g. **15**) instead of full skip.

### 2026-05-02 — Phase 2 commissioning shift: rolling mean vs consecutive “flat” streak

**Decision:** Default **Phase 2 linear** shift completion to a **rolling mean** of the last `COMMISSIONING_SHIFT_CONFIRM_SAMPLES` instant-off shift readings at the current target mA (`COMMISSIONING_SHIFT_CONFIRM_MODE="average"`), with the same mV band as before. Keep legacy **`"streak"`** mode (N consecutive single-sample in-band ticks via `commissioning.CONFIRM_TICKS`) for tests and conservative benches.

**Context:** Noisy condensate / reference noise made “completely stable” shift traces impractical; operators still need the mean shift to sit in the protection band before lock-in.

**Consequences:** Tune `COMMISSIONING_SHIFT_CONFIRM_SAMPLES` (default 5) vs settle time; set mode to `streak` only when reproducing older behavior or very quiet rigs.

### 2026-04-30 — Close remaining `claude.md` gaps (cloud queue, tech API shell, logging policy)

**Decision:** (1) Add a **sidecar** SQLite queue `LOG_DIR/cloud_queue.db` with a background **Supabase flush worker** (default **off** via `COILSHIELD_CLOUD_SYNC=0`), enqueueing JSON snapshots after each successful `latest.json` write—never blocking the control loop. (2) Register a Flask **Blueprint** [`src/tech_api.py`](../src/tech_api.py) under `/tech` when `COILSHIELD_TECH_API=1`, with unauthenticated `GET /tech/info` and HMAC-gated `GET /tech/status` using `COILSHIELD_TECH_BOND_KEY` (hex) until BLE bond storage exists. (3) Document **stdout vs structured logger** policy; route JSONL thermal notices through `cli_events.emit` where trivial. (4) Add `codegen/gen_types.py` + optional CI drift check.

**Context:** `.claude/cloud-sync.md` and `.claude/tech-api.md` describe target behavior; validation phase requires defaults **off** and no control-path coupling.

**Consequences:** Optional `pip install -e ".[supabase]"` on devices that upload; Pi must set env before enabling sync. Tech app must set bond key on bench. See [`SECURITY.md`](../SECURITY.md) for key rotation if history ever contained secrets.

### 2026-04-30 — Credential exposure and git history

**Decision:** Treat any file that contained **private keys** (e.g. SSH `pi` / `pi.pub` that were once tracked) as **burned** until rotated; prefer **rotation + filter-repo** over “delete file” alone.

**Context:** Removing files from `main` does not remove blobs from clones or forks.

**Consequences:** Owners rotate keys; follow [`SECURITY.md`](SECURITY.md); no secrets in committed `.env`.

### 2026-04-29 — Repo layout aligned to `claude.md` + optional Supabase

**Decision:** Move all application Python under `src/` (with `pyproject.toml` `package-dir`), keep `config/` at repo root, add `.claude/` task rules, `systemd/coilshield.service`, `schemas/` + `schemas/README.md`, doc hubs (`docs/ARCHITECTURE.md`, etc.), and optional Supabase wiring (`src/cloud_sync.py`, `iccp supabase-ping`, `.env.example`). Canonical agent hub is root `claude.md`.

**Context:** `claude.md` describes `coilshield-firmware` layout and validation-phase constraints; this ICCP repo is the same product surface with a different package name (`coilshield-iccp`).

**Consequences:** Tests and CLI prepend `src/` on `sys.path`; docs links use `../src/*.py`; Pi installs use `pip install -e .` as before. Supabase remains **reporting-only**—no control-path dependency (rules 3 and 5). Duplicate `files/*.md` mirror removed in favor of `.claude/` + root `claude.md` only.

## Related logs elsewhere

- Cross-cutting product / IoT ADRs: [iot-dual-system/adrs/](iot-dual-system/adrs/)
- Field and session notes (historical): [coilshield-field-session-notes.md](coilshield-field-session-notes.md)
- Session summary pointer file: [coilshield-session-summary.md](../coilshield-session-summary.md)

*(Add new dated entries after the Template block and before “Related logs”, newest first.)*
