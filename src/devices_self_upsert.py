"""Best-effort POST /rest/v1/devices on controller startup (claude.md / plan §5).

Goal: if the row in ``public.devices`` for this Pi is missing — accidental delete,
freshly-restored database, or commissioning happened before the table existed —
re-create it on first telemetry tick instead of waiting for the next BLE
re-provisioning. Uses ``Prefer: resolution=merge-duplicates`` so the call is a
true upsert and never overwrites an existing row's mutable fields.

Called once at controller startup from ``iccp_runtime.run_iccp_forever``. Failures
are logged at info level and otherwise ignored — telemetry inserts will fail loudly
on their own if the row truly doesn't exist.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

_LOG = logging.getLogger(__name__)

_TIMEOUT_S = 8.0


def _resolve_credentials() -> tuple[str, str, str | None] | None:
    """Return ``(url, bearer, anon_key)`` or ``None`` when unconfigured.

    ``bearer`` is the per-device JWT if present, otherwise the service-role key
    (bench/dev only). ``anon_key`` is set only in the JWT case so PostgREST
    receives a valid ``apikey`` header.
    """
    import cloud_bootstrap
    import cloud_sync

    url = cloud_sync.supabase_url().rstrip("/")
    if not url:
        return None
    jwt = cloud_bootstrap.current_device_jwt()
    if jwt:
        anon = cloud_sync.supabase_anon_key()
        if not anon:
            return None
        return url, jwt, anon
    service = cloud_sync.supabase_service_key()
    if not service:
        return None
    return url, service, None


def _post_devices(url: str, body: dict[str, Any], bearer: str, anon: str | None, *, timeout_s: float) -> tuple[int, str]:
    headers = {
        "Authorization": f"Bearer {bearer}",
        "apikey": anon if anon else bearer,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    req = urllib_request.Request(
        f"{url}/rest/v1/devices",
        data=json.dumps([body]).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else ""
    except (urllib_error.URLError, TimeoutError) as e:
        return 0, f"{type(e).__name__}: {e}"


def upsert_self(*, timeout_s: float = _TIMEOUT_S) -> tuple[bool, str]:
    """Upsert the local device's row. Returns ``(ok, msg)``.

    ``ok = True`` includes both "already existed" (200) and "created" (201). Network
    errors are returned as ``(False, msg)`` and the caller is expected to swallow.
    """
    from device_identity import derive_device_serial, has_valid_serial

    serial = derive_device_serial()
    if not has_valid_serial(serial):
        return False, f"unstable serial '{serial}' — skipping devices upsert"

    creds = _resolve_credentials()
    if creds is None:
        return False, "supabase not configured (no JWT and no service-role key)"
    url, bearer, anon = creds

    tech_id = (os.environ.get("COILSHIELD_TECH_ID") or "").strip()
    if not tech_id:
        try:
            from cloud_bootstrap import load_jwt

            creds = load_jwt()
            if creds and creds.tech_id:
                tech_id = creds.tech_id.strip()
        except ImportError:
            pass
    body: dict[str, Any] = {
        "serial": serial,
        "install_date": date.today().isoformat(),
        "connection_state": "online",
    }
    # tech_id is NOT NULL — only include it when we have a real value (per-device JWT
    # path doesn't change tech_id on subsequent boots; merge-duplicates leaves it alone).
    if tech_id:
        body["tech_id"] = tech_id

    code, raw = _post_devices(url, body, bearer, anon, timeout_s=timeout_s)
    if 200 <= code < 300:
        return True, f"devices upsert OK (HTTP {code})"
    if code == 0:
        return False, raw
    return False, f"devices upsert HTTP {code}: {raw[:300]}"


def upsert_self_safe() -> None:
    """Fire-and-forget wrapper for controller startup; never raises."""
    try:
        ok, msg = upsert_self()
    except Exception as e:
        _LOG.info("devices self-upsert raised %s: %s", type(e).__name__, e)
        return
    level = logging.INFO if ok else logging.INFO
    _LOG.log(level, "devices self-upsert: %s", msg)
