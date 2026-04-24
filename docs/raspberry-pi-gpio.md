# Raspberry Pi GPIO — CoilShield notes

**Full transcription (voltage tables, power-on behavior, alt functions):** [knowledge-base/components/raspberry-pi-gpio-header.md](knowledge-base/components/raspberry-pi-gpio-header.md)

**Upstream source:** Raspberry Pi Ltd — [`gpio-on-raspberry-pi.adoc`](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/gpio-on-raspberry-pi.adoc) in [raspberrypi/documentation](https://github.com/raspberrypi/documentation).

## Why this matters for this repo

- **`RPi.GPIO`** soft-PWM on **`PWM_GPIO_PINS`** assumes **3.3 V** logic high into the anode **MOSFET** gates — **BCM2711** official **VOH** min at default drive is **2.6 V** (see knowledge base). Treat **gate charge**, **series resistor**, and **part choice** as a **hardware** verification item on **Pi 4 / 5**.  
- **Gate (GPIO) vs anode / INA bus (Vbus):** the Pi only drives the **FET gate** in the **~0–3.3 V** domain. The **switched anode path** and the **INA219** “bus” voltage (what firmware logs as `bus_v` / mV-like proxies) are on the **5 V** supply rail and often read **~4.85–5.0 V** when the FET is on, **not** 3.3 V. Do not confuse **gate–source** drive (capped by GPIO) with the **switched 5 V rail** on the FET/INA side — see [mosfet-off-verification.md](mosfet-off-verification.md) and probe STEP 5 in `hw_probe.py`.
- **Power-on:** GPIOs are **inputs** with **default pulls** until configured — so **gates can float** before any process sets them. See [mosfet-off-verification.md](mosfet-off-verification.md) §0.  
- **I²C:** Header I²C is **SDA = GPIO2**, **SCL = GPIO3** (fixed pull-ups per official doc). Do not reuse CoilShield **PWM** BCM lines for bit-bang I²C without checking `config/settings.py` and README.

## Related

- [mosfet-off-verification.md](mosfet-off-verification.md) — commissioning / gate hold.  
- [anode-mosfet-irlz44.md](anode-mosfet-irlz44.md) — logic-level FET at ~3.3 V (and Pi 4 **VOH** caveat in KB).  
- [knowledge-base/README.md](knowledge-base/README.md) — index of all long-form hardware pages.
