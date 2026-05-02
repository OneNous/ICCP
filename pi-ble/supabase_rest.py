#!/usr/bin/env python3
"""PostgREST client for bench Pi using urllib (stdlib only).

Mirrors ``scripts/smoke_service_role_reading.sh`` — use **service role** only on the
device / bench host (see ``device-firmware/README_SUPABASE_SMOKE.md``).

Environment:
  SUPABASE_URL           e.g. https://<ref>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY
  COILSHIELD_SERIAL      default device serial (8+ chars)
  SMOKE_TECH_ID          default tech_id text for devices row (UUID string)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping


def _env_url() -> str:
    u = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    if u:
        return u
    ref = (os.environ.get("SUPABASE_PROJECT_REF") or "kaolilojljtxnngwasmi").strip()
    return f"https://{ref}.supabase.co"


def _headers() -> dict[str, str]:
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not key:
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY is required")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _post_json(path: str, body: list[Mapping[str, Any]] | Mapping[str, Any], *, prefer: str | None = None) -> tuple[int, str]:
    url = f"{_env_url()}/rest/v1/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8")
    headers = _headers()
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), raw
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return e.code, err_body


def upsert_device(
    serial: str,
    tech_id: str,
    *,
    install_date: str = "2026-01-01",
    connection_state: str = "online",
) -> tuple[int, str]:
    row = {
        "serial": serial,
        "install_date": install_date,
        "tech_id": tech_id,
        "connection_state": connection_state,
    }
    return _post_json("devices", [row], prefer="resolution=merge-duplicates")


def insert_reading(
    serial: str,
    observed_at: str,
    *,
    polarization_mv: int = -850,
    channel_1_ma: float = 1.2,
) -> tuple[int, str]:
    row = {
        "serial": serial,
        "observed_at": observed_at,
        "polarization_mv": polarization_mv,
        "channel_1_ma": channel_1_ma,
    }
    return _post_json("readings", [row], prefer="return=minimal")


def insert_event(
    serial: str,
    event_type: str,
    *,
    severity: str = "INFO",
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, str]:
    row = {
        "serial": serial,
        "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
        "event_type": event_type,
        "severity": severity,
        "payload": dict(payload or {}),
    }
    return _post_json("events", [row], prefer="return=minimal")


def cmd_smoke(args: argparse.Namespace, log: logging.Logger) -> int:
    serial = args.serial or os.environ.get("COILSHIELD_SERIAL", "SMOKE00000001").strip()
    tech = args.tech_id or os.environ.get("SMOKE_TECH_ID", "00000000-0000-0000-0000-000000000001").strip()
    code, body = upsert_device(serial, tech)
    log.info("devices upsert HTTP %s %s", code, body[:500])
    if code >= 400:
        return 1
    obs = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    code2, body2 = insert_reading(serial, obs, polarization_mv=args.polarization_mv, channel_1_ma=args.channel_1_ma)
    log.info("readings insert HTTP %s %s", code2, body2[:500])
    if code2 >= 400:
        return 1
    code3, body3 = insert_event(serial, "bench.smoke", payload={"source": "supabase_rest.py"})
    log.info("events insert HTTP %s %s", code3, body3[:500])
    if code3 >= 400:
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Pi bench → Supabase PostgREST (service role)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("smoke", help="Upsert device + insert one reading + one event (bench)")
    s.add_argument("--serial", default=None)
    s.add_argument("--tech-id", default=None, dest="tech_id")
    s.add_argument("--polarization-mv", type=int, default=-850)
    s.add_argument("--channel-1-ma", type=float, default=1.2)
    s.set_defaults(_run=cmd_smoke)

    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("supabase_rest")
    fn = getattr(args, "_run", None)
    if fn is None:
        p.error("missing handler")
    return int(fn(args, log))


if __name__ == "__main__":
    sys.exit(main())
