# Field self-calibration: reference temp compensation (post–v1)

`REF_TEMP_COMP_MV_PER_F` in `config/settings.py` corrects open-circuit reference mV for drain-pan temperature drift. **Do not** fit this coefficient from a short idle native capture: temperature is nearly constant over one minute, so a linear regression has no leverage (slope is noise).

**Planned approach (not commissioning-time):**

1. During normal operation over **days**, log `(temp_f, ref_mv)` pairs at a **consistent** point in the signal chain (e.g. values associated with the outer-loop instant-off or another stable ref sample).
2. When the dataset has at least **~100** samples and spans at least **~15°F**, compute a **linear slope** (mV per °F) and write `ref_temp_comp_mv_per_f` into `commissioning.json` (or a sidecar), reusing the same load path as `ref_ads_scale`.
3. Until those gates are met, keep `REF_TEMP_COMP_MV_PER_F = 0.0` (no correction).

This is a **fleet / analytics** follow-up, not a Phase 1 block.
