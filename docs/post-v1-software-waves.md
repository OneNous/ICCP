# Post–v1 software waves (fleet / follow-on)

v1 targets first-install reliability: shunt + commissioning (**Algorithm D**: `COMMISSIONING_RAMP_MODE=hybrid`), **Algorithm A** feedforward, SQLite batching, watchdog/exit failsafe, **`commissioning_complete`**, and diagnostic **temperature** on wet/dry **stats-protection** transitions in `latest.json` (`wet_onset_temp_f` / `dry_onset_temp_f`). The items below are **intentionally deferred**; they stay valid as small follow-on PRs.

| Ref   | Item |
|-------|------|
| R2    | Adaptive settle in commissioning binary / ramp (`_pump_control`); behind flags, conservative fallbacks. |
| R4    | Per-channel wet-onset EMA and richer logging (JSON/CSV first; optional DB later). |
| R5    | Adaptive outer loop / instant-off interval; align with `ReferenceElectrode` and protection timing. |
| R6    | C_dl and electrolyte quality index (EQI) nudges. |
| C     | Health composite refinements and optional nudges. |
| R7    | **Field** reference temperature self-cal over days (100+ samples, 15 °F span) — see [field-temp-comp-selfcal.md](field-temp-comp-selfcal.md) — *not* Phase 1 idle T_RELAX regression. |

Condensate *prediction* as a **control** path is out of scope; temperature on transitions is **diagnostics only**.
