# Texas Instruments ADS1113 / ADS1114 / ADS1115 — reference ADC knowledge base

Curated **datasheet facts** for CoilShield’s **reference electrode** path when `REF_ADC_BACKEND = "ads1115"` (`reference.py`, `i2c_bench.py`, `config/settings.py`). This page summarizes **SBAS444E** (*ADS111x Ultra-Small, Low-Power, I2C-Compatible, 860 SPS, 16-Bit ADCs with Internal Reference, Oscillator, and Programmable Comparator* — **May 2009, revised December 2024**). It is **not** a substitute for TI’s PDF — verify revision and test conditions on [ti.com](https://www.ti.com/product/ADS1115).

## Canonical datasheet

- **PDF:** [ADS1115 datasheet (SBAS444E)](https://www.ti.com/lit/ds/symlink/ads1115.pdf)  
- **Family:** ADS1113 (1 input, no PGA/comparator), ADS1114 (1 input + PGA + comparator), **ADS1115** (4× single-ended or 2× differential MUX + PGA + comparator). CoilShield uses the **ADS1115**.

## Role in this repo

| Role | Code / config |
|------|----------------|
| Reference potential (default) | `reference.py`, `REF_ADC_BACKEND`, `ADS1115_*`, `REF_ADS1115_DR`, `REF_ADS_MEDIAN_SAMPLES` |
| I²C register math | `i2c_bench.py` (`ads1115_*`, `_ads1115_config_word`, OS polling, volts/LSB) |
| Commissioning OC burst | `COMMISSIONING_ADS1115_DR`, burst interval/sample counts in `config/settings.py` |
| Bench / probe | `hw_probe.py`, `iccp probe`, `diagnostics.py` |

## Device summary (from datasheet)

- **Resolution:** 16-bit, **two’s complement** conversion result in the Conversion register.
- **Interface:** I²C target; **open-drain** SDA/SCL; **no clock stretching** by the ADS111x.
- **Supply VDD:** **2.0 V to 5.5 V** (Raspberry Pi **3.3 V** is typical).
- **Low power:** Order **150 µA** in continuous-conversion mode (see PDF); **single-shot** plus power-down is used for periodic reads and saves energy between samples.
- **Internal reference** and **oscillator** — no external crystal for conversion timing.
- **Max output data rate:** **860 SPS** (programmable **8 … 860 SPS** via `DR[2:0]`).
- **PGA full-scale ranges (ADS1114/ADS1115):** ±6.144 V, ±4.096 V, ±2.048 V, ±1.024 V, ±0.512 V, ±0.256 V (see **Table 8-3** in PDF). Footnote: FSR describes ADC scaling; **do not apply more than VDD + 0.3 V** to analog inputs.
- **ADS1115 MUX:** Four **single-ended** (AINn vs GND) or two **differential** pairs, selected by **`MUX[2:0]`** in the Config register.

## I²C target address (ADDR pin)

One pin **ADDR** strapped to **GND, VDD, SDA, or SCL** selects one of four 7-bit addresses (see **Table 7-2**):

| ADDR connection | 7-bit address (hex) |
|-----------------|---------------------|
| GND | 0x48 |
| VDD | 0x49 |
| SDA | 0x4A |
| SCL | 0x4B |

If **SDA** is used as the strap, TI specifies holding **SDA low for at least 100 ns after SCL goes low** so address decode is reliable during communication.

## General call reset

The device acknowledges the I²C **general call** address when the eighth bit is **0**. If the second byte is **06h**, the ADS111x perform an internal reset **as if powered up** (registers to defaults, power-down state).

## I²C bus timeout

If the I²C bus is **idle > 25 ms**, the bus **times out** (datasheet §7.5.1). Unusual on a healthy Pi stack, but relevant if debugging stuck-low SDA/SCL.

## Register map (Address Pointer P[1:0])

All data access goes through the **Address Pointer** (written as the byte after the target address in a write transaction):

| P[1:0] | Register | Purpose |
|--------|-----------|---------|
| 00b | **Conversion** | Last 16-bit conversion result (read-only), two’s complement |
| 01b | **Config** | Mode, MUX, PGA, DR, comparator / ALERT behavior |
| 10b | **Lo_thresh** | Comparator lower threshold (ADS1114/5); also used for conversion-ready setup |
| 11b | **Hi_thresh** | Comparator upper threshold (ADS1114/5); also used for conversion-ready setup |

**Endianness:** Register data is transferred **MSB first**, then LSB.

**Reset defaults (Config):** **8583h** — includes **MODE = single-shot / power-down**, **DR = 128 SPS**, **PGA = ±2.048 V**, **COMP_QUE = 11b** (comparator off, **ALERT/RDY high-Z**).

## Config register — ADS1115 bit fields (Table 8-3 summary)

### Bit 15 — OS (operational status / single-shot start)

- **Write:** **1** = start a **single** conversion when the device is in **power-down** (single-shot mode). **0** = no effect. Writing **1** during an active conversion has **no effect**.
- **Read:** **0** = conversion **in progress**; **1** = **not** converting (result may be read; in single-shot the device can return to power-down after data ready — see PDF §7.4.2.1).

CoilShield **polls OS** (or uses optional **ALERT/RDY** GPIO) after starting a conversion — this matches TI’s single-shot flow.

### Bits 14:12 — MUX[2:0] (ADS1115 only)

| MUX[2:0] | AINP | AINN |
|----------|------|------|
| 000b | AIN0 | AIN1 (differential default) |
| 001b | AIN0 | AIN3 |
| 010b | AIN1 | AIN3 |
| 011b | AIN2 | AIN3 |
| **100b** | **AIN0** | **GND** (single-ended) |
| **101b** | **AIN1** | **GND** |
| **110b** | **AIN2** | **GND** |
| **111b** | **AIN3** | **GND** |

Firmware maps `ADS1115_CHANNEL` 0…3 to **100b … 111b**.

### Bits 11:9 — PGA[2:0]

| PGA[2:0] | FSR (approx.) |
|----------|----------------|
| 000b | ±6.144 V |
| 001b | ±4.096 V |
| **010b** | **±2.048 V** (default) |
| 011b | ±1.024 V |
| 100b | ±0.512 V |
| 101b–111b | ±0.256 V |

**Code scaling:** Ideal LSB size = **FSR / 32768** (single-ended uses positive half of range; offset can still produce rare negative codes near 0 V — TI note under Table 7-3).

**Clip codes:** **+FS → 7FFFh**, **−FS → 8000h** (ideal; excludes noise/INL/offset/gain).

### Bit 8 — MODE

- **0** = **Continuous-conversion** (repeats at selected DR).
- **1** = **Single-shot** / power-down between conversions (**default**).

CoilShield uses **single-shot** for explicit sampling cadence and mux coexistence.

### Bits 7:5 — DR[2:0] (data rate)

| DR[2:0] | SPS |
|---------|-----|
| 000b | 8 |
| 001b | 16 |
| 010b | 32 |
| 011b | 64 |
| 100b | 128 (default after reset) |
| **101b** | **250** |
| 110b | 475 |
| **111b** | **860** |

**Conversion time:** TI states conversions **settle within a single cycle** and **conversion time ≈ 1 / DR** (parameter measurement section). Firmware uses **~1/DR** (with margin) for timeouts and scheduling.

### Comparator and ALERT/RDY (bits 4:0)

- **COMP_MODE, COMP_POL, COMP_LAT:** Traditional vs window comparator, polarity, latching behavior (see PDF §7.3.8).
- **COMP_QUE[1:0]:** **11b** = **comparator disabled** and **ALERT/RDY high-impedance** (**default** after reset). **Any value other than 11b** enables the comparator queue behavior (assert after 1, 2, or 4 successive threshold crossings).

**Conversion-ready on ALERT/RDY:** TI documents that when **Hi_thresh register MSB = 1** and **Lo_thresh register MSB = 0**, the pin can function as **conversion-ready** (details in §8.1.4). With **`COMP_QUE ≠ 11`**, the pin stays enabled for that behavior. In **continuous** mode, TI describes an **~8 µs** conversion-ready pulse at end of each conversion; in **single-shot**, ALERT/RDY asserts **low** at end of conversion (polarity per **COMP_POL**).

CoilShield programs **`COMP_QUE = 00`** in the config word and initializes **Lo_thresh / Hi_thresh** in `reference._init_ref_ads1115` so **ALERT/RDY** can pulse; **OS polling** remains the **reliable** completion path on Pi/Linux.

## Noise and effective resolution (§6.1)

ΔΣ performance: **lower DR** ⇒ higher internal oversampling ⇒ **lower input-referred noise**. **Higher PGA gain** also reduces input-referred noise for small signals.

**Table 6-1 / 6-2** (excerpted concept at **VDD = 3.3 V**, inputs shorted): RMS and peak-to-peak noise vs **DR** and **FSR**; effective bits **~16** at low DR, slightly reduced at **860 SPS** depending on FSR.

**Equations (TI):**

- Effective resolution = ln(FSR / V<sub>RMS noise</sub>) / ln(2)  
- Noise-free resolution = ln(FSR / V<sub>PP noise</sub>) / ln(2)

Use these tables when tuning **`REF_ADS1115_DR`** vs stability, or **`COMMISSIONING_ADS1115_DR`** for OC curves, vs observed reference jitter.

## Functional modes (§7.4)

### Single-shot (MODE = 1)

Default after power-up: **digital interface active**, **ADC powered down** until a **1** is written to **OS**. Device wakes in **~25 µs**, clears **OS** to **0**, runs one conversion, then returns to power-down when data are ready (TI sequence).

### Continuous (MODE = 0)

Conversions run back-to-back at **DR**. If Config is rewritten, the **current** conversion finishes with **old** settings; subsequent conversions use **new** settings.

### Duty cycling for power (§7.4.3)

Example from TI: **860 SPS** single-shot conversions **every 125 ms** ⇒ effective **8 SPS** with **~1/100** the average power of continuous 860 SPS — useful background for **why** single-shot is attractive on battery-aware designs.

## SMBus ALERT (§7.3.9)

In **latching comparator** mode, **ALERT/RDY** is **open-drain**; multiple devices can share the line. **SMBus alert response** (address **00011001b**) can discover which device asserted. Lowest I²C address **wins arbitration** if multiple assert.

## Application / hardware notes (§9–§10, summarized)

- **0.1 µF** bypass on **VDD** typical (transient conversion currents).
- **Pull-ups** on **SDA, SCL, ALERT/RDY** — ALERT/RDY needs a pull-up when used (open-drain).
- **Single-ended:** **0 V to +FS** (or supply), **no negative voltages** on inputs vs GND.
- **Differential** configurations maximize dynamic range and **common-mode rejection** vs single-ended.
- **Layout / decoupling / analog front-end:** See PDF **§9** (application) and **§11** (layout) for placement, ground splits, and noise — especially when reference shares a bus with **PWM** or long jumpers.

## Tie-in to firmware defaults

| Topic | Repo behavior |
|-------|----------------|
| Mode | **Single-shot** (`MODE=1` in `_ads1115_config_word`) |
| Channel | **`MUX = 4 + channel`** for AIN0…3 single-ended |
| PGA | Matches **`ADS1115_FSR_V`** / env **`COILSHIELD_ADS1115_FSR_V`** |
| Routine DR | **`REF_ADS1115_DR`** (default **5** = 250 SPS) |
| OC / burst DR | **`COMMISSIONING_ADS1115_DR`** (default **7** = 860 SPS) |
| Completion | **Poll Config OS**; optional **GPIO** on **ALERT/RDY** (`ADS1115_ALRT_*`) |
| Conversion-ready | **Lo_thresh / Hi_thresh** init + **`COMP_QUE = 00`** |
| Volts | **signed_code × (FSR / 32768)** |

If **`ADS1115_FSR_V`** does not match the programmed PGA, **mV readings drift** vs a DMM — use **`REF_ADS_SCALE`** / commissioning **`ref_ads_scale`** after FSR is correct.

## Related project docs

- [tca9548a-datasheet-notes.md](tca9548a-datasheet-notes.md) — I²C mux when the ADS1115 is on a downstream port.
- [iccp-requirements.md](../../iccp-requirements.md) §7 — reference backend requirements and sampling.
- [iccp-vs-coilshield.md](../../iccp-vs-coilshield.md) — signal chain mapping.
- [iccp-cli-reference.md](../../iccp-cli-reference.md) — `iccp probe` and mux behavior.
- [ina219-datasheet-notes.md](../../ina219-datasheet-notes.md) — anode current sense companion part.
- [reference-electrode-placement.md](../../reference-electrode-placement.md) — field geometry (not register-level).
