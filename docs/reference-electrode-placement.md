# Reference electrode — field placement

ICCP control uses a **third electrode** (reference) to sense electrochemical potential (polarization shift vs a commissioned baseline). For that measurement to be meaningful:

1. **The reference path must not carry anode current.** The reference cell should be electrically positioned so the CP return current does not flow *through* the reference element as if it were part of the anode circuit. In practice: use a dedicated sense node (your Ag/AgCl / zinc / divider front-end) wired only to the high-impedance ADC input, not in series with anode current to the coil.

2. **Physical placement:** Mount the reference **away from anode feed points** and major current paths—e.g. toward the **far end of the drain pan** from embedded anodes—so the local IR field in the electrolyte does not dominate the reading. Document the actual wire route for each install.

3. **Commissioning instant-off:** After PWM cut, the measured potential can show a **short positive spike** (~0.3 s) from inductive/capacitive effects. Firmware waits out the start of the OC burst before inflection analysis (`COMMISSIONING_OC_INFLECTION_SKIP_RATES` × burst interval). Do not shorten that gate without re-validating on hardware.

This complements `docs/iccp-comparison.md` and `docs/iccp-vs-coilshield.md` (standards mapping).
