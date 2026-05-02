"""device_identity — stable serial derivation (no real /proc/cpuinfo dependency in CI)."""

from __future__ import annotations

from pathlib import Path

import pytest

import device_identity


@pytest.fixture(autouse=True)
def _reset() -> None:
    device_identity.reset_for_tests()


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    monkeypatch.setenv("COILSHIELD_SERIAL_CACHE", str(tmp_path / "serial"))
    assert device_identity.derive_device_serial() == "SMOKE00000001"


def test_derived_cpuinfo_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COILSHIELD_SERIAL", raising=False)
    monkeypatch.setenv("COILSHIELD_SERIAL_CACHE", str(tmp_path / "serial"))

    def fake_try_cpuinfo(path: Path = Path("/proc/cpuinfo")) -> str | None:  # noqa: ARG001
        return "CS-1020304050607080"

    monkeypatch.setattr(device_identity, "_try_cpuinfo", fake_try_cpuinfo)
    s = device_identity.derive_device_serial()
    assert s == "CS-1020304050607080"
    cache = tmp_path / "serial"
    assert cache.is_file()
    device_identity.reset_for_tests()
    assert device_identity.derive_device_serial() == s


def test_has_valid_serial_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_identity, "derive_device_serial", lambda **_: "CS-UNKNOWN")
    assert device_identity.has_valid_serial() is False
