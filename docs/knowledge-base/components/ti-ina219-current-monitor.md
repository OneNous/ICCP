---
title: Texas Instruments INA219 (I2C current / bus monitor)
description: Full datasheet parameter and register transcription — SBOS448 zerø-drift bidirectional shunt and bus monitor.
topics: [INA219, TI, I2C, SMBus, shunt, bus-voltage, calibration, CoilShield]
vendor: Texas Instruments
document_id: SBOS448G
pdf: "https://www.ti.com/lit/ds/symlink/ina219.pdf"
---

# INA219 — knowledge base entry

**Source:** Texas Instruments **INA219**, *Zerø-Drift, Bidirectional Current/Power Monitor With I²C Interface*, **SBOS448G** — August 2008, revised December 2015 (confirm revision on [ti.com](https://www.ti.com/lit/ds/symlink/ina219.pdf)).

## Description (datasheet)

Digital **shunt voltage** and **bus voltage** monitor with **I²C- / SMBus-compatible** interface; reports **current**, **voltage**, and **power** using programmable resolution, conversion times, averaging, and **Calibration** register scaling. **16** pin-strapped addresses. **VS** = **3 V to 5.5 V**; bus under test **0 to 26 V** (no special supply sequencing vs bus). Packages: **SOIC-8**, **SOT23-8**.

**Grades:** **INA219A** and **INA219B** (B: tighter max specs on several parameters in §7.5).

## Pin functions (summary)

| Pin | Name | Description |
|-----|------|-------------|
| Analog | IN+ | Positive differential shunt sense |
| Analog | IN− | Negative differential shunt sense; **bus voltage measured from IN− to GND** |
| — | GND | Analog ground |
| — | VS | IC supply **3–5.5 V** |
| Digital | SCL | I²C clock |
| Digital | SDA | I²C data (open-drain) |
| Digital | A0, A1 | Address select (sampled each transaction) |

## Absolute maximum ratings

| Parameter | Symbol | Min | Max | Unit |
|-----------|--------|-----|-----|------|
| Supply voltage | VS | — | 6 | V |
| Differential (VIN+ − VIN−) | — | −26 | +26 | V |
| IN+, IN− common-mode | — | −0.3 | 26 | V |
| SDA | — | GND−0.3 | 6 | V |
| SCL | — | GND−0.3 | VS+0.3 | V |
| Current into any pin | — | — | 5 | mA |
| Open-drain digital output current | — | — | 10 | mA |
| Operating temperature | — | −40 | 125 | °C |
| Junction temperature | TJ | — | 150 | °C |
| Storage temperature | Tstg | −65 | 150 | °C |

**Note (datasheet):** Differential ±26 V allowed, but pin voltages must remain **−0.3 V to 26 V**; do not exceed **26 V** bus rating in application.

## ESD ratings (HBM / CDM / MM)

Per datasheet §7.2: HBM **±4000 V**, CDM **±750 V**, Machine model **±200 V** (see PDF conditions).

## Recommended operating conditions

| Parameter | Min | Nom | Max | Unit |
|-----------|-----|-----|-----|------|
| Common-mode VCM | — | 12 | — | V (example) |
| VS | 3 | 3.3 | 5.5 | V |
| TA | −25 | — | 85 | °C |

## Electrical characteristics (selected, TA = 25 °C, VS = 3.3 V unless noted)

Conditions in datasheet table header often include **VIN+ = 12 V**, **VSHUNT = 32 mV**, **PGA = ÷1**, **BRNG = 1** — see PDF for full conditions and **INA219A vs INA219B** columns.

### Shunt full-scale (PGA)

| PGA | VSHUNT full-scale range |
|-----|-------------------------|
| ÷1 | ±40 mV |
| ÷2 | ±80 mV |
| ÷4 | ±160 mV |
| ÷8 | ±320 mV |

### Bus voltage ADC full-scale (BRNG bit 13)

| BRNG | Bus voltage FSR (scaling) |
|------|---------------------------|
| 0 | 16 V |
| 1 | 32 V |

**Datasheet:** Do not apply more than **26 V** to the bus pins.

### Input / ADC

| Parameter | Notes |
|-----------|--------|
| CMRR | 100–120 dB typ (VIN+ = 0 to 26 V) |
| Offset voltage RTI | PGA-dependent (µV range; see PDF A vs B) |
| ADC resolution | **12 bits** |
| Shunt voltage LSB | **10 µV** |
| Bus voltage LSB | **4 mV** (bus register uses **3 LSB padding** — shift right **3** before × 4 mV) |
| Current measurement error | Grade/temp dependent (e.g. ±0.2% / ±0.5% typ/max bands in PDF) |
| Bus voltage measurement error | Similar bands in PDF |

### ADC conversion times (single sample, not averaged)

| Resolution | Conversion time (typ range in PDF) |
|------------|-----------------------------------|
| 9-bit | 84–93 µs |
| 10-bit | 148–163 µs |
| 11-bit | 276–304 µs |
| 12-bit | 532–586 µs |

### Averaging (BADC / SADC = 1xxx pattern)

| Samples | Conversion time (typ) |
|-----------|----------------------|
| 2 | 1.06 ms |
| 4 | 2.13 ms |
| 8 | 4.26 ms |
| 16 | 8.51 ms |
| 32 | 17.02 ms |
| 64 | 34.05 ms |
| 128 | 68.10 ms |

### Supply

| Parameter | Min | Max | Unit |
|-----------|-----|-----|------|
| VS operating | 3 | 5.5 | V |
| IQ active | 0.7 | 1 | mA typ/max |
| IQ power-down | 6 | 15 | µA typ/max |
| Power-on reset threshold | — | 2 | V |

### SMBus timeout

**28–35 ms** (min–max in table): if **SCL** or **SDA** held low beyond timeout, interface resets (prevents bus lock-up).

### I²C clock

Fast mode **0.001–0.4 MHz**; high-speed mode **0.001–2.56 MHz** per bus timing table (see PDF).

## Table 1 — A0/A1 strapping and 7-bit I²C address

Slave address = `0b1000A3A2A1A0` in the datasheet’s **7 MSBs**; **8-bit write address** = `(7bit << 1)`.

| A1 pin | A0 pin | 7-bit address (binary) | 7-bit (hex) | 8-bit write (hex) |
|--------|--------|------------------------|-------------|-------------------|
| GND | GND | 1000000 | **0x40** | 0x80 |
| GND | VS+ | 1000001 | **0x41** | 0x82 |
| GND | SDA | 1000010 | **0x42** | 0x84 |
| GND | SCL | 1000011 | **0x43** | 0x86 |
| VS+ | GND | 1000100 | **0x44** | 0x88 |
| VS+ | VS+ | 1000101 | **0x45** | 0x8A |
| VS+ | SDA | 1000110 | **0x46** | 0x8C |
| VS+ | SCL | 1000111 | **0x47** | 0x8E |
| SDA | GND | 1001000 | **0x48** | 0x90 |
| SDA | VS+ | 1001001 | **0x49** | 0x92 |
| SDA | SDA | 1001010 | **0x4A** | 0x94 |
| SDA | SCL | 1001011 | **0x4B** | 0x96 |
| SCL | GND | 1001100 | **0x4C** | 0x98 |
| SCL | VS+ | 1001101 | **0x4D** | 0x9A |
| SCL | SDA | 1001110 | **0x4E** | 0x9C |
| SCL | SCL | 1001111 | **0x4F** | 0x9E |

CoilShield defaults **0x40, 0x41, 0x44, 0x45** for four anodes (`config/settings.py`).

## Register map (Table 2 summary)

| Pointer (hex) | Register | Function | Type | Power-on reset (hex, per PDF) |
|---------------|----------|----------|------|-------------------------------|
| 00 | Configuration | Reset, BRNG, PGA, ADC avg, MODE | R/W | **399F** (see PDF bit diagram; verify on silicon) |
| 01 | Shunt voltage | Shunt reading | R | — |
| 02 | Bus voltage | Bus reading | R | — |
| 03 | Power | Power | R | 0000 |
| 04 | Current | Current | R | 0000 (until Calibration programmed) |
| 05 | Calibration | Current/power scaling | R/W | 0000 |

**4 µs** delay between completing a **write** and reading **same** register without pointer change at **SCL > 1 MHz** (datasheet).

### Configuration register (00h) — bit fields (summary)

- **RST (15):** 1 = full software reset (self-clearing).  
- **BRNG (13):** 0 = 16 V bus FSR, 1 = 32 V bus FSR.  
- **PG1:PG0 (12:11):** PGA shunt range (Table above; **default /8** in datasheet text).  
- **BADC (10:7), SADC (6:3):** bus / shunt ADC resolution and averaging (Table 5 in PDF).  
- **MODE (2:0):** power-down, triggered, ADC off, continuous shunt/bus (Table 6 in PDF).

### Mode settings (Table 6)

| MODE3..0 | Mode |
|----------|------|
| 000 | Power-down |
| 001 | Shunt triggered |
| 010 | Bus triggered |
| 011 | Shunt + bus triggered |
| 100 | ADC off |
| 101 | Shunt continuous |
| 110 | Bus continuous |
| **111** | **Shunt + bus continuous** (shaded default) |

## Programming — calibration and scaling (§8.5)

Until **Calibration (05h)** is programmed, **Current (04h)** and **Power (03h)** read **zero**.

**Shunt voltage:** `Vshunt = ShuntVoltageRegister × 10 µV` (sign per PGA / twos complement; see PDF).

**Bus voltage:** Shift bus register contents **right by 3 bits**, then multiply by **4 mV** (BD0 in LSB after shift).

**Calibration value** (concept from datasheet):

- Choose **Current_LSB** (A per LSB) — often smallest LSB from max expected current, rounded up for convenience.  
- **Cal = trunc(0.04096 / (Current_LSB × R_SHUNT))** (internal constant **0.04096** per TI).  
- **Current_LSB** min from **MaxExpectedCurrent / 2^15** (Equation 2 style in PDF).  
- **Power_LSB = 20 × Current_LSB** (power register scaling).

**After calibration:**

- Current register relates to shunt voltage × calibration / 4096 (Equation 4 in PDF).  
- Power register: multiply current × bus / 5000 per Equation 5; final watts = register × Power_LSB.

**Default unprogrammed use:** Read **shunt** and **bus** registers only at default **12-bit**, **PGA ÷8 (320 mV)**, **32 V bus**, continuous shunt+bus — per **§8.5.3**.

## Functional notes (from datasheet text)

- **ΔΣ** front-end, **~500 kHz ±30%** sampling; add **RC input filter** only if transients align with harmonics **> ~1 MHz**; suggested **0.1–1 µF** ceramic + **low series R**.  
- **Overload / dV/dt:** Inputs tolerate **26 V** differential in spec; inductive kickback can exceed rating — use clamps + bulk caps. **10 Ω** series on each input cited as protecting against dV/dt failure in tests up to 26 V.  
- **Power-down:** Full recovery **~40 µs** after leaving power-down.  
- **CNVR** (conversion ready) and **OVF** (math overflow) bits on bus voltage register — see **§8.6.3.2**.  
- Shunt and bus samples are **not simultaneous**; at **12-bit + 128×** averaging, **~68 ms** can elapse between the two (§8.3.1.1).

## CoilShield / repo context

- `sensors.py`: **0.1 Ω** shunt, **RANGE_16V**, **GAIN_AUTO**, **128-sample** averaging.  
- `i2c_bench.py`: CONFIG alignment comments (`0x07FF` path).  
- Legacy reference: `REF_INA219_*` in `config/settings.py`.  
- Short cross-links: [../../ina219-datasheet-notes.md](../../ina219-datasheet-notes.md), [../../mosfet-off-verification.md](../../mosfet-off-verification.md) §3.
