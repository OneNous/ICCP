# Roadmap — fleet backend vs consumer app (not in this repo)

The on-device controller ([`iccp_runtime.py`](../iccp_runtime.py), [`logger.py`](../logger.py)) is **local-first**: SQLite + `latest.json`, optional Wi-Fi on the Pi for SSH only.

A future product line may add **two separate systems**:

1. **Operator / fleet backend** — Full-rate telemetry, long retention, install validation, warranty, impedance trending. Typical stack: device → **MQTT** or HTTPS → time-series (e.g. Influx) + long-term store (e.g. PostgreSQL), 2+ year retention with hot/cold policy.
2. **Homeowner / facility app** — **Aggregated** status only: protected / not, uptime %, alerts, service CTA. Served from **your** API, not raw per-channel engineering streams.

**Gap vs current firmware:** per-minute schema ideas may include **humidity** and **energy per cycle**; those are **not** first-class in the controller today. Map existing fields (per-channel mA, PWM, impedance, ref, shift, temp, faults) to any uplink contract when you add an **agent** process (separate from the real-time control loop).

**Subscription tiers** (Basic = no cloud, Standard/Premium = uplink + app) are a **business** layer; the ICCP binary should stay runnable with **no network** for Basic.

This file is a **placeholder** for cross-team planning; implementation belongs in other repositories and services.

**Expanded planning pack (ADRs, uplink v1 schema, agent + fleet + BFF + firmware contract):** [iot-dual-system/README.md](iot-dual-system/README.md).
