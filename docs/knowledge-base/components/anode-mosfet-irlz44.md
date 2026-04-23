# Vishay IRLZ44 — anode switch reference (logic-level N-channel)

**Full datasheet tables and figures index:** [knowledge-base/components/vishay-irlz44-n-channel-mosfet.md](knowledge-base/components/vishay-irlz44-n-channel-mosfet.md) — this page stays a **short** Pi-focused summary.

This note captures **datasheet facts** useful for CoilShield’s **Raspberry Pi GPIO–driven** anode MOSFETs. It is **not** a BOM mandate: any suitable **N-channel enhancement** low-side switch with adequate **VDS**, **RDS(on)** at your real **VGS**, and layout-appropriate **Qg** is fine. Firmware behavior is described generically in [mosfet-off-verification.md](mosfet-off-verification.md) and the README (PWM, pull-downs, static gate LOW).

## Canonical datasheet

- **PDF:** [Vishay Siliconix IRLZ44 (document 91328)](https://www.vishay.com/docs/91328/irlz44.pdf)  
- **Rev. / date (as fetched):** D, 25-Oct-2021 — always confirm revision on Vishay’s site.

## Why this part class fits a Pi

- **Logic-level gate:** RDS(on) is specified at **VGS = 4 V** and **5 V** (not only 10 V). That aligns with **CMOS ~3.3 V** high levels from the Pi, with one important caveat below.
- **Enhancement N-channel:** **VGS(th)** typ/max range **1.0–2.0 V** (250 µA test). When the gate is held **low vs source** (software static LOW, pull-down, or `GPIO.LOW`), **VGS ≈ 0** → device intended off — same assumptions as [mosfet-off-verification.md §2](mosfet-off-verification.md).
- **Abs max VGS ±10 V:** Pi **~3.3 V** drive is safely inside the limit.

## Curated electrical highlights (TJ = 25 °C unless noted)

All limits are **subject to datasheet test conditions** — use the PDF for pulse width, case temperature, and footnotes.

| Parameter | Symbol | Typical / limit (see PDF) |
|-----------|--------|-------------------------|
| Drain–source voltage | VDS | 60 V max |
| Gate–source voltage | VGS | ±10 V max |
| Drain–source on-resistance | RDS(on) | max **0.028 Ω** @ VGS = 5 V, ID = 31 A (pulse); max **0.039 Ω** @ VGS = 4 V, ID = 25 A (pulse) |
| Gate threshold | VGS(th) | 1.0–2.0 V @ ID = 250 µA |
| Total gate charge | Qg | max **66 nC** (stated test circuit in datasheet) |
| Input capacitance | Ciss | typ **3300 pF** @ VDS = 25 V, f = 1 MHz |
| Continuous drain current | ID | **50 A** @ TC = 25 °C, VGS = 5 V (package/thermal limited at higher TC) |

Figures **1–3** (output and transfer characteristics) are the right place to judge **RDS(on)** and **ID** when **VGS** is closer to **3.3 V** than to the 4–5 V table rows.

## 3.3 V gate drive caveat

The Pi GPIO high is about **3.3 V**, not **4 V** or **5 V**. The IRLZ44 **electrical characteristics table** starts RDS(on) at **4 V**. Do **not** assume the 4 V/5 V **max RDS(on)** numbers apply unchanged at **3.3 V** — check **Fig. 1–3** on the PDF (or measure **VGS** and shunt current on your board at the duties you use).

If you see weak saturation (excess drop, heat) at high channel current, treat it as **hardware**: stronger gate drive, different part with **RDS(on)** specified at lower VGS, or lower stack current — not a firmware knob.

## PWM and probing (tie-in to firmware docs)

Large **Qg** and **Ciss** mean edges are slower when driven through a **~100 Ω** series resistor from a **weak CMOS** output. That supports the project guidance to **scope the gate** if you raise **`PWM_FREQUENCY_HZ`** toward **≥20 kHz** (`config/settings.py`, README **Anode PWM frequency**).

## Related project docs

- [raspberry-pi-gpio.md](raspberry-pi-gpio.md) — official GPIO levels, power-on input state, **BCM2711 VOH** vs gate drive.
- [mosfet-off-verification.md](mosfet-off-verification.md) — floating gate, pull-downs, soft-PWM 0% vs static LOW, commissioning.
- [README.md](../README.md) — `PWM_FREQUENCY_HZ`, gate resistor, `ExecStartPre` gate hold script.
