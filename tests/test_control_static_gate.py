"""PWMBank static gate-off + commissioning Phase 1 context (sim-safe)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import commissioning
import config.settings as cfg
from control import PWMBank


def test_phase1_static_context_true_calls_enter_leave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "COMMISSIONING_PHASE1_STATIC_GATE_LOW", True, raising=False)
    events: list[str] = []

    ctrl = SimpleNamespace(
        enter_static_gate_off=lambda: events.append("enter"),
        leave_static_gate_off=lambda: events.append("leave"),
    )
    with commissioning._phase1_static_gate_context(ctrl):
        events.append("body")
    assert events == ["enter", "body", "leave"]


def test_phase1_static_context_false_skips_pwm_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "COMMISSIONING_PHASE1_STATIC_GATE_LOW", False, raising=False)
    events: list[str] = []

    ctrl = SimpleNamespace(
        enter_static_gate_off=lambda: events.append("enter"),
        leave_static_gate_off=lambda: events.append("leave"),
    )
    with commissioning._phase1_static_gate_context(ctrl):
        events.append("body")
    assert events == ["body"]


def test_phase1_static_context_leave_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "COMMISSIONING_PHASE1_STATIC_GATE_LOW", True, raising=False)
    events: list[str] = []

    ctrl = SimpleNamespace(
        enter_static_gate_off=lambda: events.append("enter"),
        leave_static_gate_off=lambda: events.append("leave"),
    )
    with pytest.raises(RuntimeError):
        with commissioning._phase1_static_gate_context(ctrl):
            raise RuntimeError("boom")
    assert events == ["enter", "leave"]


def test_pwmbank_sim_static_enter_is_noop_idempotent() -> None:
    bank = PWMBank()
    assert bank.static_gate_off_active is False
    bank.enter_static_gate_off()
    assert bank.static_gate_off_active is False
    bank.enter_static_gate_off()
    bank.set_duty(0, 45.0)
    assert bank.duty(0) == 45.0
    bank.leave_static_gate_off()
    bank.cleanup()
