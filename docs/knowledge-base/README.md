---
title: CoilShield hardware knowledge base
description: Datasheet- and vendor-doc-derived reference material for key ICs, the Raspberry Pi GPIO header, and CoilShield field wiring.
topics: [hardware, INA219, ADS1115, IRLZ44, MOSFET, I2C, shunt, gate-drive, Raspberry-Pi, GPIO]
---

# Hardware knowledge base

This directory holds **long-form** reference pages for parts and **platform GPIO** the firmware and field docs depend on. Use them when commissioning, bench-debugging, or comparing a clone BOM to the reference design.

| Entry | Topic | Upstream document |
|------|--------|-------------------|
| [components/raspberry-pi-gpio-header.md](components/raspberry-pi-gpio-header.md) | **Raspberry Pi** 40-pin GPIO — levels, power-on, alt functions | [gpio-on-raspberry-pi.adoc](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/gpio-on-raspberry-pi.adoc) |
| [components/vishay-irlz44-n-channel-mosfet.md](components/vishay-irlz44-n-channel-mosfet.md) | Vishay Siliconix **IRLZ44** | [91328 PDF](https://www.vishay.com/docs/91328/irlz44.pdf) |
| [components/ti-ina219-current-monitor.md](components/ti-ina219-current-monitor.md) | Texas Instruments **INA219** | [SBOS448 PDF](https://www.ti.com/lit/ds/symlink/ina219.pdf) |
| [components/ads1115-datasheet-notes.md](components/ads1115-datasheet-notes.md) | Texas Instruments **ADS1115** (reference ADC) | [SBAS444 PDF](https://www.ti.com/lit/ds/symlink/ads1115.pdf) |
| [components/tca9548a-datasheet-notes.md](components/tca9548a-datasheet-notes.md) | Texas Instruments **TCA9548A** (I²C mux) | [SCPS207 PDF](https://www.ti.com/lit/ds/symlink/tca9548a.pdf) |

**Shorter project notes** (cross-links, Pi-specific caveats) remain at repo root under `docs/`:

- [../raspberry-pi-gpio.md](../raspberry-pi-gpio.md)
- [../anode-mosfet-irlz44.md](../anode-mosfet-irlz44.md)
- [../ina219-datasheet-notes.md](../ina219-datasheet-notes.md)
- [../mosfet-off-verification.md](../mosfet-off-verification.md)

**Disclaimer:** Values and tables are transcribed for engineering convenience. **Authority** is always the vendor’s **current** datasheet or **upstream** Raspberry Pi documentation (revision, footnotes, and figures).
