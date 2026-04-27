"""Binary search commissioning path."""

from __future__ import annotations

import pytest

import config.settings as cfg
import commissioning


def test_binary_search_makes_fewer_trials_than_linear_cap(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "MAX_MA", 5.0, raising=False)
    monkeypatch.setattr(cfg, "COMMISSIONING_BINARY_MA_LO", 0.05, raising=False)
    monkeypatch.setattr(cfg, "COMMISSIONING_BINARY_RESOLUTION_MA", 0.1, raising=False)
    monkeypatch.setattr(cfg, "COMMISSIONING_BINARY_MAX_ITERATIONS", 8, raising=False)
    trials: list[float] = []

    class R:
        def effective_shift_target_mv(self) -> float:
            return 50.0

        def effective_max_shift_mv(self) -> float:
            return 150.0

    r = R()

    def _pump(*_a, **_k) -> None:  # noqa: ANN001
        return

    def _io(*_a, **_k) -> tuple[float, float, float]:  # noqa: ANN001
        m = float(cfg.TARGET_MA)
        trials.append(m)
        shift = (m - 0.1) * 20.0
        return 100.0, shift, -0.5

    monkeypatch.setattr(commissioning, "_pump_control", _pump)
    monkeypatch.setattr(commissioning, "_instant_off_ref_mv_and_restore", _io)
    monkeypatch.setattr(commissioning, "RAMP_SETTLE_S", 0.0)
    out, hist, reached = commissioning._phase2_binary_search_mA(
        r,
        None,
        None,
        lambda *_x, **_y: None,
        False,
        None,
        "oc",
    )
    assert out > 0.0
    assert isinstance(reached, bool)
    assert len(trials) == len(hist) <= 8
    for row in hist:
        assert "ma" in row and "shift_mv" in row
    assert reached is True


def test_binary_search_reduces_ma_when_shift_above_ceiling(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """High mA pushes shift above ceiling; bisection must find mA in [50, 150] mV window."""
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "MAX_MA", 5.0, raising=False)
    # High binary lo so first mid is >2.5 mA → shift above 150 mV ceiling before bisection
    monkeypatch.setattr(cfg, "COMMISSIONING_BINARY_MA_LO", 2.0, raising=False)
    monkeypatch.setattr(cfg, "COMMISSIONING_BINARY_RESOLUTION_MA", 0.1, raising=False)
    monkeypatch.setattr(cfg, "COMMISSIONING_BINARY_MAX_ITERATIONS", 16, raising=False)
    trials: list[float] = []

    class R:
        def effective_shift_target_mv(self) -> float:
            return 50.0

        def effective_max_shift_mv(self) -> float:
            return 150.0

    r = R()

    def _pump(*_a, **_k) -> None:  # noqa: ANN001
        return

    def _io(*_a, **_k) -> tuple[float, float, float]:  # noqa: ANN001
        m = float(cfg.TARGET_MA)
        trials.append(m)
        # m≈2.5 → ~100 mV (in window); low mA → below floor; high mA → above ceiling
        shift = 100.0 + (m - 2.5) * 80.0
        return 100.0, shift, -0.5

    monkeypatch.setattr(commissioning, "_pump_control", _pump)
    monkeypatch.setattr(commissioning, "_instant_off_ref_mv_and_restore", _io)
    monkeypatch.setattr(commissioning, "RAMP_SETTLE_S", 0.0)
    out, hist, reached = commissioning._phase2_binary_search_mA(
        r,
        None,
        None,
        lambda *_x, **_y: None,
        False,
        None,
        "oc",
    )
    assert reached is True
    assert 50.0 <= (100.0 + (out - 2.5) * 80.0) <= 150.0
    assert any(float(row["ma"]) >= 2.0 for row in hist)
    assert any(
        row.get("shift_mv") is not None and float(row["shift_mv"]) > 150.0
        for row in hist
    )
    assert len(trials) == len(hist)
