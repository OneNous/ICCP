"""Background Supabase sync: pending_uploads sidecar SQLite + daemon thread.

See ``.claude/cloud-sync.md``. Never raises into the control loop; all entry points
swallow errors. Gated by ``config.settings.CLOUD_SYNC_ENABLED`` and Supabase env.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

import config.settings as cfg

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _queue_db_path() -> Path:
    name = str(getattr(cfg, "CLOUD_QUEUE_DB_NAME", "cloud_queue.db") or "cloud_queue.db").strip()
    return Path(cfg.LOG_DIR).resolve() / name.lstrip("/")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_table TEXT NOT NULL,
            payload TEXT NOT NULL,
            queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            retry_count INTEGER DEFAULT 0,
            last_error TEXT
        )
        """
    )
    conn.commit()


def _connect() -> sqlite3.Connection:
    p = _queue_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def enqueue_telemetry_snapshot(payload_json: str) -> None:
    """Persist one snapshot row for the background worker (best-effort, O(1) SQLite)."""
    if not bool(getattr(cfg, "CLOUD_SYNC_ENABLED", False)):
        return
    if not bool(getattr(cfg, "SUPABASE_CONFIGURED", False)):
        return
    if not payload_json or len(payload_json) > 8_000_000:
        return
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO pending_uploads (target_table, payload) VALUES (?, ?)",
                (str(cfg.CLOUD_TELEMETRY_TABLE), payload_json),
            )
        finally:
            conn.close()
    except Exception:
        pass


def _prune_old(conn: sqlite3.Connection) -> None:
    days = int(getattr(cfg, "CLOUD_PENDING_QUEUE_MAX_DAYS", 30) or 30)
    conn.execute(
        f"DELETE FROM pending_uploads WHERE queued_at < datetime('now', '-{max(1, days)} days')"
    )


def _classify_insert_error(exc: BaseException) -> str:
    """Return ``drop`` (do not retry — bad auth/schema/row) or ``retry``."""
    msg = f"{type(exc).__name__}: {exc}"
    low = msg.lower()
    # PostgREST / gotrue style hints (fragile but avoids hard dep on exception types).
    for token in (
        "401",
        "403",
        "404",
        "406",
        "409",
        "422",
        "jwt",
        "invalid api key",
        "permission denied",
        "row-level security",
        "violates",
        "duplicate key",
        "malformed",
        "pgrst",
    ):
        if token in low:
            return "drop"
    return "retry"


def _fetch_batch(conn: sqlite3.Connection, limit: int) -> list[tuple[int, str]]:
    cur = conn.execute(
        """
        SELECT id, payload FROM pending_uploads
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    return [(int(r[0]), str(r[1])) for r in cur.fetchall()]


def _delete_ids(conn: sqlite3.Connection, ids: list[int]) -> None:
    if not ids:
        return
    qmarks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM pending_uploads WHERE id IN ({qmarks})", ids)


def _bump_retry(conn: sqlite3.Connection, ids: list[int], err: str) -> None:
    if not ids:
        return
    qmarks = ",".join("?" * len(ids))
    conn.execute(
        f"""
        UPDATE pending_uploads
        SET retry_count = retry_count + 1, last_error = ?
        WHERE id IN ({qmarks})
        """,
        [err[:2000]] + ids,
    )


def _create_supabase_client() -> Any:
    """Indirection for tests (patch this instead of ``supabase.create_client``)."""
    from supabase import create_client

    import cloud_sync

    return create_client(cloud_sync.supabase_url(), cloud_sync.supabase_service_key())


def _process_once() -> None:
    import cloud_sync

    if not cloud_sync.is_supabase_configured():
        return
    batch = int(getattr(cfg, "CLOUD_BATCH_SIZE", 50) or 50)
    table = str(getattr(cfg, "CLOUD_TELEMETRY_TABLE", "telemetry_snapshots"))
    try:
        conn = _connect()
    except Exception:
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        _prune_old(conn)
        rows = _fetch_batch(conn, batch)
        if not rows:
            conn.commit()
            return
        upload_ids: list[int] = []
        parsed: list[dict[str, Any]] = []
        bad_ids: list[int] = []
        for rid, payload in rows:
            try:
                obj = json.loads(payload)
                if not isinstance(obj, dict):
                    bad_ids.append(rid)
                else:
                    upload_ids.append(rid)
                    parsed.append(obj)
            except (json.JSONDecodeError, TypeError, ValueError):
                bad_ids.append(rid)
        if bad_ids:
            _delete_ids(conn, bad_ids)
        if not parsed:
            conn.commit()
            return
        try:
            client = _create_supabase_client()
        except ImportError:
            conn.rollback()
            return
        try:
            client.table(table).insert(parsed).execute()
        except Exception as e:
            policy = _classify_insert_error(e)
            err_s = f"{type(e).__name__}: {e}"
            if policy == "drop":
                _delete_ids(conn, upload_ids)
            else:
                _bump_retry(conn, upload_ids, err_s)
            conn.commit()
            return
        _delete_ids(conn, upload_ids)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _run_loop() -> None:
    interval = max(1.0, float(getattr(cfg, "CLOUD_SYNC_INTERVAL_S", 60.0) or 60.0))
    health_iv = max(30.0, float(getattr(cfg, "CLOUD_HEALTH_CHECK_INTERVAL_S", 300.0) or 300.0))
    last_health = 0.0
    extra_backoff = 0.0
    import cloud_sync

    while not _stop.is_set():
        try:
            _process_once()
        except Exception:
            pass
        try:
            now = time.monotonic()
            if now - last_health >= health_iv:
                last_health = now
                ok, msg = cloud_sync.supabase_ping()
                if not ok:
                    print(
                        f"[cloud_worker] health check failed: {msg}",
                        file=sys.stderr,
                        flush=True,
                    )
                    extra_backoff = min(32.0, max(1.0, (extra_backoff or 1.0) * 2.0))
                else:
                    extra_backoff = 0.0
        except Exception:
            pass
        if _stop.wait(timeout=interval + extra_backoff):
            break


def start_background_sync() -> None:
    """Start daemon thread if cloud sync is enabled and Supabase is configured."""
    global _thread
    if not bool(getattr(cfg, "CLOUD_SYNC_ENABLED", False)):
        return
    import cloud_sync

    if not cloud_sync.is_supabase_configured():
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run_loop, name="cloud_sync_worker", daemon=True)
        _thread.start()


def stop_and_join(*, timeout_s: float = 5.0) -> None:
    """Signal worker exit and wait briefly (PWM / logger shutdown order)."""
    global _thread
    _stop.set()
    t: threading.Thread | None
    with _lock:
        t = _thread
    if t is not None and t.is_alive():
        t.join(timeout=timeout_s)


def reset_for_tests() -> None:
    """Test helper: stop thread and clear stop event."""
    global _thread
    _stop.set()
    with _lock:
        th = _thread
        _thread = None
    if th is not None and th.is_alive():
        th.join(timeout=2.0)
    _stop.clear()
