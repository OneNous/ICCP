# Texas Instruments INA219 — shunt / bus monitor reference

**Full datasheet tables, I²C address map, registers, and calibration math:** [knowledge-base/components/ti-ina219-current-monitor.md](knowledge-base/components/ti-ina219-current-monitor.md) — this page stays a **short** firmware-integration summary.

Curated **datasheet facts** for CoilShield’s **per-anode** current and bus-voltage sensing (and optional **legacy** reference use via `REF_INA219_*`). Firmware talks to the chip through **`pi-ina219`** (`sensors.py`, `i2c_bench.py`); behavior and addresses live in `config/settings.py`. This page is **not** a substitute for TI’s PDF—always verify revision and conditions on [ti.com](https://www.ti.com/product/INA219).

## Canonical datasheet

- **PDF:** [INA219 datasheet (SBOS448)](https://www.ti.com/lit/ds/symlink/ina219.pdf)  
- **Title / revision (as summarized):** *Zerø-Drift, Bidirectional Current/Power Monitor With I²C Interface* — SBOS448G (August 2008–revised December 2015 in the indexed copy). TI may issue newer revisions; check the landing page before quoting limits in contracts.

## Role in this repo

| Role | Code / config |
|------|-----------------|
| Four anode channels | `sensors.py`, `INA219_ADDRESSES`, shunt **0.1 Ω** default on breakouts |
| Optional fifth / reference | `reference.py` when `REF_ADC_BACKEND = "ina219"` — **legacy**, see [iccp-requirements.md](iccp-requirements.md) |
| Probe / bench | `hw_probe.py`, `iccp probe` |

## Device summary (from datasheet)

- **Interface:** I²C- / SMBus-compatible; **16** strap combinations on **A0/A1** → 16 addresses (see TI Table 1 in the PDF).
- **Supply VS:** **3 V to 5.5 V** (typical Pi **3.3 V** is in range). Quiescent current order **1 mA** active (see PDF).
- **Bus voltage (ADC range):** Full-scale scaling **16 V** or **32 V** via **BRNG** in the configuration register; datasheet text stresses **do not exceed 26 V** on the bus pins in practice—stay within your breakout and stack design.
- **Shunt sense (PGA):** Programmable gain **÷1, ÷2, ÷4, ÷8** maps to nominal **±40, ±80, ±160, ±320 mV** full-scale differential **(VIN+ − VIN−)** across the shunt (see TI §7.5). **12-bit** result; shunt voltage **LSB ≈ 10 µV** (typ), bus voltage **LSB ≈ 4 mV** (typ).
- **Common-mode:** High **CMRR**; inputs operate with common-mode up to the bus rating—layout and Kelvin sense to the shunt still matter for accuracy.
- **Grades:** **INA219A** vs **INA219B** (B: tighter max specs on some parameters—see ordering table).

## Pin concept (wiring sanity)

Per TI pin table: **IN+** / **IN−** are the **differential shunt** nodes; **bus voltage** is measured **from IN− to GND**. Mis-wiring **IN+ / IN−** vs your breakout’s **“bus”** labels is a common source of wrong sign or nonsense current—cross-check your board silk against the PDF, then [mosfet-off-verification.md §3](mosfet-off-verification.md).

## Tie-in to firmware defaults

This project’s real path uses **`pi-ina219`** with **0.1 Ω** shunts, **`INA219.RANGE_16V`**, **`GAIN_AUTO`**, and **128-sample** averaging on bus/shunt ADC (`sensors.py`). Bench **`i2c_bench`** uses a **CONFIG** word aligned with that stack (**0x07FF** when auto-gain resolves to **40 mV** range—see comments in `sensors.py` / `i2c_bench.py`). If you change shunt value or PGA manually, **`Calibration`** / current math must stay consistent with the TI programming model (see PDF §8.5 **Calibration** register).

## SMBus timeout (I²C stuck-low)

The datasheet describes an **SMBus timeout** (on the order of **28 ms**) that can reset the interface if **SCL or SDA** is held low too long. In normal Pi use this rarely bites, but it is one reason **long** I²C hangs or **aggressive** clock stretching debug should reference the PDF, not guess.

## Accuracy vs commissioning thresholds

TI quotes **current** and **bus** measurement error bands (grade and temperature dependent—see §7.5). CoilShield’s “at rest” gates (**`COMMISSIONING_OC_CONFIRM_I_MA`**, **`COMMISSIONING_PHASE1_NATIVE_ABORT_I_MA`**, etc.) must remain **above** combined **offset + noise + layout** for your shunt and PGA; if Phase 1 flaps near zero, tighten **hardware** and **sample filtering** before chasing thresholds in software alone.

## Related project docs

- [tca9548a-datasheet-notes.md](knowledge-base/components/tca9548a-datasheet-notes.md) — optional I²C mux when INA219/ADS sit on downstream ports.
- [ads1115-datasheet-notes.md](knowledge-base/components/ads1115-datasheet-notes.md) — reference ADC (default `REF_ADC_BACKEND`).
- [mosfet-off-verification.md](mosfet-off-verification.md) §3 — INA219 wiring and “full bus” vs FET off.
- [README.md](../README.md) — default addresses, `iccp probe`, reference INA219 section.
- [iccp-comparison.md](iccp-comparison.md) — five-INA219 hardware overview and standards links.
- [iccp-cli-reference.md](iccp-cli-reference.md) — `iccp probe` behavior.
