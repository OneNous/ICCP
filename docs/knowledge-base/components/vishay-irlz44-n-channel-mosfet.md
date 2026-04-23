---
title: Vishay Siliconix IRLZ44 (N-channel logic-level power MOSFET)
description: Full datasheet parameter transcription for document 91328 — TO-220AB, 60 V, logic-level gate.
topics: [IRLZ44, MOSFET, Vishay, gate-drive, TO-220, anode-switch]
vendor: Vishay Siliconix
document_number: "91328"
pdf: "https://www.vishay.com/docs/91328/irlz44.pdf"
---

# IRLZ44 — knowledge base entry

**Source:** Vishay Siliconix **IRLZ44**, Document Number **91328**, Rev. **D**, 25-Oct-2021 (confirm on [vishay.com](https://www.vishay.com/docs/91328/irlz44.pdf)).

**Device:** Single **N-channel** power MOSFET, **TO-220AB** (G, D, S).

## Features (datasheet list)

- Dynamic dV/dt rating  
- Logic-level gate drive  
- RDS(on) specified at **VGS = 4 V** and **5 V**  
- **175 °C** operating junction temperature  
- Fast switching  
- Ease of paralleling  
- Simple drive requirements  
- Material categorization / RoHS notes per Vishay doc **99912** (see PDF)

## Product summary

| Parameter | Value |
|-----------|--------|
| VDS | **60 V** |
| RDS(on) max @ VGS = 5.0 V | **0.028 Ω** |
| Qg max | **66 nC** |
| Configuration | Single N-channel |

## Ordering information

| Variant | Package |
|---------|---------|
| IRLZ44PbF | TO-220AB, lead-free |
| IRLZ44PbF-BE3 | Lead-free and halogen-free |

## Absolute maximum ratings (TC = 25 °C unless noted)

| Parameter | Symbol | Limit | Unit |
|-----------|--------|-------|------|
| Drain-source voltage | VDS | 60 | V |
| Gate-source voltage | VGS | ±10 | V |
| Continuous drain current (VGS @ 5 V), TC = 25 °C | ID | 50 | A |
| Continuous drain current (VGS @ 5 V), TC = 100 °C | ID | 36 | A |
| Pulsed drain current (note a) | IDM | 200 | A |
| Linear derating factor | — | 1.0 | W/°C |
| Single pulse avalanche energy (note b) | EAS | 400 | mJ |
| Maximum power dissipation, TC = 25 °C | PD | 150 | W |
| Peak diode recovery dV/dt (note c) | dV/dt | 4.5 | V/ns |
| Operating junction and storage temperature | TJ, Tstg | −55 to +175 | °C |
| Soldering recommendations peak temp (note d), 10 s | — | 300 | °C |
| Mounting torque 6-32 or M3 | — | 10 lbf·in / 1.1 N·m | — |

**Notes (lettered as in datasheet — see PDF for figures and pulse definitions):**

- **a:** Repetitive rating; pulse width limited by maximum junction temperature (Fig. 11).  
- **b:** VDD = 25 V, starting TJ = 25 °C, L = 179 µH, Rg = 25 Ω, IAS = 51 A (Fig. 12).  
- **c:** ISD ≤ 51 A, dV/dt ≤ 250 A/µs, VDD ≤ VDS, TJ ≤ 175 °C.  
- **d:** 1.6 mm from case.  
- **e:** Current limited by package (die current = 51 A).

## Thermal resistance ratings

| Parameter | Symbol | Typ. | Max. | Unit |
|-----------|--------|------|------|------|
| Junction-to-ambient | RthJA | — | 62 | °C/W |
| Case-to-sink, flat, greased | RthCS | 0.50 | — | °C/W |
| Junction-to-case (drain) | RthJC | — | 1.0 | °C/W |

## Specifications — static (TJ = 25 °C unless noted)

| Parameter | Symbol | Test conditions | Min. | Typ. | Max. | Unit |
|-----------|--------|-----------------|------|------|------|------|
| Drain-source breakdown voltage | V(BR)DSS | VGS = 0, ID = 250 µA | 60 | — | — | V |
| VDS temperature coefficient | ΔV(BR)DSS/TJ | Ref. 25 °C, ID = 1 mA | — | 0.070 | — | V/°C |
| Gate-source threshold voltage | VGS(th) | VDS = VGS, ID = 250 µA | 1.0 | — | 2.0 | V |
| Gate-source leakage | IGSS | VGS = 10 V | — | — | ±100 | nA |
| Zero gate voltage drain current | IDSS | VDS = 60 V, VGS = 0 | — | — | 25 | µA |
| | | VDS = 48 V, VGS = 0, TJ = 150 °C | — | — | 250 | µA |
| Drain-source on-resistance | RDS(on) | VGS = 5.0 V, ID = 31 A (note b) | — | — | 0.028 | Ω |
| | | VGS = 4.0 V, ID = 25 A (note b) | — | — | 0.039 | Ω |
| Forward transconductance | gfs | VDS = 25 V, ID = 31 A (note b) | 23 | — | — | S |

## Specifications — dynamic (TJ = 25 °C unless noted)

| Parameter | Symbol | Test conditions | Typ. | Max. | Unit |
|-----------|--------|-----------------|------|------|------|
| Input capacitance | Ciss | VGS = 0, VDS = 25 V, f = 1 MHz (Fig. 5) | 3300 | — | pF |
| Output capacitance | Coss | same | 1200 | — | pF |
| Reverse transfer capacitance | Crss | same | 200 | — | pF |
| Total gate charge | Qg | VGS = 5.0 V, ID = 51 A, VDS = 48 V (Fig. 6, 13, note b) | — | 66 | nC |
| Gate-source charge | Qgs | — | — | 12 | nC |
| Gate-drain charge | Qgd | — | — | 43 | nC |
| Turn-on delay | td(on) | VDD = 30 V, ID = 51 A, Rg = 4.6 Ω, RD = 0.56 Ω (Fig. 10, note b) | 17 | — | ns |
| Rise time | tr | same | 230 | — | ns |
| Turn-off delay | td(off) | same | 42 | — | ns |
| Fall time | tf | same | 110 | — | ns |
| Internal drain inductance | LD | 6 mm from package to die contact | 4.5 | — | nH |
| Internal source inductance | LS | — | 7.5 | — | nH |

## Drain-source body diode

| Parameter | Symbol | Conditions | Min. | Typ. | Max. | Unit |
|-----------|--------|------------|------|------|------|------|
| Continuous source-drain diode current | IS | MOSFET symbol body diode (note c,e) | — | — | 50 | A |
| Pulsed diode current | ISM | (note a) | — | — | 200 | A |
| Body diode forward voltage | VSD | TJ = 25 °C, IS = 51 A, VGS = 0 (note b) | — | — | 2.5 | V |
| Body diode reverse recovery time | trr | TJ = 25 °C, IF = 51 A, dI/dt = 100 A/µs (note b) | — | 130 | 180 | ns |
| Body diode reverse recovery charge | Qrr | — | 0.84 | 1.3 | µC |

## Typical characteristics (figures on PDF)

Figures **1–13** in the PDF include: output characteristics (25 °C and 175 °C), transfer characteristics, normalized RDS(on) vs temperature, capacitance vs VDS, gate charge vs VGS, diode forward voltage, SOA, ID vs case temperature, switching test circuit/waveforms, transient thermal impedance, unclamped inductive switching, gate charge waveforms.

## CoilShield / Pi usage (repo context)

- Pi GPIO **~3.3 V** high is **below** the **4 V / 5 V** RDS(on) table rows — use PDF **Figs. 1–3** or measure on hardware. See [../../anode-mosfet-irlz44.md](../../anode-mosfet-irlz44.md) and [../../mosfet-off-verification.md](../../mosfet-off-verification.md).  
- Firmware: `PWM_GPIO_PINS`, `PWMBank`, `PWM_FREQUENCY_HZ` in `config/settings.py`.
