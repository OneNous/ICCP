"""cloud_sync.readings_row_from_latest — slim ``readings`` rows for Supabase."""

from __future__ import annotations

import pytest

import cloud_sync
import device_identity


@pytest.fixture(autouse=True)
def _reset_identity_cache() -> None:
    device_identity.reset_for_tests()


def test_readings_row_none_when_serial_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_SERIAL", raising=False)
    monkeypatch.setattr(device_identity, "derive_device_serial", lambda **_: "CS-UNKNOWN")
    monkeypatch.setattr(device_identity, "has_valid_serial", lambda *_: False)
    assert cloud_sync.readings_row_from_latest({"ts_unix": 1.0, "native_mv": 1.0}) is None


def test_readings_polarization_prefers_native_mv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    row = cloud_sync.readings_row_from_latest(
        {
            "ts_unix": 1_700_000_000,
            "native_mv": -100.7,
            "ref_raw_mv": 999.0,
            "channels": {"0": {"ma": 0.5}},
        }
    )
    assert row is not None
    assert row["polarization_mv"] == -101
    assert row["channel_1_ma"] == 0.5
    assert row["channel_2_ma"] is None


def test_readings_channels_use_shunt_i_ma_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    row = cloud_sync.readings_row_from_latest(
        {
            "ts_unix": 0.0,
            "ref_shift_mv": -2.0,
            "channels": {"0": {"shunt_i_ma": 3.3}},
        }
    )
    assert row is not None
    assert row["channel_1_ma"] == 3.3
    assert row["polarization_mv"] == -2


def test_channel_mas_from_snapshot_empty() -> None:
    assert cloud_sync.channel_mas_from_snapshot({}) == (None, None, None, None)
