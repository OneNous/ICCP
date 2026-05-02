"""Tests for pi_edge.ensure_bluetooth."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="DBus BLE stack is Linux-only")
def test_wait_for_ble_adapter_path_returns_first_hit() -> None:
    from pi_edge.ensure_bluetooth import wait_for_ble_adapter_path

    n = {"c": 0}

    def find() -> str | None:
        n["c"] += 1
        return "/org/bluez/hci0" if n["c"] >= 2 else None

    with patch("pi_edge.ensure_bluetooth.ensure_bluetooth_enabled"):
        path = wait_for_ble_adapter_path(find, attempts=5, delay_s=0.01)
    assert path == "/org/bluez/hci0"


def test_ensure_bluetooth_uses_rfkill_when_usr_sbin_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pi_edge import ensure_bluetooth as eb

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    real_is_file = Path.is_file

    def is_file(self) -> bool:
        s = str(self)
        if s == "/usr/sbin/rfkill":
            return True
        if s == "/sbin/rfkill":
            return False
        return real_is_file(self)

    monkeypatch.setattr(eb.subprocess, "run", fake_run)
    monkeypatch.setattr(eb.Path, "is_file", is_file)

    eb.ensure_bluetooth_enabled(verbose=False)
    assert calls == [["/usr/sbin/rfkill", "unblock", "bluetooth"]]
