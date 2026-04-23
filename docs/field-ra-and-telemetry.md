---
title: Field anode resistance (R_a) vs. logged impedance
description: Operator-facing one-pager — textbook/field R_a (Dwight, Sunde, etc.) is not computed or implied by this firmware; logs use electrical V/I.
topics: [cathodic-protection, anode, R_a, impedance, operators, documentation]
---

# Field \(R_a\) and CoilShield telemetry

**Stable link (for runbooks and PRs):** `docs/field-ra-and-telemetry.md` in this repository.

This page exists for **clarity** and a **durable anchor** when operators or reviewers ask how “anode resistance” in cathodic-protection textbooks relates to the numbers in **`latest.json`**. It does **not** add calculators, heuristics, or hidden estimates.

## Two different quantities

| | **Field / design \(R_a\)** | **Logged `chN_impedance_ohm` (this firmware)** |
| --- | --- | --- |
| **What it is** | Low-frequency **anode-to-electrolyte** resistance used in **pipeline, offshore, and buried** design: resistivity \(\rho\), geometry, stand-off, ground beds, anode **groups**, life/consumption. | **`bus_v / I`** on the **driven electrical branch** (INA219 shunt path) — a **bench/coil** diagnostic, same definition as in [`logger`](../logger.py) and [iccp-vs-coilshield.md](iccp-vs-coilshield.md). |
| **Where the names show up** | Textbooks and standards often cite **closed-form** models (e.g. **Dwight**-style single anodes, **Sunde** for **multiple** vertical anodes) in **consistent units** — for **amp-level** field sizing and life checks. | **Not** those formulas. The code does not implement Dwight, Sunde, or any other **soil/water** resistance model. |
| **“From settings”** | \(R_a\) is **not** read from `config/settings.py` or `commissioning.json`. Those files hold **targets, limits, and hardware mapping** — not a precomputed field \(R_a\). | Derived each tick from **measured** bus V and shunt current. |

## What this project deliberately does *not* do

- **No** Python (or other) **Dwight/Sunde calculator** in-repo — use your **standard, licensed handbook, or calc sheet** for field work.
- **No** **auto-estimate** of “field” \(R_a\) from firmware settings or logs — that would **blur** an industry design number with a **coil shunt** metric and invite misuse.

For **bibliography, ScienceDirect topic links, and a longer CP vs noise glossary**, use [knowledge-base/anode-resistance-cp-context.md](knowledge-base/anode-resistance-cp-context.md). For **code-level mapping** to a “standard ICCP” write-up, use [iccp-vs-coilshield.md](iccp-vs-coilshield.md).

**Disclaimer:** This file is not a substitute for qualified CP design or applicable codes; it only separates **terminology** and **scope** for this repository.
