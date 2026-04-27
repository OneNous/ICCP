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
