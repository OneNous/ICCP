"""Fault category gates auto-clear (polarization = manual only)."""

from __future__ import annotations

import time

import pytest

import config.settings as cfg
from control import ChannelState, Controller


def test_polarization_fault_not_auto_cleared_by_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "FAULT_AUTO_CLEAR", True)
    monkeypatch.setattr(cfg, "FAULT_RETRY_INTERVAL_S", 0.01)
    monkeypatch.setattr(cfg, "FAULT_RETRY_MAX", 99)
    ctrl = Controller()
    st = ctrl._states[0]
    st.status = ChannelState.FAULT
    st.latch_message = "POLARIZATION CUTOFF test"
    st.fault_category = cfg.FAULT_CATEGORY_POLARIZATION
    st.fault_time = time.monotonic() - 1.0
    st.fault_retry_count = 0
    r = {"ok": True, "current": 0.0, "bus_v": 5.0}
    ctrl._maybe_auto_clear_fault(0, st, r)
    assert st.status == ChannelState.FAULT
    assert st.fault_category == cfg.FAULT_CATEGORY_POLARIZATION


def test_latch_polarization_cutoff_all_active_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl = Controller()
    ctrl.latch_polarization_cutoff_all("POLARIZATION unit test")
    for ch in range(cfg.NUM_CHANNELS):
        if cfg.is_channel_active(ch):
            assert ctrl._states[ch].status == ChannelState.FAULT
            assert ctrl._states[ch].fault_category == cfg.FAULT_CATEGORY_POLARIZATION
        ctrl._clear_channel_fault(ctrl._states[ch])
