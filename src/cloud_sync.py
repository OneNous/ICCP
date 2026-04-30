"""Supabase client helpers (claude.md / .claude/cloud-sync.md).

Best-effort only: failures here must never affect the control loop. Use the service role
key only from environment or a root ``.env`` on dev machines — never log keys or expose
them over HTTP/BLE.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supabase import Client

_REPO_ROOT_CACHE: Path | None = None


def _repo_root() -> Path:
    global _REPO_ROOT_CACHE
    if _REPO_ROOT_CACHE is not None:
        return _REPO_ROOT_CACHE
    here = Path(__file__).resolve().parent
    root = here.parent if (here.parent / "config" / "settings.py").exists() else here
    _REPO_ROOT_CACHE = root
    return root


def load_dotenv_if_present(*, override: bool = False) -> bool:
    """Load ``<repo>/.env`` if ``python-dotenv`` is installed. Returns whether load was attempted."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    env_path = _repo_root() / ".env"
    if not env_path.is_file():
        return False
    load_dotenv(env_path, override=override)
    return True


def supabase_url() -> str:
    return (os.environ.get("SUPABASE_URL") or "").strip()


def supabase_service_key() -> str:
    return (
        (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        or (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    )


def is_supabase_configured() -> bool:
    return bool(supabase_url() and supabase_service_key())


def get_supabase_client() -> Client | None:
    """Return a Supabase client, or ``None`` if misconfigured or ``supabase`` is not installed."""
    if not is_supabase_configured():
        return None
    try:
        from supabase import create_client
    except ImportError:
        return None
    return create_client(supabase_url(), supabase_service_key())


def supabase_ping() -> tuple[bool, str]:
    """
    Cheap connectivity + auth check (Storage list buckets — no app tables required).

    Returns ``(ok, message)``. Never includes secrets in ``message``.
    """
    if not is_supabase_configured():
        return False, "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_SERVICE_KEY) not set"
    try:
        from supabase import create_client
    except ImportError as e:
        return False, f"supabase package missing: {e} (pip install -e '.[supabase]')"
    url, key = supabase_url(), supabase_service_key()
    try:
        client = create_client(url, key)
        buckets = client.storage.list_buckets()
    except Exception as e:
        return False, f"Supabase request failed: {type(e).__name__}: {e}"
    n = len(buckets) if buckets is not None else 0
    return True, f"ok ({n} storage bucket(s) visible)"


def insert_rows(table: str, rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """
    Insert one or more rows into ``table`` (service role; bypasses RLS).

    For future telemetry sync — callers must catch exceptions and never propagate
    into the control loop.
    """
    client = get_supabase_client()
    if client is None:
        return False, "client not available"
    if not rows:
        return True, "nothing to insert"
    try:
        client.table(table).insert(rows).execute()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, f"inserted {len(rows)} row(s) into {table!r}"
