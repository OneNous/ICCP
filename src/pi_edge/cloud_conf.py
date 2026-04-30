"""
Read persisted ``/etc/iccp/cloud.conf`` (from ``iccp-cloud-register``).

Used to fill MQTT broker host/port when env vars are unset (post-register bootstrap).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def cloud_conf_path() -> Path:
    return Path(
        (os.environ.get("ICCP_CLOUD_CONF") or "/etc/iccp/cloud.conf").strip()
    ).expanduser()


def load_cloud_conf(path: Path | None = None) -> dict[str, Any]:
    """Return parsed JSON or ``{}`` if missing / unreadable."""
    p = path or cloud_conf_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def mqtt_endpoint_from_conf(conf: dict[str, Any]) -> str:
    for k in ("mqtt_endpoint", "mqtt_host", "ICCP_IOT_ENDPOINT"):
        v = conf.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def mqtt_port_from_conf(conf: dict[str, Any], default: int = 8883) -> int:
    raw = conf.get("mqtt_port")
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
