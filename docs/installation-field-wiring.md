# Field installation — CoilShield ICCP (HVAC coil)

This document matches the product field story: **single 5 V USB** supply, **cathode bond outside the condensate path**, and **verifiable** commissioning. For electrochemical background and NACE shift criterion, see [iccp-requirements.md](iccp-requirements.md) and [iccp-vs-coilshield.md](iccp-vs-coilshield.md).

## Power

- **One** 5 V USB power source for the controller (Raspberry Pi + logic + anode drive). No separate 12 V rail is required for the product architecture described in settings (`MIN_BUS_V` / `MAX_BUS_V` bracket typical USB bus sag).

## Cathode return (bond) — critical

**The bond wire (cathode return to system GND) must be on dry metal *outside* the drain-pan / condensate electrolyte.**

If the return is submerged in the same water as the anodes, current can shunt through bulk water to the bond, **bypassing the fin metal**, and you will not see real polarization on the aluminum.

**Typical good bond (residential / split system):** the **copper liquid line** where it exits the air handler into the mechanical room—dry, accessible, and electrically continuous with the coil circuit through the refrigerant path. Do **not** clip the bond inside the drain pan or to a path that is continuously bathed in condensate if that creates a “short” return that skips the fin surface.

MMO anodes go to the fin side through a **non-conductive** wicking path (e.g. sponge) so there is **no** metal-to-metal short between anode and cathode; see your mechanical design docs.

## Reference electrode

- **Tip position:** close to the **cathode (fin) surface** in the condensate film (on the order of a few millimetres) to limit uncorrected IR in the *steady* read. Also route the sensor **away from dense anode feed current** in the pan so local IR from drive current does not dominate. Both goals are compatible: *near the fin for geometry*, *not in the highest current-density plume if avoidable* — see [reference-electrode-placement.md](reference-electrode-placement.md).

- **Signal:** shielded or twisted run to the ADS1115; tie shield to GND at the controller. Commissioning uses **instant-off** to strip IR from the *off-transient* measurement; see [config/settings.py](../config/settings.py) `COMMISSIONING_OC_*`.

## Commissioning — Phase 1 (native) and galvanic

With MMO or graphite in **bulk electrolyte**, appreciable **galvanic** current can flow even with MOSFETs off, corrupting the “native” baseline. For Phase 1, **remove anodes from the electrolyte** (or otherwise guarantee no external anodic current) while capturing native, as required by your bench procedure.

## Operator CLI (canonical)

The supported entry points are **subcommands** (see [iccp_cli.py](../iccp_cli.py)):

```bash
# Run the controller (after venv / install)
iccp start

# First-time or re-commission (writes commissioning.json in project root)
iccp commission
```

Do **not** rely on `iccp -start` or `iccp --commission` — those forms are not the defined interface.

**Clear fault latch (if used):** touch the file set in `config.settings.CLEAR_FAULT_FILE` (default: project `clear_fault`).

## Boot on a Raspberry Pi

Use the example unit in [deploy/iccp.service](../deploy/iccp.service): adjust `User`, `WorkingDirectory`, and `ExecStart` to your venv path. Set `COILSHIELD_LOG_DIR` if you want a fixed absolute telemetry directory for dashboards.

## I²C / mux

Production units use **no TCA9548A**: all INA219 and the ADS1115 share the Pi bus with **unique** 7-bit addresses. Keep `I2C_MUX_ADDRESS = None` and related mux fields `None` in [config/settings.py](../config/settings.py). See [ina219-i2c-bringup.md](ina219-i2c-bringup.md) for address map and probe flow.

## Telemetry meaning of “impedance”

Per-channel `Z ≈ Vbus / I` in logs is a **bench electrical** diagnostic, not the same as textbook field “anode-to-earth” resistance. See [field-ra-and-telemetry.md](field-ra-and-telemetry.md).
