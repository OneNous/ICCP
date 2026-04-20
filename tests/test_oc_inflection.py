"""OC decay inflection helper (find_oc_inflection_mv)."""

from __future__ import annotations

import pytest

from reference import find_oc_inflection_mv


def test_find_oc_inflection_short_returns_last() -> None:
    s = [(0.0, 300.0), (0.01, 280.0), (0.02, 270.0)]
    assert find_oc_inflection_mv(s) == 270.0


def test_find_oc_inflection_piecewise_knee() -> None:
    # Linear steep drop (0–5), then gentle slope (5–20) — knee near transition.
    samples = []
    t = 0.0
    for i in range(6):
        samples.append((t, 300.0 - i * 15.0))
        t += 0.01
    for j in range(1, 16):
        samples.append((t, 225.0 - j * 0.5))
        t += 0.01
    # Skip all five steep-segment rates so the window is the gentle tail only.
    got = find_oc_inflection_mv(samples, skip_rates=5, tail_exclude=0.2)
    assert got == pytest.approx(224.5, abs=0.01)


def test_find_oc_inflection_returns_valid_sample_index() -> None:
    """Regression: inflection index is always within samples (no IndexError)."""
    s = [(i * 0.001, 100.0 - i * 0.1) for i in range(30)]
    v = find_oc_inflection_mv(s, skip_rates=0, tail_exclude=0.05)
    assert any(abs(v - p[1]) < 1e-6 for p in s)


def test_find_oc_inflection_flat_tail_excluded() -> None:
    # Steep then flat end — should not pick final flat noise as "knee" if tail excluded.
    s = [(i * 0.01, 300.0 - i * 2.0) for i in range(15)]
    for i in range(15, 30):
        s.append((i * 0.01, 270.0 + (i % 2) * 0.01))
    got = find_oc_inflection_mv(s, skip_rates=1, tail_exclude=0.3)
    assert got < 270.5
