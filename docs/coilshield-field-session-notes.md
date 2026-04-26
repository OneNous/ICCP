---
title: CoilShield bench + install field notes (2026-04)
description: Practical wiring/measurement conventions and commissioning notes distilled from bench sessions. Not a marketing spec.
topics: [cathodic-protection, commissioning, wiring, ads1115, ina219, install, field-notes]
---

## Scope

This document captures **bench-proven** and **install-actionable** notes for the CoilShield ICCP rig. It is intentionally narrow:

- **Included**: wiring, measurement conventions, commissioning gotchas, and the “bond outside electrolyte” constraint.
- **Not included**: pricing/ROI, market sizing, patent claims, or fleet/app architecture.

## System mental model (one channel)

Current path (per channel):

1. **+5 V rail** → INA219 **Vin+** → shunt → INA219 **Vin−**
2. INA219 **Vin−** → MOSFET **drain**
3. MOSFET **source** → anode lead → electrolyte film (condensate) → cathode metal surface
4. Cathode metal → **bond point on dry metal outside electrolyte** → controller **GND**

The INA219 current is the control feedback. The reference electrode is used for a slow “polarization shift” confirmation (instant-off sampling).

## Critical installation constraint: bond wire must be outside electrolyte

If the cathode bond/return is submerged in the same electrolyte as the anodes, current can return through **bulk electrolyte** directly to the bond wire and **bypass the cathode metal surface**, so you do not get meaningful polarization of the structure.

**Rule:** clamp the bond wire to a **dry metal point electrically continuous with the coil** but **outside** the electrolyte zone. For HVAC installs, a practical candidate is the **liquid line outside the cabinet**, which is continuous through the refrigerant circuit.

## Reference electrode measurement convention (and why the sign looks “inverted” vs some books)

CoilShield’s ADS1115 wiring is:

- ADS1115 **AIN0**: Ag/AgCl signal (reference electrode)
- ADS1115 **GND**: structure/return (bond wire / plier clamp to the structure)

So the scalar reported as `ref_raw_mv` is approximately:

\[
ref\_raw\_mv \approx V_{ref} - V_{structure}
\]

Under cathodic protection, the structure becomes **more negative**, so \(V_{structure}\) decreases and **`ref_raw_mv` increases**. That is expected with this wiring.

**Firmware convention:** polarization shift is stored as:

\[
ref\_shift\_mv = ref\_raw\_mv - baseline\_mv
\]

So **positive `ref_shift_mv` means “more protected”** for this hardware. See:

- `ReferenceElectrode.shift_mv()` in [`/Users/mhm/Desktop/ICCP/reference.py`](/Users/mhm/Desktop/ICCP/reference.py)
- instant-off shift in [`/Users/mhm/Desktop/ICCP/commissioning.py`](/Users/mhm/Desktop/ICCP/commissioning.py)

If you are reading a CP manual that defines “structure-to-electrolyte potential” with the **meter leads swapped**, the *displayed sign* may be opposite. What matters operationally here is: **shift is computed as raw minus baseline**, and it should trend **positive** when CP is working.

## ADS1115 reference wiring (single-ended)

Recommended “get it working” wiring (single-ended):

- ADS1115 **AIN0** → Ag/AgCl signal wire
- ADS1115 **AIN1** → **not connected**
- ADS1115 **GND** → controller GND rail

In settings:

- `ADS1115_CHANNEL = 0`
- `ADS1115_DIFFERENTIAL = False`

Avoid differential mode unless AIN− is guaranteed to be tied to the intended negative sense node (a floating AIN− will pick up stray voltage and produce meaningless deltas).

## Commissioning gotchas

- **Phase 1a (true native)**: remove anodes from the electrolyte. Submerged inert anodes can drive non-trivial galvanic current even with MOSFET gates off, corrupting the baseline.
- **Instant-off sampling**: after cutting PWM, ignore the immediate transient and sample in a short window where IR drop has collapsed and the decay curve is stable. The code implements an inflection finder over a burst/window.

## PWM floors (operator expectations)

There are two “floors” worth distinguishing:

- `PWM_MIN_DUTY`: minimum non-zero gate duty once the controller decides to drive.
- `DUTY_PROBE`: the REGULATE “probe floor” used to avoid deadlocking at 0% when current is below target.

If you want a 1% minimum behavior, set `DUTY_PROBE = 1.0` (and keep `PWM_MIN_DUTY = 1`). See [`/Users/mhm/Desktop/ICCP/config/settings.py`](/Users/mhm/Desktop/ICCP/config/settings.py).

