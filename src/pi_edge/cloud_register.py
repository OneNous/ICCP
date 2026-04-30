"""
HTTPS device registration client — persists ``/etc/iccp/cloud.conf`` (0600).

POST JSON to the cloud ``/devices/register`` endpoint with exponential backoff.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from pi_edge.device_identity import device_serial


def _register_bearer_token() -> str:
    t = (os.environ.get("ICCP_CLOUD_REGISTER_TOKEN") or "").strip()
    if t:
        return t
    path = (os.environ.get("ICCP_CLOUD_REGISTER_TOKEN_FILE") or "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _cloud_conf_path() -> Path:
    raw = (os.environ.get("ICCP_CLOUD_CONF") or "/etc/iccp/cloud.conf").strip()
    return Path(raw).expanduser()


def _register_url() -> str:
    full = (os.environ.get("ICCP_CLOUD_REGISTER_URL") or "").strip()
    if full:
        return full
    base = (os.environ.get("ICCP_CLOUD_API_URL") or "").strip().rstrip("/")
    if not base:
        raise ValueError("Set ICCP_CLOUD_API_URL (e.g. https://api.example.com)")
    if base.endswith("/devices/register"):
        return base
    return f"{base}/devices/register"


def _firmware_version() -> str:
    try:
        from importlib.metadata import version

        return version("coilshield-iccp")
    except Exception:
        return "unknown"


def _http_json(
    url: str,
    body: dict[str, Any],
    *,
    method: str = "POST",
    timeout_s: float = 30.0,
) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8")
    m = (method or "POST").strip().upper()
    if m not in ("POST", "PUT"):
        m = "POST"
    req = urllib.request.Request(
        url,
        data=data,
        method=m,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    token = _register_bearer_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read() if e.fp else b""


def register_once(
    *,
    url: str | None = None,
    max_attempts: int = 8,
    initial_backoff_s: float = 2.0,
    max_backoff_s: float = 120.0,
) -> dict[str, Any]:
    """
    POST ``{serial, install_date, firmware_version, ...}``; return merged
    persisted config including any JSON fields returned by the server body.
    """
    serial = device_serial()
    install_date = (os.environ.get("ICCP_INSTALL_DATE") or "").strip()
    if not install_date:
        install_date = time.strftime("%Y-%m-%d", time.gmtime())

    payload: dict[str, Any] = {
        "serial": serial,
        "install_date": install_date,
        "firmware_version": _firmware_version(),
    }
    loc = (os.environ.get("ICCP_DEVICE_LOCATION") or "").strip()
    if loc:
        payload["location"] = loc
    tid = (os.environ.get("ICCP_TECH_ID") or "").strip()
    if tid:
        payload["tech_id"] = tid

    endpoint = url or _register_url()
    attempt = 0
    backoff = initial_backoff_s
    last_err: str | None = None

    while attempt < max_attempts:
        attempt += 1
        try:
            reg_method = (
                os.environ.get("ICCP_CLOUD_REGISTER_METHOD") or "POST"
            ).strip().upper()
            code, raw = _http_json(
                endpoint, payload, method=reg_method, timeout_s=30.0
            )
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = str(e)
            time.sleep(min(max_backoff_s, backoff) * (0.8 + 0.4 * random.random()))
            backoff = min(max_backoff_s, backoff * 2)
            continue

        body: dict[str, Any] = {}
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                body = {"raw_response": raw.decode("utf-8", errors="replace")[:4000]}

        # 200/201 success; 409 idempotent "already registered" if server returns token
        if code in (200, 201) or (code == 409 and isinstance(body, dict) and body):
            base = endpoint
            if "/devices/register" in base:
                api_base = base.rsplit("/devices/register", 1)[0]
            else:
                parts = urlsplit(base)
                api_base = f"{parts.scheme}://{parts.netloc}".rstrip("/")
            conf = {
                "api_base": api_base,
                "serial": serial,
                "registered_at_unix": time.time(),
                "last_http_status": code,
            }
            if isinstance(body, dict):
                for k in (
                    "device_token",
                    "token",
                    "mqtt_host",
                    "mqtt_endpoint",
                    "mqtt_port",
                ):
                    if k in body and body[k] is not None:
                        conf[k] = body[k]
                conf["register_response"] = body
            return conf

        last_err = f"HTTP {code}: {raw[:500]!r}"
        time.sleep(min(max_backoff_s, backoff) * (0.8 + 0.4 * random.random()))
        backoff = min(max_backoff_s, backoff * 2)

    raise RuntimeError(last_err or "register failed")


def persist_cloud_conf(conf: dict[str, Any], path: Path | None = None) -> None:
    path = path or _cloud_conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(conf, indent=2, sort_keys=True).encode("utf-8")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ICCP HTTPS device registration")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payload and serial only; no HTTP or file write",
    )
    ap.add_argument(
        "--conf",
        type=Path,
        default=None,
        help="Override cloud.conf path (default: ICCP_CLOUD_CONF or /etc/iccp/cloud.conf)",
    )
    ns = ap.parse_args(argv)
    if ns.dry_run:
        serial = device_serial()
        install_date = (os.environ.get("ICCP_INSTALL_DATE") or "").strip()
        if not install_date:
            install_date = time.strftime("%Y-%m-%d", time.gmtime())
        preview: dict[str, Any] = {
            "serial": serial,
            "install_date": install_date,
            "firmware_version": _firmware_version(),
            "method": (os.environ.get("ICCP_CLOUD_REGISTER_METHOD") or "POST").strip(),
        }
        try:
            preview["register_url"] = _register_url()
        except ValueError as e:
            preview["register_url"] = None
            preview["register_url_error"] = str(e)
        print(json.dumps(preview, indent=2))
        return 0
    try:
        conf = register_once()
    except (RuntimeError, ValueError) as e:
        print(f"iccp-cloud-register: {e}", file=sys.stderr)
        return 1
    persist_cloud_conf(conf, ns.conf)
    print(f"iccp-cloud-register: wrote {ns.conf or _cloud_conf_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
