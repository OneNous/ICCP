# Texas Instruments TCA9548A — 8-channel I²C switch reference

Curated **datasheet facts** for CoilShield’s optional **I²C mux** (multiple INA219 + ADS1115 on one upstream bus, different downstream branches). Firmware selects ports via **`i2c_bench.mux_select_on_bus`**; addresses and channel map live in **`config/settings.py`** (`I2C_MUX_*`). This page is **not** a substitute for TI’s PDF—always verify revision and limits on [ti.com](https://www.ti.com/product/TCA9548A). Header **I²C0** pinout and **3.3 V** GPIO rules: [raspberry-pi-gpio.md](raspberry-pi-gpio.md).

## Canonical datasheet

- **PDF:** [TCA9548A datasheet (SCPS207)](https://www.ti.com/lit/ds/symlink/tca9548a.pdf)  
- **Title / revision (as indexed here):** *TCA9548A Low-Voltage 8-Channel I2C Switch with Reset* — **SCPS207H** (May 2012, revised **September 2024** in the indexed copy). TI may issue newer revisions; check the product folder before quoting limits.

## Role in this repo

| Role | Code / config |
|------|----------------|
| Steer upstream bus to one of eight downstream pairs | `i2c_bench.mux_select_on_bus` → SMBus **`write_byte(mux_addr, control_byte)`** |
| Per-anode INA219 on different ports | `I2C_MUX_ADDRESS`, `I2C_MUX_CHANNELS_INA219` |
| Legacy: one mux port for all anode INA219s | `I2C_MUX_CHANNEL_INA219` |
| ADS1115 on its own port | `I2C_MUX_CHANNEL_ADS1115` |
| Optional settle time after select | `I2C_MUX_POST_SELECT_DELAY_S` |
| Bus serialization (Pi / mux glitches) | `i2c_bench.i2c_bus_lock` |
| Commissioning / bench visibility | `hw_probe.py`, **`iccp probe`** STEP 1 + **STEP 1b** |
| Human-readable wiring labels | `channel_labels.anode_hw_label` |

## Product summary (from datasheet §1–§3)

- **Function:** **1-to-8 bidirectional translating switches** — upstream **SCL/SDA** connect to **SC0/SD0 … SC7/SD7** downstream pairs.
- **Control:** **I²C / SMBus** target; **one 8-bit control register** (no separate register address in the transaction).
- **RESET:** **Active-low** — resets registers and I²C state machine, **deselects all channels** (same end state as **power-on reset** when asserted long enough).
- **Address pins:** **A0, A1, A2** → up to **eight TCA9548A devices** on the same upstream bus (addresses **0x70–0x77**).
- **Channel programming:** Any **single** channel **or combination** of channels may be enabled (each bit = one channel).
- **Power-up:** **All channels deselected**; **no glitch on power-up** (marketing bullet); supports **hot insertion**.
- **Voltage:** **VCC 1.65 V to 5.5 V** recommended (see §5.3 for **TA** vs **VCC** corner); **5-V tolerant** I/O.
- **Translation:** Pass-gate construction + **per-segment pull-ups** allow **different VDPU** on upstream vs each downstream pair (e.g. 1.8 / 2.5 / 3.3 V segments talking to 5 V — see §7.1–§7.3).
- **I²C rate:** **Standard-mode (100 kHz)** and **Fast-mode (400 kHz)** (feature list / §7.3).
- **ESD (datasheet table):** **HBM ±2000 V**, **CDM ±1000 V** (§5.2) — manufacturing handling, not field abuse.

## Pin functions (condensed from §4 / Table 4-1)

| Pin(s) | Type | Role |
|--------|------|------|
| **SCL, SDA** | I/O | **Upstream** controller bus — pull up to **VDPUM** (mux-side pull-up rail). |
| **SCn, SDn** | I/O | **Downstream** channel **n** — pull up to **VDPU*n*** for that segment. |
| **A0, A1, A2** | Input | Strap to **VCC** or **GND** — **7-bit address** bits (not the data byte). |
| **RESET** | Input | **Active low**; if unused, tie to **VCC** (or VDPUM per footnote) **through a pull-up** per TI. |
| **VCC, GND** | Power | Device supply and return. |

Package options calld out in the PDF include **TSSOP-24 (PW)**, **VQFN-24 (RGE)**, **VSSOP-24 (DGS)** — body sizes in the ordering / packaging addendum.

## Absolute maximum and recommended operating (§5.1–§5.3)

**Absolute maximum (stress ratings, not continuous operating):**

- **VCC:** −0.5 V to **7 V**
- **Input voltage on pins:** −0.5 V to **7 V** (with footnote on exceeding ratings if currents are limited)
- **Storage T:** −65 °C to **150 °C**

**Recommended operating (highlights):**

- **VCC:** **1.65 V to 5.5 V** for **−40 °C ≤ TA ≤ 85 °C**; for **85 °C < TA ≤ 125 °C**, **VCC** max **3.6 V** (see full table in PDF).
- **VIH** on **SCL, SDA:** **0.7 × VCC** up to **6 V** (note the 6 V ceiling in the table).
- **VIH** on **A2–A0, RESET:** **0.7 × VCC** to **VCC + 0.5 V**
- **VIL:** **−0.5 V** to **0.3 × VCC**

## Electrical characteristics (selected, §5.5)

Interpret **MIN/TYP/MAX** and **test conditions** from the PDF for your **VCC** and **TA**.

- **Power-on reset thresholds (no load, VI = VCC or GND):**  
  - **VPORR** (VCC rising): **1.2 V min, 1.5 V typ**  
  - **VPORF** (VCC falling): **0.8 V to 1 V** (table range)
- **ICC operating** depends on **VCC** and **fSCL** (100 kHz vs 400 kHz); e.g. at **400 kHz**, **3.6 V** typ on the order of **20–35 µA** (see full table).
- **Standby ICC** with inputs low or high: sub-**µA** to a few **µA** typical depending on **VCC** (see table).
- **RON (switch on-resistance):** depends strongly on **VCC** and **IO** test condition — e.g. **3 V–3.6 V**, **VO = 0.4 V**, **IO = 15 mA**: **5 Ω typ**, **30 Ω max** (one row); lower **VCC** and higher **IO** worsen **RON** (see §5.5 and typical curves).

## I²C interface timing (§5.6)

**Standard-mode (100 kHz):** e.g. **SCL high ≥ 4 µs**, **SCL low ≥ 4.7 µs**, **tBUF** (bus free between STOP and START) **≥ 4.7 µs**, **Cb** (cap per line) **≤ 400 pF**.

**Fast-mode (400 kHz):** e.g. **SCL high ≥ 0.6 µs**, **SCL low ≥ 1.3 µs**, **tBUF ≥ 1.3 µs**; rise/fall times scale with **Cb** per formulas in the table (e.g. **ticr** / **ticf** include **20 + 0.1·Cb** ns terms with caps).

## Reset timing (§5.7)

- **tW(L)** — **RESET** low pulse duration: **≥ 6 ns** (**min** in table — still assert clean, glitch-free low in real boards).
- **tREC(STA)** — recovery from **RESET** to **START:** **0 ns** min (controller may begin I²C immediately per table).

## Switching characteristics (§5.8)

- **Propagation** from **SDA/SCL** to **SDn/SCn** is characterized as **RC**-limited with **RON** and **CL** (see note under **tpd**).
- **trst** — **RESET** to **SDA** released high: **500 ns** typ (see figure note — relates to clearing a stuck bus scenario).

## Programming model (§7.5) — what the firmware must do

### Target address (§7.5.2, Table 7-1)

7-bit pattern **1110 A2 A1 A0** (TI figure shows fixed **1110** prefix with strap bits).

| A2 | A1 | A0 | Decimal | Hex (7-bit) |
|----|----|-----|-----------|-------------|
| L | L | L | 112 | **0x70** |
| L | L | H | 113 | 0x71 |
| … | … | … | … | … |
| H | H | H | 119 | **0x77** |

CoilShield’s default **`I2C_MUX_ADDRESS = 0x70`** matches **all straps low**.

### Single-register device (§7.5.3)

TI explicitly states the TCA9548A is a **simple single-register** device: after the **7-bit address + W**, the controller sends **the control byte directly** — **no register sub-address byte**.

### Writes (§7.5.3.1, Figure 7-4)

**START** → **device address, R/W=0** → **ACK** → **8-bit control register** → **ACK** → **STOP**. If multiple data bytes are sent, **the last byte wins** (§7.5.4).

### Reads (§7.5.3.2, Figure 7-5)

**START** → **address, R/W=1** → **ACK** → device returns **control register** → **NACK** + **STOP** from controller.

### Control register semantics (§7.5.4, Figure 7-6, Table 7-2)

- **B0** enables **channel 0**, **B1** → channel 1, … **B7** → channel 7.
- **1** = channel **enabled**, **0** = **disabled**.
- **0x00** = **no channel selected** — **power-up / reset default** (Table 7-2).
- **Multiple bits set** = multiple downstream segments **on at once** (legal if your wiring/I²C rules allow).
- **Channel becomes active after a STOP** on the bus — TI requires lines in a **high** state before connection so **false edges** are not injected; **STOP must follow the ACK** of the write (§7.5.4).

**CoilShield convention:** `mux_select_on_bus` writes **`1 << n`** for **`n` in 0…7** — exactly **one** channel on at a time, matching common “tree” wiring.

## RESET and POR (§7.4, §7.5.5–§7.5.6)

- **RESET low:** resets **registers** and **I²C state machine**, **deselects all** channels (must meet **tW(L)** minimum).
- **RESET** must be **pulled up** to **VCC** when not driven.
- **POR** on **VCC** ramp: held in reset until **VPOR** reached; registers default to **0**, all channels **off**; **VCC** must fall below **VPOR** again to repeat a full power-reset cycle (see wording in §7.5.6 vs §5.5 symbol names — follow the PDF figure you use for design).

**Firmware today:** does not bit-bang **RESET**; recovery is **power cycle** or external hardware. Optional future: GPIO-driven **RESET** if downstream **SDA stuck low** is a field issue.

## Tie-in to CoilShield firmware

| Datasheet behavior | Repo behavior |
|--------------------|---------------|
| Write **one byte** after address | `SMBus.write_byte(mux_addr, 1 << ch)` in `mux_select_on_bus` |
| Channels **0–7** | `ValueError` if outside range |
| **STOP** completes before downstream traffic | Normal **write_byte** sequence ends with **STOP**; then **`I2C_MUX_POST_SELECT_DELAY_S`** optional sleep |
| Idle scan hides downstream targets | `ads1115_behind_i2c_mux`, **`iccp probe`** STEP **1b** |
| **0x00** deselect all | Not required for normal reads; useful for diagnostics / power if you add explicit **deselect** calls |
| Up to **8 mux ICs** on one bus | Not modeled in config today (single **`I2C_MUX_ADDRESS`**); extend if you stack multiple muxes |

## Bench / debug implications

1. **`i2cdetect` without selecting a port** often shows **only 0x70** (or your strap address) — expected per **POR default** (all channels off).
2. **Wrong port** → **NACK** on INA219/ADS1115 — use **`iccp probe`** STEP **1b** and cross-check **`I2C_MUX_CHANNELS_INA219`** / **`I2C_MUX_CHANNEL_ADS1115`**.
3. **Errno 5 / EIO** when hopping mux → downstream: see comments in **`i2c_bench.py`** on **per-bus lock** and **`I2C_MUX_POST_SELECT_DELAY_S`** tuning.

## Related project docs

- [ina219-datasheet-notes.md](ina219-datasheet-notes.md) — shunt monitor on each downstream branch.
- [README.md](../README.md) — **`iccp probe`**, mux vs flat bus.
- [iccp-cli-reference.md](iccp-cli-reference.md) — probe STEP **1** / **1b** text.
- [mosfet-off-verification.md](mosfet-off-verification.md) — anode indexing vs mux port in logs.
- [iccp-requirements.md](iccp-requirements.md) — Phase 0 I²C precheck mentions mux.
