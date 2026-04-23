---
title: Anode resistance — field CP vs CoilShield telemetry
description: Topic-summary and ScienceDirect link inventory for anode-to-electrolyte resistance, distinct from in-repo electrical Z = V / I.
topics: [cathodic-protection, anode, resistivity, ICCP, ScienceDirect, CoilShield, impedance]
---

# Anode resistance (CP context) and CoilShield

**Short operator one-pager** (field \(R_a\) vs logged Ω, no calculators in-repo): [field-ra-and-telemetry.md](../field-ra-and-telemetry.md).

This page **does not** reproduce Elsevier or ScienceDirect prose. It summarizes **concepts** commonly found under the topic *Anode resistance* in cathodic protection (CP) and records **outbound links** to [ScienceDirect Topics: Anode resistance](https://www.sciencedirect.com/topics/engineering/anode-resistance) (optional fragment [`#recommended-publications`](https://www.sciencedirect.com/topics/engineering/anode-resistance#recommended-publications) scrolls to the journal list on the same page).

Use it to separate **industry** “anode resistance in the electrolyte” from the **`impedance_ohm` column** in this repository’s logs (see [iccp-vs-coilshield.md](../iccp-vs-coilshield.md) and the appendix here).

## 1. Three different meanings of “anode resistance”

| Meaning | Typical domain | Idea |
| -------- | -------------- | ---- |
| **A. Small-signal (electronics)** | Thermionic / valve amplifiers | A ratio like \(\Delta V_a / \Delta I_a\) on the *anode* of a tube, grid fixed. The same topic hub may surface a **non-CP** chapter excerpt. |
| **B. Anode to electrolyte / “earth” (field CP)** | Pipelines, offshore, buried structures | A **low-frequency** resistance between the **ground bed (or anode package)** and the **bulk** soil or seawater. It is one part of a **path** in series with cable, connections, and (sometimes ignored in rough work) structure-to-electrolyte. Geometry (stand-off vs flush, single vs group), **electrolyte resistivity** \(\rho\), backfill, and anode **consumption** (shrinking size, higher \(R_a\)) all matter. Textbooks often use named formulas (e.g. Dwight-style, Sunde for groups) in consistent units. |
| **C. In-repo `impedance_ohm` (this firmware)** | Bench / CoilShield INA | **`bus_v / I`** from a shunt channel—an **electrical** path metric for the driven branch (see `logger` / [iccp-vs-coilshield.md](../iccp-vs-coilshield.md)). It is **not** a substitute for a design \(R_a\) from \(\rho\) and anode shape in infinite seawater, and it is not a structure potential survey. |

## 2. Synthesis: what the ScienceDirect *hub* is useful for (CP branch)

The topic page aggregates **snippets** from several books. In plain language, the **CP** thread usually covers:

- **Resistivity \(\rho\)** of soil or water spans many orders of magnitude. High \(\rho\) (e.g. dry rock) implies **higher** path resistance and, for a given available driving potential, **less** anode current unless design compensates.
- **Stand-off vs flush**, **distance** to the *protection object*, and **anode type** (vertical, horizontal, group, bracelet, plate) change which **closed-form** or empirical resistance model applies. Minimum clear distances and correction factors appear in standards-derived texts; follow your chosen standard, not a forum summary.
- **Total path**: anode–electrolyte, **wiring**, and sometimes a separate **structure** term; in **sacrificial** design, cable and connections may still be small but are not always negligible.
- **Output current** is often reasoned with **Ohm’s law** using the difference between anode and structure (polarized) potentials, divided by **total** series resistance (definitions and sign conventions are book-specific).
- **Life and utilization**: anodes **consume**; a typical **utilization** factor in training examples leaves material before resistance rises catastrophically. Recalculate \(R_a\) (and output) for **worn** dimensions when doing life analysis.
- **Stray / interaction** current: other metallic structures in the same electrolyte can **pick up or return** current (“interaction”); design and test practices exist to limit harm.

**Brackish or fresh water** often has **higher** \(\rho\) than open ocean, so the **same** physical anode can **deliver less** current in those environments unless potential or geometry changes—this is standard CP design reasoning, not a firmware bug.

## 3. Chapters and books (links only — open with institutional access if paywalled)

These **PII URLs** are the ones the topic hub **embeds**; full chapter HTML may return **HTTP 403** to automated clients or require sign-in. Treat them as **bibliography**, not as copyable text in this repo.

| Chapter (short title) | Book (year) | Link |
| --------------------- | ----------- | ---- |
| Corrosion Protection (offshore stand-off/flush anode *R* formulas) | *Offshore Structures* (2012) — M. A. El-Reedy | <https://www.sciencedirect.com/science/article/pii/B9780123854759000067> |
| Cathodic Protection of Pipelines (anode to earth, Dwight, horizontal, groups, life) | *Handbook of Environmental Degradation of Materials* **2e** (2012) — Popov & Kumaraguru | <https://www.sciencedirect.com/science/article/pii/B9781437734553000250> |
| Design Considerations… (resistivity, semi-infinite electrolyte, interaction) | *Cathodic Corrosion Protection Systems* (2014) — A. Bahadori | <https://www.sciencedirect.com/science/article/pii/B978012800274200003X> |
| Proactive approach to integrity (anode *R* at initial vs consumed size) | *Asset Integrity Management for Offshore and Onshore Structures* (2022) — M. A. El-Reedy | <https://www.sciencedirect.com/science/article/pii/B9780128245408000065> |
| *Cathodic Protection of Pipelines* (total circuit, third edition) | *Handbook of Environmental Degradation of Materials* **3e** (2018) — Popov & Lee | <https://www.sciencedirect.com/science/article/pii/B9780323524728000253> |
| *Subsea Tree Design* (anode distribution, spacing, shielding) | *Subsea Engineering Handbook* **2e** (2019) — Y. Bai & Q. Bai | <https://www.sciencedirect.com/science/article/pii/B9780128126226000257> |

Other **non-ICCP** extracts on the same topic page (e.g. **microbial fuel cells**, **SOFC** anode current collection, **valve amplifiers**) illustrate why **“anode resistance”** is **polysemous**; they are listed in the [appendix inventory](#appendix-a-link-inventory) as **unrelated** to field ICCP for CoilShield’s purposes.

## 4. “Related terms” and “Recommended publications” on the hub

### Related terms (footer of the topic page)

ScienceDirect links a **Related terms** list (e.g. Cathodic Protection, Sacrificial Anode, Bracelet Anode, Cathodic Protection System, Backfill Material, Coating Breakdown Factor, power sources, and electrochemical device topics). Those are **other topic pages**—each has its own graph. This repo only **points** to them; see the inventory for CP-relevant `topics/…` URLs that appear **inside** the CP excerpts on the anode-resistance page.

### Recommended publications

The same page’s **Recommended publications** block points at **broad** journals (e.g. *Journal of Power Sources*, *International Journal of Hydrogen Energy*, *Applied Energy*, *Energy Conversion and Management*). They are **not** dedicated CP handbooks; they are generic **energy and electrochemical devices** venues. For **CP anode design**, prefer the **book chapters** in §3 and your project’s standards (NACE/ISO/DNV, etc.).

- Journal of Power Sources: <https://www.sciencedirect.com/journal/journal-of-power-sources>  
- International Journal of Hydrogen Energy: <https://www.sciencedirect.com/journal/international-journal-of-hydrogen-energy>  
- Applied Energy: <https://www.sciencedirect.com/journal/applied-energy>  
- Energy Conversion and Management: <https://www.sciencedirect.com/journal/energy-conversion-and-management>  

## 5. How this maps to CoilShield

- **Field \(R_a\)** from \(\rho\), anode size, and placement is used in **industry** to size **anodes, rectifiers, and cable** and to predict **I** in **A**–class circuits. This repo’s **regulator** targets **mA** per channel from **`TARGET_MA`**, using **shunt** feedback—not a precomputed **\(R_a\)** from soil maps.
- Logged **Ω** and **V\_cell = bus × duty** help operators see **conduction and drive level**; they are **not** a certificate that a **CUI coupon** is polarized to **N** mV vs Ag/AgCl in a standard cell geometry.
- For a deeper **implementation** comparison, start at [iccp-vs-coilshield.md](../iccp-vs-coilshield.md). Reference **placement** and scaling are separate: [reference-electrode-placement.md](../reference-electrode-placement.md).

## Tier 2 check (one hop, CP)

The topic page <https://www.sciencedirect.com/topics/engineering/cathodic-protection> (linked from the hub) was **HTTP 200** with a normal browser `User-Agent` in an automated `curl` check on 2026-04-23. Content was **not** pasted here; it remains on Elsevier’s site. Use it as the **parent** topic for *CP*; do not recurse through every “Related terms” on that page in this document.

---

## Appendix A. Link inventory

**Legend — relevance:** `CP` = field cathodic protection; `ICCP-adj` = same electrolyte, useful vocabulary; `Noise` = other disciplines using the same words; `Nav` = site navigation / login. **Access:** from this environment, **Topic** pages often return **200**; **book chapter** `…/science/article/pii/…` URLs may return **403** to unauthenticated or automated clients (Cloudflare / institutional gate)—open in a **browser** with your subscription.

**A. Root**

| URL | Type | Relevance | Access / notes |
| --- | ---- | -------- | -------------- |
| <https://www.sciencedirect.com/topics/engineering/anode-resistance> | Topic hub | CP + Noise | 200 (curl) |
| <https://www.sciencedirect.com/topics/engineering/anode-resistance#recommended-publications> | Same, fragment | Journals list | N/A (client-side scroll) |

**B. Book / chapter and book landing (unique PII and ISBN paths)**

| URL | Type | Relevance | Access / notes |
| --- | ---- | -------- | -------------- |
| <https://www.sciencedirect.com/science/article/pii/B9780080966403000022> | Chapter (valve amps) | Noise (tube anode) | 403 in curl; browser |
| <https://www.sciencedirect.com/science/article/pii/B9780123854759000067> | Chapter (offshore *R* formulas) | CP | 403 in curl; browser |
| <https://www.sciencedirect.com/book/9780123854759/offshore-structures> | Book | CP | check in browser |
| <https://www.sciencedirect.com/science/article/pii/B9781437734553000250> | Chapter (Popov 2e pipelines) | CP | 403 in curl; browser |
| <https://www.sciencedirect.com/book/9781437734553/handbook-of-environmental-degradation-of-materials> | Book | CP | |
| <https://www.sciencedirect.com/science/article/pii/B978012800274200003X> | Chapter (Bahadori design) | CP | 403 in curl; browser |
| <https://www.sciencedirect.com/book/9780128002742/cathodic-corrosion-protection-systems> | Book | CP | |
| <https://www.sciencedirect.com/science/article/pii/B9780128245408000065> | Chapter (El-Reedy integrity) | CP | 403 in curl; browser |
| <https://www.sciencedirect.com/book/9780128245408/asset-integrity-management-for-offshore-and-onshore-structures> | Book | CP | |
| <https://www.sciencedirect.com/science/article/pii/S0961953422002914> | Journal article (MFC) | Noise | CP-unrelated use of “anode” |
| <https://www.sciencedirect.com/science/article/pii/B9780323524728000253> | Chapter (Popov 3e) | CP | 403 in curl; browser |
| <https://www.sciencedirect.com/book/9780323524728/handbook-of-environmental-degradation-of-materials> | Book | CP | |
| <https://www.sciencedirect.com/science/article/pii/B9780128126226000257> | Chapter (subsea anode distribution) | CP | 403 in curl; browser |
| <https://www.sciencedirect.com/book/9780128126226/subsea-engineering-handbook> | Book | CP | |
| <https://www.sciencedirect.com/science/article/pii/B9780123747136000044> | Chapter (SOFC) | Noise | anode in fuel cell context |

**C. Topic sublinks (inline in CP excerpts, unique paths)**

| URL | Relevance | Notes |
| --- | -------- | ----- |
| <https://www.sciencedirect.com/topics/engineering> | Nav | subject index |
| <https://www.sciencedirect.com/topics/engineering/anode-dimension> | CP | design input |
| <https://www.sciencedirect.com/topics/materials-science/seawater> | CP | resistivity / environment |
| <https://www.sciencedirect.com/topics/engineering/protection-object> | CP | stand-off / flush target |
| <https://www.sciencedirect.com/topics/engineering/anode-type> | CP | |
| <https://www.sciencedirect.com/topics/engineering/anode-surface> | CP | area |
| <https://www.sciencedirect.com/topics/materials-science/anode-material> | CP | |
| <https://www.sciencedirect.com/topics/engineering/cathodic-protection-system> | CP | |
| <https://www.sciencedirect.com/topics/engineering/cathodic-protection> | CP | Tier-2 parent topic |
| <https://www.sciencedirect.com/topics/engineering/open-circuit-potential> | CP | OCP in circuit |
| <https://www.sciencedirect.com/topics/engineering/cathodic-current-density> | CP | |
| <https://www.sciencedirect.com/topics/engineering/current-efficiency> | CP | anode life |
| <https://www.sciencedirect.com/topics/engineering/river-water> | CP | brackish / range of \(\rho\) |
| <https://www.sciencedirect.com/topics/engineering/ohms-law> | ICCP-adj | |
| <https://www.sciencedirect.com/topics/engineering/high-current-density> | CP | current distribution |
| <https://www.sciencedirect.com/topics/engineering/secondary-structure> | CP | interaction |
| <https://www.sciencedirect.com/topics/engineering/sacrificial-anode> | CP | |
| <https://www.sciencedirect.com/topics/engineering/remedial-measure> | CP | |
| <https://www.sciencedirect.com/topics/engineering/net-anode-mass> | CP | life |
| <https://www.sciencedirect.com/topics/engineering/final-radius> | CP | consumed anode |
| <https://www.sciencedirect.com/topics/engineering/bracelet-anode> | CP | |
| <https://www.sciencedirect.com/topics/engineering/anode-alloy> | CP | |
| <https://www.sciencedirect.com/topics/engineering/square-centimeter> | Nav | unit |
| <https://www.sciencedirect.com/topics/engineering/individual-anode> | CP | |
| <https://www.sciencedirect.com/topics/engineering/anode-side> | Noise | can appear in SOFC excerpt |
| <https://www.sciencedirect.com/topics/engineering/micro-tubular-solid-oxide-fuel-cell> | Noise | |
| <https://www.sciencedirect.com/topics/engineering/anode-tube> | Noise | |
| <https://www.sciencedirect.com/topics/engineering/cathode-side> | Noise | |
| <https://www.sciencedirect.com/topics/engineering/high-operating-temperature> | Noise | SOFC excerpt |
| <https://www.sciencedirect.com/topics/agricultural-and-biological-sciences/root-growth> | Noise | MFC / bio excerpt |

**D. Footer “Related terms” (topic home links on hub)**

| URL | Relevance |
| --- | -------- |
| <https://www.sciencedirect.com/topics/engineering/battery-electrochemical-energy-engineering> | ICCP-adj / device |
| <https://www.sciencedirect.com/topics/engineering/reduced-graphene-oxide> | Noise |
| <https://www.sciencedirect.com/topics/engineering/cathodic-protection> | CP (duplicate of §C) |
| <https://www.sciencedirect.com/topics/engineering/microbial-fuel-cell> | Noise |
| <https://www.sciencedirect.com/topics/engineering/backfill-material> | CP |
| <https://www.sciencedirect.com/topics/engineering/power-density> | ICCP-adj / device |
| <https://www.sciencedirect.com/topics/engineering/sacrificial-anode> | CP (duplicate) |
| <https://www.sciencedirect.com/topics/engineering/bracelet-anode> | CP (duplicate) |
| <https://www.sciencedirect.com/topics/engineering/cathodic-protection-system> | CP (duplicate) |
| <https://www.sciencedirect.com/topics/engineering/coating-breakdown-factor> | CP |

**E. Journals (Recommended publications) and nav**

| URL | Relevance |
| --- | -------- |
| <https://www.sciencedirect.com/journal/journal-of-power-sources> | General electrochem energy |
| <https://www.sciencedirect.com/journal/international-journal-of-hydrogen-energy> | General |
| <https://www.sciencedirect.com/journal/applied-energy> | General |
| <https://www.sciencedirect.com/journal/energy-conversion-and-management> | General |
| <https://www.sciencedirect.com/browse/journals-and-books> | Nav |

**F. Omitted (login / cookie / duplicate)**

- `https://www.sciencedirect.com/user/login?…` — Nav (sign-in)  
- Repeated `View chapter` / `Read full chapter` URLs — **duplicates** of the unique PII rows in §B  
- External Elsevier logo / cookie / OneTrust URLs — not listed  

---

## Appendix B. Disclaimer

This file is **operator-facing context** and a **citation list**. It is **not** a substitute for qualified CP design, codes, or signed drawings. For authoritative formulas and figures, use licensed access to the **cited books** or your applicable **industry standard**.
