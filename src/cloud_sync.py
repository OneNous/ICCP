"""Supabase client helpers (claude.md / .claude/cloud-sync.md).

Best-effort only: failures here must never affect the control loop. Use the service role
key only from environment or a root ``.env`` on dev machines — never log keys or expose
them over HTTP/BLE.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping


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


def supabase_anon_key() -> str:
    """Anon publishable key — used as the ``apikey`` header alongside per-device JWTs."""
    return (
        (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
        or (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or "").strip()
    )


def supabase_service_key() -> str:
    """Bearer token for PostgREST. Order: per-device JWT → service-role key (bench/dev only).

    Production Pis only ever ship with the anon key + a per-device JWT minted by
    ``device-register``. The service-role fallback exists for bench smoke tests
    and the validation rig where ``COILSHIELD_SERIAL`` and the service-role key
    are explicitly configured by the operator.
    """
    try:
        from cloud_bootstrap import current_device_jwt

        jwt = current_device_jwt()
        if jwt:
            return jwt
    except ImportError:
        pass
    return (
        (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        or (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    )


def is_supabase_configured() -> bool:
    return bool(supabase_url() and supabase_service_key())


def _snapshot_time_iso_utc(snapshot: Mapping[str, Any]) -> str:
    """RFC3339 UTC timestamp for PostgREST ``timestamptz`` from ``ts_unix`` or wall clock."""
    ts_unix = snapshot.get("ts_unix")
    if isinstance(ts_unix, (int, float)):
        tdt = datetime.fromtimestamp(float(ts_unix), tz=timezone.utc)
        return tdt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _cloud_device_serial() -> str | None:
    """Stable serial for cloud rows; ``None`` when too short for ``devices.serial`` CHECK."""
    from device_identity import derive_device_serial, has_valid_serial

    s = derive_device_serial()
    return s if has_valid_serial(s) else None


def _polarization_mv_int(snapshot: Mapping[str, Any]) -> int | None:
    """Scalar for ``readings.polarization_mv`` — prefer ``native_mv``, then ref, then shift."""
    for key in ("native_mv", "ref_raw_mv", "ref_shift_mv", "shift_mv"):
        v = snapshot.get(key)
        if v is None:
            continue
        try:
            return int(round(float(v)))
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def channel_mas_from_snapshot(
    snapshot: Mapping[str, Any],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Per-channel mA from ``channels`` map (keys ``0``..``n``), up to four."""
    ch = snapshot.get("channels")
    if not isinstance(ch, dict):
        return (None, None, None, None)
    out: list[float | None] = []
    for i in range(4):
        d = ch.get(str(i))
        if not isinstance(d, dict):
            out.append(None)
            continue
        m = d.get("ma")
        if m is None:
            m = d.get("shunt_i_ma")
        if m is None:
            out.append(None)
            continue
        try:
            out.append(float(m))
        except (TypeError, ValueError):
            out.append(None)
    return (out[0], out[1], out[2], out[3])


def readings_row_from_latest(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map ``latest.json`` to one ``public.readings`` row (``schemas/readings.sql``)."""
    serial = _cloud_device_serial()
    if serial is None:
        return None
    c1, c2, c3, c4 = channel_mas_from_snapshot(snapshot)
    return {
        "serial": serial,
        "observed_at": _snapshot_time_iso_utc(snapshot),
        "polarization_mv": _polarization_mv_int(snapshot),
        "channel_1_ma": c1,
        "channel_2_ma": c2,
        "channel_3_ma": c3,
        "channel_4_ma": c4,
    }


def telemetry_points_row_from_latest(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map a ``latest.json``-style dict to one ``public.telemetry_points`` row (PostgREST).

    Canonical columns: ``serial``, ``time``, ``shift_mV``, ``total_mA``, ``payload_json``
    (see ``schemas/devices.sql``). Returns ``None`` if ``COILSHIELD_SERIAL`` is unset or
    too short for ``devices.serial`` (≥ 8 chars).
    """
    serial = _cloud_device_serial()
    if serial is None:
        return None
    time_iso = _snapshot_time_iso_utc(snapshot)
    shift = snapshot.get("ref_shift_mv")
    if shift is None:
        shift = snapshot.get("shift_mv")
    total = snapshot.get("total_ma")
    return {
        "serial": serial,
        "time": time_iso,
        "shift_mV": float(shift) if shift is not None else None,
        "total_mA": float(total) if total is not None else None,
        "payload_json": json.dumps(snapshot, separators=(",", ":"), default=str),
    }


def _create_supabase_client() -> Client | None:
    """Same auth rules as ``cloud_worker`` — anon + JWT when bound, else service role."""
    if not supabase_url():
        return None
    try:
        from supabase import create_client
    except ImportError:
        return None
    try:
        from cloud_bootstrap import current_device_jwt
    except ImportError:
        current_device_jwt = lambda: None  # type: ignore[misc, assignment]

    jwt = current_device_jwt()
    if jwt:
        anon = supabase_anon_key() or supabase_service_key()
        client = create_client(supabase_url(), anon)
        try:
            client.postgrest.auth(jwt)
        except Exception:
            pass
        return client
    key = (
        (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        or (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    )
    if not key:
        return None
    return create_client(supabase_url(), key)


def get_supabase_client() -> Client | None:
    """Return a Supabase client, or ``None`` if misconfigured or ``supabase`` is not installed."""
    if not is_supabase_configured():
        return None
    return _create_supabase_client()


def supabase_ping() -> tuple[bool, str]:
    """
    Cheap connectivity + auth check (Storage list buckets — no app tables required).

    Returns ``(ok, message)``. Never includes secrets in ``message``.
    """
    if not is_supabase_configured():
        return (
            False,
            "SUPABASE_URL and auth not set (service role on bench, or anon key + "
            "per-device JWT after install-code)",
        )
    try:
        from supabase import create_client
    except ImportError as e:
        return False, f"supabase package missing: {e} (pip install -e '.[supabase]')"
    client = _create_supabase_client()
    if client is None:
        return False, "could not build Supabase client"
    try:
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
