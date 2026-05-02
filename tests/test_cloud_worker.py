"""cloud_worker — sidecar queue and worker (no live Supabase in CI)."""

from __future__ import annotations

import importlib
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_identity_cache() -> None:
    """Each test resolves serial fresh; otherwise cached env from previous test wins."""
    import device_identity

    device_identity.reset_for_tests()


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("COILSHIELD_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("COILSHIELD_SERIAL_CACHE", str(tmp_path / "serial"))
    import config.settings as cfg

    importlib.reload(cfg)
    yield tmp_path
    importlib.reload(cfg)


def test_enqueue_skipped_when_disabled(log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "0")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)

    cloud_worker.enqueue_telemetry_snapshot('{"ok":true}')
    db = log_dir / "cloud_queue.db"
    assert not db.exists()


def test_enqueue_inserts_when_enabled(
    log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)

    cloud_worker.enqueue_telemetry_snapshot(json.dumps({"telemetry_seq": 1}))
    db = log_dir / "cloud_queue.db"
    assert db.is_file()
    con = sqlite3.connect(str(db))
    try:
        n = con.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0]
        assert n == 1
    finally:
        con.close()


def test_process_once_deletes_on_successful_insert(log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Non-``telemetry_points`` target inserts JSON rows as-is (legacy / custom tables)."""
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("COILSHIELD_CLOUD_TELEMETRY_TABLE", "telemetry_snapshots")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)

    cloud_worker.enqueue_telemetry_snapshot(json.dumps({"a": 1}))

    mock_exec = MagicMock()

    class _Chain:
        def insert(self, _rows):
            return self

        def execute(self):
            mock_exec()
            return self

    class _Table:
        def insert(self, rows):
            assert len(rows) == 1 and rows[0]["a"] == 1
            return _Chain()

    class _Client:
        def table(self, _name: str):
            return _Table()

    with patch.object(cloud_worker, "_create_supabase_client", return_value=_Client()):
        cloud_worker._process_once()

    db = log_dir / "cloud_queue.db"
    con = sqlite3.connect(str(db))
    try:
        n = con.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0]
        assert n == 0
    finally:
        con.close()


def test_process_once_telemetry_points_normalizes(
    log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("COILSHIELD_CLOUD_TELEMETRY_TABLE", "telemetry_points")
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)

    snap = {"ts_unix": 1_700_000_000, "ref_shift_mv": -1.5, "total_ma": 2.25, "telemetry_seq": 3}
    cloud_worker.enqueue_telemetry_snapshot(json.dumps(snap))

    cap_tp: list[dict] = []

    class _Chain:
        def insert(self, _rows):
            return self

        def execute(self):
            return self

    class _Table:
        def __init__(self, name: str) -> None:
            self._name = name

        def insert(self, rows):
            if self._name == "telemetry_points":
                cap_tp.extend(rows)
            return _Chain()

    class _Client:
        def table(self, name: str):
            return _Table(name)

    with patch.object(cloud_worker, "_create_supabase_client", return_value=_Client()):
        cloud_worker._process_once()

    assert len(cap_tp) == 1
    row = cap_tp[0]
    assert row["serial"] == "SMOKE00000001"
    assert row["shift_mV"] == -1.5
    assert row["total_mA"] == 2.25
    assert "payload_json" in row


def test_process_once_inserts_readings_when_cloud_readings_enabled(
    log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("COILSHIELD_CLOUD_READINGS", "1")
    monkeypatch.setenv("COILSHIELD_CLOUD_TELEMETRY_TABLE", "telemetry_points")
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)

    snap = {
        "ts_unix": 1_700_000_000,
        "native_mv": -100.2,
        "ref_shift_mv": -1.5,
        "total_ma": 2.25,
        "channels": {
            "0": {"ma": 1.0},
            "1": {"ma": 1.1},
            "2": {"ma": 0.9},
            "3": {"ma": 0.8},
        },
    }
    cloud_worker.enqueue_telemetry_snapshot(json.dumps(snap))

    cap_tp: list[list[dict]] = []
    cap_rd: list[list[dict]] = []

    class _Chain:
        def insert(self, _rows):
            return self

        def execute(self):
            return self

    class _Table:
        def __init__(self, name: str) -> None:
            self._name = name

        def insert(self, rows):
            if self._name == "telemetry_points":
                cap_tp.append(list(rows))
            elif self._name == "readings":
                cap_rd.append(list(rows))
            return _Chain()

    class _Client:
        def table(self, name: str):
            return _Table(name)

    with patch.object(cloud_worker, "_create_supabase_client", return_value=_Client()):
        cloud_worker._process_once()

    assert len(cap_tp) == 1 and len(cap_rd) == 1
    rd = cap_rd[0][0]
    assert rd["serial"] == "SMOKE00000001"
    assert rd["polarization_mv"] == -100
    assert rd["channel_1_ma"] == 1.0
    assert rd["channel_4_ma"] == 0.8


def test_process_once_readings_failure_still_clears_queue(
    log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("COILSHIELD_CLOUD_READINGS", "1")
    monkeypatch.setenv("COILSHIELD_CLOUD_TELEMETRY_TABLE", "telemetry_points")
    monkeypatch.setenv("COILSHIELD_SERIAL", "SMOKE00000001")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)

    cloud_worker.enqueue_telemetry_snapshot(
        json.dumps({"ts_unix": 1_700_000_000, "ref_shift_mv": -1.0, "total_ma": 1.0})
    )

    class _Chain:
        def insert(self, _rows):
            return self

        def execute(self):
            return self

    class _Table:
        def __init__(self, name: str) -> None:
            self._name = name

        def insert(self, rows):
            if self._name == "readings":
                raise RuntimeError("readings unavailable")
            return _Chain()

    class _Client:
        def table(self, name: str):
            return _Table(name)

    with patch.object(cloud_worker, "_create_supabase_client", return_value=_Client()):
        cloud_worker._process_once()

    db = log_dir / "cloud_queue.db"
    con = sqlite3.connect(str(db))
    try:
        n = con.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0]
        assert n == 0
    finally:
        con.close()


def test_process_once_drops_queue_when_serial_unresolvable_for_telemetry_points(
    log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``derive_device_serial`` fails the validity check, telemetry rows must drop."""
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("COILSHIELD_CLOUD_TELEMETRY_TABLE", "telemetry_points")
    monkeypatch.delenv("COILSHIELD_SERIAL", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import cloud_worker
    import config.settings as cfg
    import device_identity

    importlib.reload(cfg)
    importlib.reload(cloud_worker)
    monkeypatch.setattr(device_identity, "derive_device_serial", lambda **_: "CS-UNKNOWN")
    monkeypatch.setattr(device_identity, "has_valid_serial", lambda *_: False)

    cloud_worker.enqueue_telemetry_snapshot(json.dumps({"ts_unix": 1, "ref_shift_mv": 1.0}))

    with patch.object(
        cloud_worker,
        "_create_supabase_client",
        side_effect=AssertionError("should not insert"),
    ):
        cloud_worker._process_once()

    db = log_dir / "cloud_queue.db"
    con = sqlite3.connect(str(db))
    try:
        n = con.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0]
        assert n == 0
    finally:
        con.close()


def test_process_once_drops_malformed_json(log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)

    cloud_worker.enqueue_telemetry_snapshot("not-json{{{")
    with patch.object(
        cloud_worker,
        "_create_supabase_client",
        side_effect=AssertionError("should not insert"),
    ):
        cloud_worker._process_once()
    db = log_dir / "cloud_queue.db"
    con = sqlite3.connect(str(db))
    try:
        n = con.execute("SELECT COUNT(*) FROM pending_uploads").fetchone()[0]
        assert n == 0
    finally:
        con.close()


def test_start_stop_join(log_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC_INTERVAL_S", "3600")
    monkeypatch.setenv("COILSHIELD_CLOUD_HEALTH_INTERVAL_S", "3600")
    import cloud_worker
    import config.settings as cfg

    importlib.reload(cfg)
    importlib.reload(cloud_worker)
    cloud_worker.reset_for_tests()

    def _short_loop() -> None:
        while not cloud_worker._stop.is_set():
            if cloud_worker._stop.wait(timeout=0.05):
                break

    with patch.object(cloud_worker, "_run_loop", _short_loop):
        cloud_worker.start_background_sync()
        time.sleep(0.08)
        cloud_worker.stop_and_join(timeout_s=2.0)
    cloud_worker.reset_for_tests()
