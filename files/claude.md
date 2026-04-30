# CoilShield Firmware — Claude / Cursor Operating Rules

> **You are working in the `coilshield-firmware` repo.** This is the Python firmware that runs on the Raspberry Pi inside every CoilShield device. It is a SEPARATE repo from the main `coilshield` monorepo. Read this file fully, then load the sub-rule file in `.claude/` that matches your task.

## What This Repo Is

Python firmware running on Raspberry Pi 3 (and later Pi 4 / Pi Zero 2 W). Controls the ICCP hardware:

- 4 channels of MOSFET-driven anodes
- Per-channel current sensing via INA3221 (replaces the older INA219 design)
- Reference electrode reading via ADS1115 + Ag/AgCl saturated KCl electrode
- DS18B20 temperature sensor
- WiFi connectivity to Supabase backend
- BLE advertising for tech-app provisioning
- Local Flask HTTP server for tech-app commissioning after WiFi is up

This is the most safety-critical part of the system. The polarization hard cutoff lives here. If this code malfunctions, real customer coils get damaged. Treat every change with that in mind.

## Repo Layout

```
coilshield-firmware/
├── src/
│   ├── main.py                      // Entry point, supervisor loop
│   ├── control.py                   // Per-channel FSM + safety cutoff
│   ├── sensors.py                   // INA3221 + simulator
│   ├── reference.py                 // ADS1115 + Ag/AgCl
│   ├── commissioning.py             // Self-commissioning sequence
│   ├── temp.py                      // DS18B20 reading
│   ├── logger.py                    // SQLite + CSV + JSON sinks
│   ├── leds.py                      // Status LED control
│   ├── dashboard.py                 // Local Flask web dashboard
│   ├── iccp_cli.py                  // Single `iccp` CLI entry
│   ├── pi_edge/                     // BLE Wi‑Fi, MQTT, HTTPS register (optional extras)
│   ├── ble_provisioning.py          // Thin entry → pi_edge.ble_provision
│   ├── cloud_sync.py                // Supabase push placeholder (TODO)
│   ├── tech_api.py                  // Tech-app Flask routes placeholder (TODO)
│   └── generated/
│       └── schema_types.py          // Monorepo codegen placeholder (read-only when generated)
│   (+ iccp_runtime, hw_probe, tui, diagnostics, … — see pyproject.toml py-modules)
├── config/
│   └── settings.py                  // Tunables, addresses, thresholds (+ argv_*.py)
├── schemas/                         // Synced manually from coilshield/schemas/
├── static/                          // Dashboard vendored fonts (repo root)
├── deploy/                          // Extra systemd examples (sidecars, bootstrap)
├── tests/                           // Unit tests
├── systemd/
│   └── coilshield.service           // Canonical controller unit example
├── claude.md                        // This file
├── .claude/                         // Sub-rule files
├── README.md
└── requirements.txt
```

## How to Use This Document

Read this hub first. Then read sub-files based on the task:

| Task | Files to load |
|---|---|
| Fresh agent, no context | This → `.claude/onboarding.md` → recent `docs/DECISIONS.md` |
| Anything touching the safety cutoff | `.claude/safety-and-control.md` (mandatory) |
| Sensor reading, sensor drivers | `.claude/sensors-and-hardware.md` |
| Cloud sync to Supabase | `.claude/cloud-sync.md` → `.claude/schemas.md` |
| BLE provisioning | `.claude/ble-provisioning.md` |
| Local HTTP API for tech app | `.claude/tech-api.md` |
| Schema changes | `.claude/schemas.md` |
| Deployment, systemd, OS config | `.claude/deployment.md` |
| Anything else | `.claude/cross-cutting.md` |

You do not need to load all sub-files. Read what's relevant.

## The Five Rules That Override Everything

These apply to every change. If a sub-rule contradicts these, these win.

1. **Polarization hard cutoff at −1080 mV vs Ag/AgCl is a safety law, not a config option.** It is enforced every control cycle. No code path may bypass it. If you find yourself wanting to disable it for testing, use `COILSHIELD_SIM=1` simulator mode or a coupon test rig — never disable it on real hardware connected to a real coil.

2. **Schemas in `schemas/` are synced from the monorepo, NOT authored here.** Any schema change happens in `coilshield/schemas/` first. Then synced here. Then types regenerated via `codegen/`. See `.claude/schemas.md`.

3. **The control loop must keep running even if everything else fails.** Cloud sync down? Keep controlling. WiFi gone? Keep controlling. Logger errors? Keep controlling. The control loop is sacred — only the safety cutoff stops it.

4. **No `print()` for production logs. Use `logger.py`.** systemd captures stdout but it's unstructured. The logger writes to four sinks (SQLite, latest.json, CSV, fault.log) which are queryable and useful.

5. **The device must work without the cloud.** If Supabase is down, the device protects the coil. If WiFi is down, the device protects the coil. If the tech app is uninstalled, the device protects the coil. The cloud is a reporting layer, not a control layer.

## When You Make A Decision

Architectural decisions log to `docs/DECISIONS.md` in this repo (parallel to the monorepo's). Date, who/what made the call, reasoning, consequences. Don't skip this — the next agent has no memory.

## Validation Phase Status

We are in **validation phase**. Building 10 working units. Testing on real coils for 30 days. Don't:

- Add features not in the validation roadmap
- Refactor existing working code without explicit approval
- Add new dependencies casually
- Build for hypothetical future hardware revisions

If a request seems out of scope, push back before doing it.

## Reference Documents in This Repo

- `docs/ARCHITECTURE.md` — how the firmware is structured internally
- `docs/DECISIONS.md` — running log of choices
- `docs/HARDWARE.md` — pin assignments, I2C addresses, wiring diagrams
- `docs/CALIBRATION.md` — bench calibration procedures
- `schemas/` — copies of monorepo schemas (manually synced)
- `coilshield-session-summary.md` — historical record of what's been built (reference only, not actively maintained)

## When You Need Context From the Monorepo

This repo is standalone but the system isn't. The monorepo (`coilshield/`) has:

- Canonical schemas (we sync, we don't author)
- Architecture docs covering how surfaces connect
- The decision log for cross-surface decisions
- Sub-rule files for the apps and command center

If a task requires understanding how the device interacts with the apps or backend, you may need to look at the monorepo's docs. The owner has that repo locally and can paste relevant context.
