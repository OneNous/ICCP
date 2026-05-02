"""cloud_sync.telemetry_points_row_from_latest — PostgREST row shape for Supabase."""

from __future__ import annotations

import json

import pytest

import cloud_sync
import device_identity


@pytest.fixture(autouse=True)
def _reset_identity_cache() -> None:
    """Each test should re-resolve from its own monkey-patched env."""
    device_identity.reset_for_tests()


def test_row_none_when_serial_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    """If every identity source returns ``None``, telemetry rows must drop, not insert junk."""
    monkeypatch.delenv("COILSHIELD_SERIAL", raising=False)
    monkeypatch.setattr(device_identity, "derive_device_serial", lambda **_: "CS-UNKNOWN")
    monkeypatch.setattr(device_identity, "has_valid_serial", lambda *_: False)
    assert cloud_sync.telemetry_points_row_from_latest({"ts_unix": 1.0}) is None


def test_row_maps_shift_total_time_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    snap = {"ts_unix": 1_700_000_000, "ref_shift_mv": -12.3, "total_ma": 4.56, "telemetry_seq": 9}
    row = cloud_sync.telemetry_points_row_from_latest(snap)
    assert row is not None
    assert row["serial"] == "SMOKE00000001"
    assert row["shift_mV"] == -12.3
    assert row["total_mA"] == 4.56
    assert row["time"].endswith("+00:00")
    back = json.loads(row["payload_json"])
    assert back["telemetry_seq"] == 9


def test_row_falls_back_shift_mv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    row = cloud_sync.telemetry_points_row_from_latest({"ts_unix": 0.0, "shift_mv": 5.0})
    assert row is not None
    assert row["shift_mV"] == 5.0
