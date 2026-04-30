"""cloud_worker — sidecar queue and worker (no live Supabase in CI)."""

from __future__ import annotations

import importlib
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("COILSHIELD_LOG_DIR", str(tmp_path))
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
    monkeypatch.setenv("COILSHIELD_CLOUD_SYNC", "1")
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
