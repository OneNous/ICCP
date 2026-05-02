"""Per-device JWT bootstrap (claude.md / plan §4).

Pi-side companion to ``supabase/functions/device-register``. The bench installer
ships every Pi with only ``SUPABASE_URL`` and ``SUPABASE_ANON_KEY`` in
``/etc/coilshield/env`` — never the shared service-role key. On first field
boot:

1. The BLE provisioning service receives an install-code write from the tech app
   (see ``packages/api-contract/ble-protocol.md`` ``cmd:0x05`` ``set_install_code``).
2. ``redeem_install_code(code)`` POSTs to ``${SUPABASE_URL}/functions/v1/device-register``
   with ``{ serial, install_code }``. The Edge Function validates the code, upserts
   the ``devices`` row, and returns ``{ token, exp, devices_serial }``.
3. ``persist_jwt()`` writes ``/etc/coilshield/cloud.conf`` (mode 0600, owned by
   ``coilshield``) with the per-device JWT and its expiry.
4. The BLE provisioning unit removes ``/etc/coilshield/ble_provision.enable`` so
   it doesn't re-advertise on next reboot. ``cloud_worker`` flushes the queue
   under the new JWT.

Subsequent boots simply read the cached JWT. When it is within
``RENEWAL_WINDOW_S`` of expiry, the controller re-runs ``redeem_install_code`` if
a new code is written, else logs a warning. (Automatic rotation is plan
out-of-scope.)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

_LOG = logging.getLogger(__name__)

DEFAULT_CONF_PATH = Path("/etc/coilshield/cloud.conf")
BLE_FLAG_PATH = Path("/etc/coilshield/ble_provision.enable")

DEFAULT_TIMEOUT_S = 10.0
RENEWAL_WINDOW_S = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class CloudCredentials:
    """Per-device JWT plus the serial it was minted for."""

    token: str
    exp: int
    serial: str
    tech_id: str | None = None

    def is_expired(self, now: float | None = None) -> bool:
        ts = float(now) if now is not None else time.time()
        return self.exp <= ts

    def needs_renewal(self, now: float | None = None) -> bool:
        ts = float(now) if now is not None else time.time()
        return self.exp - ts <= RENEWAL_WINDOW_S


class BootstrapError(RuntimeError):
    """Raised when the Edge Function exchange fails — caller decides retry vs. surface."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def _conf_path() -> Path:
    """Look up the cloud-conf path at call time so tests can override it."""
    override = os.environ.get("COILSHIELD_CLOUD_CONF")
    return Path(override) if override else DEFAULT_CONF_PATH


def _ble_flag_path() -> Path:
    override = os.environ.get("COILSHIELD_BLE_PROVISION_FLAG")
    return Path(override) if override else BLE_FLAG_PATH


def _supabase_anon_key() -> str:
    """Anon key only — service role must NEVER live on a customer Pi."""
    return (
        os.environ.get("SUPABASE_ANON_KEY", "")
        or os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    ).strip()


def _supabase_url() -> str:
    return (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")


def load_jwt(path: Path | None = None) -> CloudCredentials | None:
    """Read ``cloud.conf`` if present and parseable; return ``None`` otherwise."""
    p = path or _conf_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        _LOG.warning("cloud.conf at %s is not valid JSON: %s", p, e)
        return None
    token = str(data.get("token") or "").strip()
    serial = str(data.get("serial") or "").strip()
    tid = str(data.get("tech_id") or "").strip() or None
    exp_raw = data.get("exp")
    if not token or not serial or not isinstance(exp_raw, (int, float)):
        return None
    return CloudCredentials(token=token, exp=int(exp_raw), serial=serial, tech_id=tid)


def persist_jwt(creds: CloudCredentials, path: Path | None = None) -> Path:
    """Write the per-device JWT to ``cloud.conf`` (mode 0600, never world-readable)."""
    p = path or _conf_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "token": creds.token,
            "exp": int(creds.exp),
            "serial": creds.serial,
            **({"tech_id": creds.tech_id} if creds.tech_id else {}),
        },
        separators=(",", ":"),
    )
    p.write_text(payload, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def disable_ble_provisioning(path: Path | None = None) -> bool:
    """Remove the BLE-provision flag so ``coilshield-ble-provision.service`` no-ops next start."""
    p = path or _ble_flag_path()
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        _LOG.warning("could not remove %s: %s", p, e)
        return False


def _post_register(url: str, payload: dict[str, Any], anon_key: str, *, timeout_s: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {anon_key}",
            "apikey": anon_key,
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib_error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        status = e.code
    except (urllib_error.URLError, TimeoutError) as e:
        raise BootstrapError("network_error", f"{type(e).__name__}: {e}") from e

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise BootstrapError("invalid_response", f"non-JSON body (status {status})", status=status)
    if status >= 400 or not data.get("ok"):
        raise BootstrapError(
            str(data.get("code") or "edge_function_error"),
            str(data.get("message") or raw or f"status {status}"),
            status=status,
        )
    return data


def redeem_install_code(
    install_code: str,
    *,
    serial: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    persist: bool = True,
) -> CloudCredentials:
    """Exchange a BLE-supplied install code for a per-device JWT.

    Caller responsibility: only invoke from a single thread (typically the BLE
    GATT command-write handler), and pass the same ``serial`` that was advertised
    over BLE so the Edge Function can pin the row.
    """
    code = (install_code or "").strip()
    if not code:
        raise BootstrapError("missing_install_code", "install_code is required")

    if serial is None:
        from device_identity import derive_device_serial, has_valid_serial

        serial = derive_device_serial()
        if not has_valid_serial(serial):
            raise BootstrapError("short_serial", "device serial is too short for devices.serial CHECK")

    base = _supabase_url()
    anon = _supabase_anon_key()
    if not base or not anon:
        raise BootstrapError("supabase_misconfigured", "SUPABASE_URL or SUPABASE_ANON_KEY missing")

    url = f"{base}/functions/v1/device-register"
    data = _post_register(
        url,
        {"serial": serial, "install_code": code},
        anon,
        timeout_s=timeout_s,
    )

    token = str(data.get("token") or "").strip()
    exp = data.get("exp")
    if not token or not isinstance(exp, (int, float)):
        raise BootstrapError("invalid_response", "edge function did not return token + exp")
    tid = str(data.get("tech_id") or "").strip() or None
    creds = CloudCredentials(token=token, exp=int(exp), serial=serial, tech_id=tid)

    if persist:
        persist_jwt(creds)
        disable_ble_provisioning()
    return creds


def current_device_jwt() -> str | None:
    """Return a non-expired per-device JWT, or ``None`` if missing/expired."""
    creds = load_jwt()
    if creds is None or creds.is_expired():
        return None
    return creds.token
