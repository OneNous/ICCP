"""``config/argv_channels`` — active anode selection from argv / env."""

from __future__ import annotations

import os

import pytest

from config.argv_channels import (
    apply_coilshield_active_channels_from_argv,
    parse_channel_indices_from_flag_strings,
)


def test_apply_channels_0based_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert apply_coilshield_active_channels_from_argv(["x", "--channels", "0,2"]) is None
    assert os.environ.get("COILSHIELD_ACTIVE_CHANNELS") == "0,2"


def test_apply_anodes_1based_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert apply_coilshield_active_channels_from_argv(["iccp", "start", "--anodes", "1,3"]) is None
    assert os.environ.get("COILSHIELD_ACTIVE_CHANNELS") == "0,2"


def test_apply_anode_singular_1based(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert apply_coilshield_active_channels_from_argv(["start", "--anode", "1"]) is None
    assert os.environ.get("COILSHIELD_ACTIVE_CHANNELS") == "0"


def test_apply_channel_singular_0based(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert apply_coilshield_active_channels_from_argv(["x", "--channel", "0"]) is None
    assert os.environ.get("COILSHIELD_ACTIVE_CHANNELS") == "0"


def test_apply_channels_and_channel_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert apply_coilshield_active_channels_from_argv(["--channels", "0,1", "--channel", "2"]) == 2


def test_apply_both_flags_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_ACTIVE_CHANNELS", raising=False)
    assert (
        apply_coilshield_active_channels_from_argv(
            ["--channels", "0", "--anodes", "2"]
        )
        == 2
    )
    assert "COILSHIELD_ACTIVE_CHANNELS" not in os.environ


def test_parse_indices_anode_1() -> None:
    assert parse_channel_indices_from_flag_strings(4, anode="1") == frozenset({0})


def test_parse_indices_channels_list() -> None:
    assert parse_channel_indices_from_flag_strings(4, channels="0,2") == frozenset(
        {0, 2}
    )


def test_parse_indices_empty_means_all() -> None:
    assert parse_channel_indices_from_flag_strings(4) is None


def test_parse_indices_rejects_mixed() -> None:
    with pytest.raises(ValueError, match="0-based"):
        parse_channel_indices_from_flag_strings(4, channel="0", anode="1")
