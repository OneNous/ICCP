# CoilShield Roadmap (AI Reference)

> **Audience:** AI agents (Claude, Cursor) needing to understand current project state and what to do next. **Format:** Dense, structured, parseable. **Companion:** `coilshield-production-roadmap.md` is the human-readable version with prose explanations. This file is for agents.

## Project State (One-Line Summary)

CoilShield is in **validation phase**. Goal: 10 ICCP units installed in real HVAC evaporator coils, running for 30 days, with zero coil damage. Pre-LLC. Pre-revenue. Pre-patent.

## Repo Structure

```
coilshield/                  → MONOREPO (TS apps + docs + schemas)
coilshield-firmware/         → SEPARATE REPO (Python on Pi)
```

## Phase Status

| Phase | Status | Gate |
|---|---|---|
| 0. Foundation (monorepo, backend, schemas) | NOT STARTED | Schemas defined, monorepo set up |
| 1. End-to-end plumbing | NOT STARTED | Smoke test: device → backend → both apps → command center |
| 2. 10-unit hardware build | NOT STARTED | All 10 units bench-validated |
| 2b. Aluminum coupon test | NOT STARTED | 7-day test with no etching |
| 2c. Field deployment | NOT STARTED | All 10 installed via tech app |
| 2d. 30-day monitoring | NOT STARTED | All 10 stable, no coil damage |
| 3. Pre-launch (LLC, insurance, patent) | GATED | Validation must pass first |
| 4. Sacrificial anode product | DEFERRED | Wave 2 |
| Client app | DEFERRED | Phase 3 |
| Website | DOWN | Until validation passes |

## Priority Order (Top-to-Bottom = Do First-to-Last)

### 1. Monorepo Structure
- New repo `coilshield`, pnpm workspaces, apps/, packages/, docs/, schemas/
- Migrate existing skeletons: tech-app-ios (Swift), tech-app-android (Flutter), command-center (will be rewritten in Swift), website (existing, leave alone)
- Set up `.claude/` directory with rule files (DONE — this file is part of that work)
- Initial DECISIONS.md log

### 2. Backend (Supabase)
- Create dev + prod Supabase projects
- Define schemas in `coilshield/schemas/*.sql`: devices, installations, readings, commissioning_runs, events, users
- Enable RLS on all tables
- Generate TS types via Supabase CLI
- Codegen Swift, Dart, Python types from same SQL

### 3. ICCP Device (Firmware)
- Lives in `coilshield-firmware` repo
- Already substantially built: control loop, sensors (INA3221), simulator, logger, dashboard, commissioning, temp sensor
- TODO: cloud sync to Supabase, BLE provisioning, tech HTTP API
- Hard cutoff at −1080 mV vs Ag/AgCl is non-negotiable safety law
- Smoke test: device pushes reading visible in Supabase within 60s

### 4. Tech App (iOS — Swift) AND Tech App (Android — Flutter)
- Built in PARALLEL — feature parity required
- iOS: Swift / SwiftUI / iOS 16+
- Android: Flutter / Dart / Riverpod / Android 10+ (API 29)
- Both use BLE provisioning then local HTTP for commissioning
- Both submit installation reports + photos to Supabase
- Both distribute via TestFlight (iOS) / Play Console internal testing (Android)
- Smoke test: complete commissioning, see record + photos in Supabase, on BOTH platforms

### 5. Command Center (Swift macOS)
- Lives in `coilshield/apps/command-center/`
- Swift / SwiftUI / macOS 13+
- Single-window app, single user (the owner)
- Realtime subscriptions to readings + events tables
- The Electron version is DEAD — do not maintain
- Smoke test: tech app submits commissioning, see in command center within 5 seconds

### 6. Field Deployment (10 Real Installs)
- After all smoke tests pass
- Identify 5-10 friendly hosts (HVAC contractors or homeowners willing to host experimental units)
- Each tester signs research-prototype acknowledgment (no money, no warranty, can remove anytime)
- Stagger installs so all 10 don't end at the same time
- Monitor for 30 days
- Validation pass criteria (binary):
  - All 10 ran 30 days without coil damage
  - Polarization stayed in window ≥80% of wet hours
  - Tech app commissioned ≥8 of 10 first-try
  - <10% data loss to backend
  - No critical bugs in command center

### 7. Website (Three-Stage Launch)
- Stage 1: stays down through validation
- Stage 2: waitlist page after Phase 1 smoke tests pass (single page, email capture, no engineering details)
- Stage 3: full launch only after LLC + insurance (Phase 3 only)
- ENGINEERING DETAILS NEVER GO ON THE PUBLIC SITE — patent IP exposure

### 8. Legal & Business (Phase 3 Only)
- Triggered ONLY after validation passes
- LLC formation ($50-500)
- EIN ($0)
- Business bank account
- Provisional patent application ($75-300, micro-entity)
- Trademark search + filing ($250-350)
- Product liability insurance ($500-2000/yr)
- Terms of Service + Privacy Policy
- Reseller / distributor agreement template
- Customer warranty document

