# Two-phase open-circuit calibration (true native + galvanic offset)

CoilShield can commission a **true** metal/electrolyte baseline with anodes **out** of the bath (Phase 1a), then a second open-circuit capture with anodes **in** the bath, MOSFETs **off** (Phase 1b), using the same relax window and stability rules as Phase 1a (`T_RELAX`, `NATIVE_STABILITY_*`).

## Stored fields (`commissioning.json`)

| Key | Meaning |
| --- | ------- |
| `native_mv` | Phase 1a — OCP with anodes **removed** (true native / corrosion potential of the fin path) |
| `native_oc_anodes_in_mv` | Phase 1b — OCP with anodes **submerged**, 0% PWM |
| `galvanic_offset_mv` | `native_mv − native_oc_anodes_in_mv` (positive in the typical “depression” case) |
| `galvanic_offset_baseline_mv` | Set on the **first** full 1a+1b pair at this site — used to trend anode **health** (optional service flag) |
| `galvanic_offset_service_recommended` | `true` if current `galvanic_offset_mv` &lt; `GALVANIC_OFFSET_SERVICE_FRACTION` × `galvanic_offset_baseline_mv` at re-commissioning |

**Shift / outer loop:** `baseline_mv_for_shift` is **`native_oc_anodes_in_mv`** when Phase 1b was run, otherwise `native_mv`. Polarization **shift** = that baseline − **instant-off** (or on-tick) reference, so the loop measures protection **from the in-situ open-circuit with anodes present**, not from a bench-only 1a number that ignores galvanic influence.

**Total vs additional shift:** `TARGET_SHIFT_MV` is the **total** desired polarization from **true native (Phase 1a)**. With 1b, `galvanic_offset_mv = native(1a) − native(1b)` is already part of that total, so the **additional** shift required from the 1b baseline is `effective_shift_target_mv = max(0, TARGET_SHIFT_MV − galvanic_offset_mv)` (and similarly for `MAX_SHIFT_MV` via `effective_max_shift_mv`). Commissioning and the shift FSM use these effective values so you do not double-count the galvanic depolarization.

**Telemetry** (`latest.json` system block): `native_mv` (shift baseline for display consistency), `native_true_anodes_out_mv` (1a), optional galvanic fields when commissioned.

## Operator flow

1. **Phase 1a** — Remove anode assemblies, Enter, wait `T_RELAX` → `native_mv`.  
2. **Install anodes** (prompt), Enter.  
3. **Phase 1b** — Same capture primitive, 0% duty → `native_oc_anodes_in_mv`, `galvanic_offset_mv` computed.  
4. **Phase 2+** — Current ramp and lock as today.

**Skip 1b (legacy / bench):** `ICCP_SKIP_GALVANIC_1B=1` or `COMMISSIONING_GALVANIC_1B_ENABLED = False` in `config/settings.py`.

**Service threshold:** `GALVANIC_OFFSET_SERVICE_FRACTION` (default 0.2) — re-commissioning compares the new `galvanic_offset_mv` to the first-install `galvanic_offset_baseline_mv`; a large fall flags `galvanic_offset_service_recommended` and stderr guidance (trending toward bare titanium / passive behavior is a **model**, not a guaranteed failure mode — validate on your MMO anode and electrolyte program).

## Product and IP

Automatic logging of (1) true native, (2) in-bath OCP, and (3) their difference — plus trending against a first-install reference — is intended as a **field-install health signal** and optional remote-operations hook (`galvanic_offset_*` in `latest.json` / `commissioning.json`).

Legal claims are outside this repository; coordinate with counsel for patent filings, trade-secret marking, and customer-facing “health %” / warranty language.

## Shunt mA: A1..A4 vs “my first anode”

Commissioning lines list **A1, A2, …** = **firmware channel index 0, 1, …** = the order in `INA219_ADDRESSES` and `PWM_GPIO_PINS` (see `anode_hw_label` / `channel_labels.py`). If you install a single anode on the **third** harness row, |I| shows on **A3**, not A1. Values near **0.1 mA** on one channel and ~0 on others are often shunt **offset or noise** until real CP current (mA range) appears — confirm the anode/return is on the row you think matches A1 in your build.

**Control set vs harness:** if only one anode is physically installed, pass **`iccp commission --anode 1`** (for A1) or set **`COILSHIELD_ACTIVE_CHANNELS=0`** (0-based) so the firmware’s “active” anode list is only that row. Otherwise, by default, every logical row is in the control path and you will see the same duty and per-row shunt columns on A1..A# — not “only the wired leg.” (Requires per-channel PWM; see `SHARED_RETURN_PWM` and `docs/hardware-shared-anode-bank.md` if you use a shared return bank.)

## See also

- [reference-electrode-placement.md](reference-electrode-placement.md) — reference tip placement vs anode return  
- [iccp-vs-coilshield.md](iccp-vs-coilshield.md) — what “shift” means in firmware vs classical ICCP surveys
