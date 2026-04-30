"""Tech app HTTP API — Flask blueprint (``.claude/tech-api.md``).

Mounted at ``/tech`` on the dashboard ``app`` when ``config.settings.TECH_API_ENABLED``
is true. HMAC uses ``COILSHIELD_TECH_BOND_KEY`` (hex-encoded secret bytes) until a BLE
bond store exists.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import importlib.metadata
import json
import os
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

import config.settings as cfg

tech_bp = Blueprint("tech_api", __name__, url_prefix="/tech")


def _bond_key_bytes() -> bytes | None:
    hx = (getattr(cfg, "TECH_BOND_KEY_HEX", "") or "").strip()
    if not hx:
        return None
    try:
        return binascii.unhexlify(hx.replace(" ", ""))
    except (binascii.Error, TypeError, ValueError):
        return None


def _uptime_seconds() -> int:
    p = Path("/proc/uptime")
    try:
        line = p.read_text(encoding="utf-8")
        return int(float(line.split()[0]))
    except (OSError, ValueError, IndexError):
        return int(time.monotonic())


def _firmware_version() -> str:
    try:
        return importlib.metadata.version("coilshield-iccp")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _read_latest_telemetry() -> dict[str, Any] | None:
    path = Path(cfg.LOG_DIR) / getattr(cfg, "LATEST_JSON_NAME", "latest.json")
    try:
        raw = path.read_text(encoding="utf-8")
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _verify_hmac() -> tuple[Any, int] | None:
    """Return ``(jsonify(...), code)`` on failure; ``None`` if OK."""
    key = _bond_key_bytes()
    if key is None:
        return jsonify({"error": "TECH_BOND_KEY not configured (set COILSHIELD_TECH_BOND_KEY)"}), 501

    sig_h = (request.headers.get("X-CoilShield-Signature") or "").strip()
    ts_raw = (request.headers.get("X-CoilShield-Timestamp") or "").strip()
    if not sig_h or not ts_raw.isdigit():
        return jsonify({"error": "missing X-CoilShield-Signature or X-CoilShield-Timestamp"}), 401
    ts = int(ts_raw)
    skew = float(getattr(cfg, "TECH_HMAC_MAX_SKEW_S", 300) or 300)
    if abs(int(time.time()) - ts) > skew:
        return jsonify({"error": "timestamp outside allowed skew"}), 401

    body = request.get_data(cache=False)
    canonical = f"{ts}\n".encode("utf-8") + body
    expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_h.lower(), expected.lower()):
        return jsonify({"error": "invalid signature"}), 401
    return None


@tech_bp.get("/info")
def tech_info() -> Any:
    """Unauthenticated discovery (TA-3)."""
    serial = (os.environ.get("COILSHIELD_SERIAL") or "").strip() or os.uname().nodename
    return jsonify(
        {
            "serial": serial,
            "firmware_version": _firmware_version(),
            "hardware_revision": (os.environ.get("COILSHIELD_HW_REV") or "unknown").strip(),
            "uptime_seconds": _uptime_seconds(),
            "ble_protocol_version": "1.0.0",
        }
    )


@tech_bp.get("/status")
def tech_status() -> Any:
    err = _verify_hmac()
    if err is not None:
        return err
    snap = _read_latest_telemetry()
    if snap is None:
        return jsonify({"error": "telemetry not available"}), 503
    return jsonify({"latest": snap})


@tech_bp.post("/commission")
def tech_commission() -> Any:
    err = _verify_hmac()
    if err is not None:
        return err
    return jsonify({"error": "commission via tech API not implemented yet"}), 501


@tech_bp.get("/commission/status")
def tech_commission_status() -> Any:
    err = _verify_hmac()
    if err is not None:
        return err
    return jsonify({"error": "not implemented yet"}), 501


@tech_bp.post("/clear-fault")
def tech_clear_fault() -> Any:
    err = _verify_hmac()
    if err is not None:
        return err
    return jsonify({"error": "not implemented yet"}), 501


@tech_bp.get("/events")
def tech_events() -> Any:
    err = _verify_hmac()
    if err is not None:
        return err
    return jsonify({"error": "not implemented yet"}), 501
