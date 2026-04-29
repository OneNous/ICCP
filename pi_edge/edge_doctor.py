"""
On-device diagnostics for Pi edge: identity, optional deps, paths, MQTT readiness.

Exit ``0`` always unless ``--strict`` is set and MQTT would be misconfigured
(no endpoint after env + ``cloud.conf``, or missing cert files when endpoint set).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from pi_edge import cloud_conf as cc
from pi_edge.device_identity import device_serial
from pi_edge.mqtt_client import iot_paths_resolved


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError, AttributeError):
        return False


def _collect() -> dict[str, Any]:
    end, ca, cert, key, port = iot_paths_resolved()
    cpath = cc.cloud_conf_path()
    conf = cc.load_cloud_conf()
    log_raw = (
        os.environ.get("COILSHIELD_LOG_DIR") or os.environ.get("ICCP_LOG_DIR") or ""
    ).strip()
    latest: Path | None
    if log_raw:
        latest = Path(log_raw).expanduser() / "latest.json"
    else:
        try:
            from config.settings import LOG_DIR

            latest = Path(LOG_DIR) / "latest.json"
        except Exception:
            latest = None

    def stat(p: Path | None) -> dict[str, Any]:
        if p is None:
            return {"path": None, "exists": False}
        return {"path": str(p.resolve()), "exists": p.is_file()}

    return {
        "platform": {
            "system": sys.platform,
            "executable": sys.executable,
        },
        "identity": {"device_serial": device_serial()},
        "imports": {
            "paho_mqtt": _has_module("paho.mqtt.client"),
            "dbus": _has_module("dbus"),
            "gi_glib": _has_module("gi.repository.GLib"),
            "inotify_simple": _has_module("inotify_simple"),
        },
        "executables": {
            name: shutil.which(name)
            for name in (
                "iccp",
                "wpa_cli",
                "nmcli",
                "bluetoothctl",
                "busctl",
                "iccp-ble-provision",
                "iccp-cloud-register",
                "iccp-commission-mqtt",
                "iccp-telemetry-mqtt",
            )
        },
        "cloud_conf": {
            "path": str(cpath),
            "exists": cpath.is_file(),
            "keys": sorted(conf.keys()) if conf else [],
            "mqtt_hint": cc.mqtt_endpoint_from_conf(conf) or None,
        },
        "mqtt_tls": {
            "endpoint": end or None,
            "port": port,
            "ca": stat(Path(ca)),
            "cert": stat(Path(cert)),
            "key": stat(Path(key)),
        },
        "telemetry": {
            "latest_json": stat(latest),
            "log_dir_env_set": bool(log_raw),
        },
        "ble": {
            "provisioning_env": os.environ.get("ICCP_BLE_PROVISIONING"),
            "flag_default": "/etc/iccp/ble_provision.enable",
            "flag_exists": Path(
                os.environ.get("ICCP_BLE_PROVISION_FLAG")
                or "/etc/iccp/ble_provision.enable"
            ).expanduser().is_file(),
        },
        "wifi_backend": (
            os.environ.get("COILSHIELD_WIFI_BACKEND")
            or os.environ.get("ICCP_WIFI_BACKEND")
            or "wpa_cli"
        ),
    }


def _strict_mqtt_ok(rep: dict[str, Any]) -> tuple[bool, list[str]]:
    errs: list[str] = []
    m = rep["mqtt_tls"]
    ep = m.get("endpoint")
    if not ep:
        errs.append("no MQTT endpoint (set ICCP_IOT_ENDPOINT / ICCP_MQTT_HOST or register device)")
    for label in ("ca", "cert", "key"):
        if not m[label]["exists"]:
            errs.append(f"TLS file missing: {m[label]['path']}")
    return (len(errs) == 0, errs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ICCP Pi edge diagnostics (BLE / cloud / MQTT paths).",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON object (automation-friendly)",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if MQTT TLS is not fully configured (endpoint + three PEM paths)",
    )
    ns = ap.parse_args(argv)
    rep = _collect()
    if ns.json:
        print(json.dumps(rep, indent=2))
    else:
        print("=== ICCP Pi edge doctor ===")
        print(f"serial:        {rep['identity']['device_serial']}")
        print(f"platform:      {rep['platform']['system']}")
        print(f"wifi backend:  {rep['wifi_backend']}")
        print("--- imports ---")
        for k, v in rep["imports"].items():
            print(f"  {k}: {'yes' if v else 'no'}")
        print("--- tools ---")
        for k, v in rep["executables"].items():
            print(f"  {k}: {v or '(not on PATH)'}")
        print("--- cloud.conf ---")
        ccinfo = rep["cloud_conf"]
        print(f"  path:   {ccinfo['path']}  exists={ccinfo['exists']}")
        if ccinfo.get("mqtt_hint"):
            print(f"  mqtt:   {ccinfo['mqtt_hint']}")
        m = rep["mqtt_tls"]
        print("--- MQTT TLS ---")
        print(f"  endpoint: {m['endpoint'] or '(unset)'}  port={m['port']}")
        for label in ("ca", "cert", "key"):
            s = m[label]
            print(f"  {label}: {s['path']}  exists={s['exists']}")
        tel = rep["telemetry"]
        print("--- telemetry ---")
        print(f"  LOG_DIR set: {tel['log_dir_env_set']}")
        lj = tel["latest_json"]
        print(f"  latest.json: {lj['path']}  exists={lj['exists']}")
        ble = rep["ble"]
        print("--- BLE provision ---")
        print(f"  ICCP_BLE_PROVISIONING={ble['provisioning_env']!r}")
        print(f"  flag file exists: {ble['flag_exists']} (default {ble['flag_default']})")

    if ns.strict:
        ok, errs = _strict_mqtt_ok(rep)
        if not ok:
            for e in errs:
                print(f"iccp-edge-doctor: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
