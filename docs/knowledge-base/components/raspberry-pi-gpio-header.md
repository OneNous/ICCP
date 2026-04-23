---
title: Raspberry Pi — 40-pin GPIO header (official documentation digest)
description: Transcription of Raspberry Pi Ltd GPIO header documentation — levels, power-on behavior, alternate functions, and voltage tables by SoC family.
topics: [Raspberry-Pi, GPIO, BCM2835, BCM2711, PWM, I2C, drive-strength, CoilShield]
vendor: Raspberry Pi Ltd
source_url: "https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/gpio-on-raspberry-pi.adoc"
---

# Raspberry Pi GPIO — knowledge base entry

**Source:** Raspberry Pi documentation, *GPIO and the 40-pin header* — AsciiDoc in the [raspberrypi/documentation](https://github.com/raspberrypi/documentation) repo: [`gpio-on-raspberry-pi.adoc`](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/gpio-on-raspberry-pi.adoc). **Rendered** pages move with site structure; the GitHub file is a stable permalink to the **exact** text transcribed here. Reconcile any numbers with the **current** [Raspberry Pi computers documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html) before hard design decisions.

## Physical header

- **40-pin** GPIO header on current Raspberry Pi boards; **0.1 in (2.54 mm)** pitch.  
- **NOTE:** Header may be **unpopulated** on **Zero** and **Pico** devices **without** the **“H”** suffix.

## Pin behavior (conceptual)

- Pins may be **general-purpose input**, **general-purpose output**, or one of **up to six** **alternate** functions (function set is **pin-dependent**).  
- **NOTE:** GPIO numbering is **not** in physical pin order. **GPIO0** and **GPIO1** exist (physical pins **27** and **28**) but are **reserved** (e.g. ID EEPROM on many boards).

### Outputs

Output high ≈ **3.3 V**, low ≈ **0 V**.

### Inputs

Read as high / low; **internal pull-up or pull-down** configurable in software for most pins. **GPIO2** and **GPIO3** have **fixed pull-ups** (I²C).

### Reference on device

Run **`pinout`** in a terminal on Raspberry Pi OS (tool from [GPIO Zero](https://gpiozero.readthedocs.io/), installed by default).

### Safety (datasheet-style warnings)

- Use **series resistors** for LEDs.  
- **Do not** apply **5 V** to **3.3 V**–tolerant GPIO logic.  
- **Do not** connect **motors** directly to GPIO — use an **H-bridge** or motor driver.

### Permissions

User must be in the **`gpio`** group (default user usually is):

```bash
sudo usermod -a -G gpio <username>
```

## Silicon: “pads” and configuration

SoC pins are sometimes called **pads** in Broadcom peripheral docs. They are **CMOS push-pull** drivers / input buffers. Register-controlled options include:

- Internal **pull-up / pull-down** enable/disable  
- Output **drive strength**  
- Input **Schmitt-trigger** filtering  

### Power-on states

After **power-on reset**, GPIOs revert to **general-purpose inputs** with **default pulls** applied per the SoC alternate-function / pad tables (**most** GPIOs have a defined default pull). This is why **unconfigured** lines can **float** in an undefined logic state until Linux or firmware configures them — relevant to **MOSFET gates** driven from the header before `RPi.GPIO` runs. See [../../mosfet-off-verification.md](../../mosfet-off-verification.md) and [../../raspberry-pi-gpio.md](../../raspberry-pi-gpio.md).

## Interrupts (when used as GPIO input)

Configurable sources include:

- **Level** (high / low)  
- **Rising / falling edge** (synchronous — small **synchronisation**: stable transition within a **three-cycle** window at system clock)  
- **Asynchronous** rising/falling (narrow pulses)  

Level interrupts stay asserted until software clears the condition.

## Alternate functions (summary)

Not every function is on every pin; common mappings from the official doc:

| Function | Mapping (BCM numbers) |
|----------|------------------------|
| **Software PWM** | Available on **all** GPIO pins (library-dependent) |
| **Hardware PWM** | **GPIO12, GPIO13, GPIO18, GPIO19** |
| **SPI0** | MOSI **10**; MISO **9**; SCLK **11**; CE0 **8**, CE1 **7** |
| **SPI1** | MOSI **20**; MISO **19**; SCLK **21**; CE0 **18**; CE1 **17**; CE2 **16** |
| **I²C (main)** | SDA **GPIO2**; SCL **GPIO3** |
| **I²C ID EEPROM** | Data **GPIO0**; Clock **GPIO1** |
| **Serial** | TX **GPIO14**; RX **GPIO15** |

**Pad control** (drive strength, Schmitt) still applies in alternate functions.

CoilShield defaults: **`PWM_GPIO_PINS = (17, 27, 22, 23)`**, **`LED_STATUS_GPIO = 25`**, optional **`ADS1115_ALRT_GPIO`**, **`REF_I2C_BUS`** bit-bang on **SDA=20, SCL=12** — see `config/settings.py` and README.

## Voltage specifications — BCM2835, BCM2836, BCM2837, RP3A0

*(e.g. Pi Zero, Pi 3+ family — confirm your board in official doc.)*

| Symbol | Parameter | Conditions | Min | Typ | Max | Unit |
|--------|-----------|------------|-----|-----|-----|------|
| VIL | Input low voltage | — | — | — | **0.9** | V |
| VIH | Input high voltage | hysteresis enabled (note a) | **1.6** | — | — | V |
| IIL | Input leakage current | TA = +85 °C | — | — | **5** | µA |
| CIN | Input capacitance | — | — | **5** | — | pF |
| VOL | Output low voltage | IOL = −2 mA (note b) | — | — | **0.14** | V |
| VOH | Output high voltage | IOH = 2 mA (note b) | **3.0** | — | — | V |
| IOL | Output low current | VO = 0.4 V (note c) | **18** | — | — | mA |
| IOH | Output high current | VO = 2.3 V (note c) | **17** | — | — | mA |
| RPU | Pull-up resistor | — | **50** | — | **65** | kΩ |
| RPD | Pull-down resistor | — | **50** | — | **65** | kΩ |

- **a:** Hysteresis enabled.  
- **b:** Default drive strength **8 mA**.  
- **c:** Maximum drive strength **16 mA**.

## Voltage specifications — BCM2711 (Pi 4 series)

| Symbol | Parameter | Conditions | Min | Typ | Max | Unit |
|--------|-----------|------------|-----|-----|-----|------|
| VIL | Input low voltage | — | — | — | **0.8** | V |
| VIH | Input high voltage | hysteresis enabled (note a) | **2.0** | — | — | V |
| IIL | Input leakage current | TA = +85 °C | — | — | **10** | µA |
| VOL | Output low voltage | IOL = −4 mA (note b) | — | — | **0.4** | V |
| VOH | Output high voltage | IOH = 4 mA (note b) | **2.6** | — | — | V |
| IOL | Output low current | VO = 0.4 V (note c) | **7** | — | — | mA |
| IOH | Output high current | VO = 2.6 V (note c) | **7** | — | — | mA |
| RPU | Pull-up resistor | — | **33** | — | **73** | kΩ |
| RPD | Pull-down resistor | — | **33** | — | **73** | kΩ |

- **a:** Hysteresis enabled.  
- **b:** Default drive strength **4 mA**.  
- **c:** Maximum drive strength **8 mA**.

**Design note:** BCM2711 **VOH** min at rated current is **2.6 V**, not **3.0 V** — margin for **logic-level MOSFET** gate drive is tighter than on older Pis; see [../../anode-mosfet-irlz44.md](../../anode-mosfet-irlz44.md) and knowledge base [vishay-irlz44-n-channel-mosfet.md](vishay-irlz44-n-channel-mosfet.md).

## Disclaimer

Text and tables are transcribed from Raspberry Pi’s open documentation for **CoilShield field use**. **Authority** is the **current** upstream file and any SoC-specific datasheets (Compute Module, etc., per original doc).
