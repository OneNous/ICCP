"""Channel path classification (DRY / WEAK_WET / CONDUCTIVE)."""

from __future__ import annotations

import pytest

import config.settings as cfg
from control import ChannelState, classify_channel


def _ch() -> ChannelState:
    return ChannelState(0)


def test_classify_low_z_holds_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    ch = _ch()
    ch.status = ChannelState.WEAK_WET
    # 11.5 V / 0.02 A = 575 Ω < 800
    assert classify_channel(ch, 20.0, 11.5, cfg) == ChannelState.WEAK_WET


def test_dry_hysteresis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 3)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 5)

    ch = _ch()
    ch.status = ChannelState.WEAK_WET
    for _ in range(2):
        assert classify_channel(ch, 0.02, 11.5, cfg) == ChannelState.WEAK_WET
    assert classify_channel(ch, 0.02, 11.5, cfg) == ChannelState.DRY


def test_weak_wet_high_impedance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 2)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 2)

    ch = _ch()
    ch.status = ChannelState.DRY
    # 0.4 mA, 11.5 V → 28.75 kΩ > 12k, current < 0.5 mA
    assert classify_channel(ch, 0.4, 11.5, cfg) == ChannelState.WEAK_WET


def test_conductive_requires_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CHANNEL_DRY_MA", 0.05)
    monkeypatch.setattr(cfg, "DRY_HOLD_TICKS", 2)
    monkeypatch.setattr(cfg, "CHANNEL_CONDUCTIVE_MA", 0.5)
    monkeypatch.setattr(cfg, "MAX_EFFECTIVE_OHMS", 12000.0)
    monkeypatch.setattr(cfg, "MIN_EFFECTIVE_OHMS", 800.0)
    monkeypatch.setattr(cfg, "CONDUCTIVE_HOLD_TICKS", 3)

    ch = _ch()
    ch.status = ChannelState.WEAK_WET
    # 2 mA @ 5 V → 2.5 kΩ, strong path
    assert classify_channel(ch, 2.0, 5.0, cfg) == ChannelState.WEAK_WET
    assert classify_channel(ch, 2.0, 5.0, cfg) == ChannelState.WEAK_WET
    assert classify_channel(ch, 2.0, 5.0, cfg) == ChannelState.CONDUCTIVE
