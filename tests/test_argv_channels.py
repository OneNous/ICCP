"""``config/argv_channels`` — active anode selection from argv / env."""

from __future__ import annotations

import os

import pytest

from config.argv_channels import apply_coilshield_active_channels_from_argv


def test_apply_channels_0based_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert apply_coilshield_active_channels_from_argv(["x", "--channels", "0,2"]) is None
    assert os.environ.get("COILSHIELD_ACTIVE_CHANNELS") == "0,2"


def test_apply_anodes_1based_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert apply_coilshield_active_channels_from_argv(["iccp", "start", "--anodes", "1,3"]) is None
    assert os.environ.get("COILSHIELD_ACTIVE_CHANNELS") == "0,2"


def test_apply_both_flags_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert (
        apply_coilshield_active_channels_from_argv(
            ["--channels", "0", "--anodes", "2"]
        )
        == 2
    )
    assert "COILSHIELD_ACTIVE_CHANNELS" not in os.environ