### 9. Client App (Phase 3 Only)
- React Native + Expo, same monorepo
- Read-only views for homeowners
- Sign up + link to device serial
- View device status, wet event history, alerts
- Submit to App Store + Play Store (1-2 review cycles)

### 10. Sacrificial Anode Product (Wave 2)
- Separate product, separate timeline
- Don't tackle until ICCP is selling
- Probably extruded magnesium ribbon (commercial, low MOQ) not custom casting
- Skip until validation generates revenue

## Tech Stack Summary

| Surface | Language | Framework | Native APIs |
|---|---|---|---|
| Tech app iOS | Swift | SwiftUI | CoreBluetooth, Keychain, AVFoundation |
| Tech app Android | Dart | Flutter + Riverpod | flutter_blue_plus, flutter_secure_storage |
| Command center | Swift | SwiftUI | CoreBluetooth, Keychain, Supabase Realtime |
| Device firmware | Python | bare CPython + Flask | RPi.GPIO, smbus2, bless |
| Backend | (managed) | Supabase | n/a |
| Website | (existing) | (existing — don't touch) | n/a |

## Hard Rules (Override Everything)

1. Schemas in `coilshield/schemas/` are source of truth. All other types are generated.
2. iOS and Android tech apps maintain feature parity. Done = both work.
3. Polarization hard cutoff at −1080 mV vs Ag/AgCl is enforced in firmware. Cannot be bypassed.
4. No money during validation. Period.
5. No public engineering disclosure until provisional patent filed.
6. Electron command center is dead. Do not maintain.

## Critical Safety Numbers

| Parameter | Value |
|---|---|
| Aluminum protection target | −1019 mV vs Ag/AgCl (3M KCl) |
| Aluminum protection window (safe) | −969 to −1069 mV vs Ag/AgCl |
| Pitting threshold (less negative = unprotected) | −969 mV vs Ag/AgCl |
| Alkaline etching threshold (more negative = damage) | −1069 mV vs Ag/AgCl |
| Hard cutoff (firmware-enforced) | −1080 mV vs Ag/AgCl (11 mV margin) |
| Floor warning (sustained underprotection) | −900 mV vs Ag/AgCl |
| Reference electrode | Ag/AgCl, 3M KCl filling, Stonylab 6×65mm |
| Native potential vs zinc reference (legacy) | +174 mV (steel cathode bench) |
| Polarized potential vs zinc (legacy) | −670 mV |
| Bench-confirmed protection shift | 844 mV (8.4× NACE 100 mV minimum) |
| Target current per channel | 0.5 mA initial, outer loop adjusts |
| Hard current cap | 5.0 mA per channel |
| Inner loop interval | 0.5 s |
| Outer loop interval | 60 s |
| Wet detection threshold | 0.15 mA |
| Probe duty / duration | 3% / 2 s every 60 s |

## Validation Phase Budget

| Item | Cost | Status |
|---|---|---|
| Supabase free tier | $0 | TODO |
| 10 ICCP units (parts) | $500-1500 | TODO |
| Project boxes | $50-150 | TODO |
| Cable assemblies | $50-100 | TODO |
| Apple Developer | $0 (already owned) | DONE |
| Google Play Developer | $25 (optional, iOS-only validation OK) | TODO |
| Aluminum fin stock for coupon test | $20 | TODO |
| Real coil for destructive test | $200-500 (or borrow) | TODO |
| Reference electrodes (extras) | $100-200 | 1 of 2 owned |
| **Total validation cost** | **$1,000-2,500** | |

## Hardware Status (As of 2026-04-29)

Confirmed working:
- Raspberry Pi 3 on WiFi
- 4× INA219 at addresses 0x40, 0x41, 0x44, 0x45 (legacy — being migrated to 2× INA3221)
- 4× IRLZ44N MOSFETs
- DS18B20 temperature sensor (76°F room temp confirmed)
- Control loop running on real hardware (PROBING → PROTECTING → duty ramping)
- All 4 channels protecting simultaneously in tap water test (0.5 mA per channel)
- `main.py --real --verbose --skip-commission` running cleanly
- Ag/AgCl saturated KCl reference electrode (Stonylab) installed

In progress / planned:
- Migration from 4× INA219 to 2× INA3221 (cleaner I2C)
- ADS1115 wired to reference electrode (board on bench, not yet wired)
- BLE provisioning (TODO — not yet implemented)
- Cloud sync to Supabase (TODO — not yet implemented)
- Local tech HTTP API (TODO — not yet implemented)

## Multi-Surface Workflows (Reference)

### Commissioning End-to-End

1. Tech app discovers device via BLE
2. Tech app pairs with device
3. Tech app reads WiFi networks via BLE
4. Tech app writes WiFi credentials via BLE
5. Device joins WiFi, registers with Supabase
6. Device stops BLE advertising
7. Tech app finds device via mDNS on local WiFi
8. Tech app POSTs `/commission` to device's local HTTP
9. Device runs commissioning sequence (ramp current until polarization in window)
10. Device pushes commissioning_runs record to Supabase
11. Tech app captures installation photos
12. Tech app POSTs installation report (with photo refs) to Supabase
13. Tech app uploads photos to Supabase Storage
14. Device continues pushing readings to Supabase
15. Command center sees new install + readings within 5 seconds (Supabase Realtime)

### Schema Change End-to-End

1. Update SQL in `coilshield/schemas/*.sql`, bump version
2. Apply via Supabase migration (`supabase db push`)
3. Run `pnpm run codegen:all` in monorepo
4. Verify generated files updated: Swift x2, Dart, TypeScript
5. Update consumers in iOS, Android, command center
6. Manually copy SQL files to firmware repo's `schemas/`
7. Run `python codegen/gen_python.py` in firmware repo
8. Update firmware consumers
9. Test each surface independently
10. Run end-to-end smoke test
11. Log change in `docs/DECISIONS.md` (both repos)

### Fault Detection End-to-End

1. Firmware detects fault (overcurrent, sensor error, polarization cutoff, etc.)
2. Channel transitions to FAULT state, gates off
3. Local fault.log entry written
4. Event row pushed to Supabase events table
5. If severity = critical, edge function `send-fault-email` triggers
6. Owner receives email
7. Command center Realtime subscription catches the new event
8. Command center triggers system notification (macOS)
9. Owner reviews fault, decides on response
10. If transient: auto-recovery after 60s (up to 3 times)
11. If latched (cutoff fired): manual clear required via `/clear-fault` endpoint or SSH

## Decision Triggers (When to Pause and Reconsider)

These events should pause work and force a re-evaluation:

- Coupon test shows etching of any kind → STOP, debug control, re-test
- Any test unit causes detectable change in coil performance (cooling drop, refrigerant leak, ice formation) → pull all units, investigate
- Reference electrode reading stops making sense (positive during wet, drift >100 mV/week) → replace electrode
- More than 2 of 10 units fail in 30 days → diagnose common failure mode before continuing
- A tester mentions hearing of a similar product → research, may be racing competitor
- A tester wants to pay → polite no, validation rules forbid money

## Operating Vocabulary (Project-Specific)

- **The cutoff** — polarization hard cutoff at −1080 mV
- **The window** — aluminum protection range, −969 to −1069 mV
- **The chain** — device → backend → app → command center
- **Validation phase** — pre-LLC, pre-revenue, 10 units in real coils
- **Wave 1 / 2 / 3** — phases of commercialization (post-validation)
- **The bench** — owner's test rig with simulated electrolyte
- **Coupon test** — small fin sample, safe testing surface
- **Real coil** — actual aluminum-fin/copper-tube evaporator (only after coupon tests pass)
- **The spine** — docs in monorepo (`docs/`, `schemas/`, `.claude/`)
- **Wet event** — period when condensate is present and a channel is actively protecting
- **Probing** — brief 3% duty pulse to detect re-wetting on dormant channels

## Files Cross-Reference

In monorepo (`coilshield/`):
- `claude.md` — main hub
- `.claude/onboarding.md` — fresh agent guide
- `.claude/native-platforms.md` — three-codebase strategy
- `.claude/mobile-ios.md` — Swift iOS rules
- `.claude/mobile-android.md` — Flutter Android rules
- `.claude/parallel-mobile.md` — feature parity coordination
- `.claude/desktop-app.md` — Swift macOS rules
- `.claude/firmware.md` — Python Pi rules (cross-reference; canonical is in firmware repo)
- `.claude/schemas-and-data.md` — codegen pipeline
- `.claude/backend.md` — Supabase rules
- `.claude/ble-provisioning.md` — GATT protocol
- `.claude/cross-cutting.md` — multi-surface concerns
- `.claude/architecture-and-decisions.md` — DECISIONS.md discipline
- `docs/ROADMAP.md` — this file (or copy thereof)
- `docs/ARCHITECTURE.md` — technical "how it fits"
- `docs/DECISIONS.md` — running decision log
- `docs/GLOSSARY.md` — project vocabulary
- `docs/ENV_VARS.md` — environment variable reference
- `schemas/*.sql` — canonical data model
- `codegen/*` — type generation scripts

In firmware repo (`coilshield-firmware/`):
- `claude.md` — firmware repo hub
- `.claude/onboarding.md` — fresh agent guide
- `.claude/safety-and-control.md` — control loop + cutoff rules (CRITICAL)
- `.claude/sensors-and-hardware.md` — sensor drivers
- `.claude/cloud-sync.md` — Supabase push
- `.claude/ble-provisioning.md` — GATT peripheral implementation
- `.claude/tech-api.md` — local Flask HTTP API
- `.claude/schemas.md` — schema sync from monorepo
- `.claude/deployment.md` — systemd, OS, install procedure
- `.claude/cross-cutting.md` — firmware-internal concerns
- `coilshield-session-summary.md` — historical reference (not maintained)
- `schemas/*.sql` — synced from monorepo manually
- `src/generated/schema_types.py` — generated from schemas

## Last Updated

2026-04-29 — initial AI-optimized version
