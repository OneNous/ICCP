"""
Watch ``latest.json`` and publish a versioned telemetry snapshot on a fixed cadence.

Default interval matches controller heavy logging (``LOG_INTERVAL_S``) via
``ICCP_TELEMETRY_INTERVAL_S`` (default 120). Keeps MQTT off the control loop.

When ``ICCP_TELEMETRY_INOTIFY=1`` and ``inotify-simple`` is installed (Linux), the
sidecar blocks on the log directory instead of blind polling (plan: mtime /
inotify).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pi_edge.device_identity import device_serial
from pi_edge.mqtt_client import AwsIotMqttPublisher, mqtt_client_id

SCHEMA = "iccp.telemetry.v1"


def _log_dir() -> Path:
    raw = (
        os.environ.get("COILSHIELD_LOG_DIR") or os.environ.get("ICCP_LOG_DIR") or ""
    ).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    try:
        from config.settings import LOG_DIR

        return Path(LOG_DIR).resolve()
    except Exception:
        return (Path.cwd() / "logs").resolve()


def _interval_s() -> float:
    raw = (os.environ.get("ICCP_TELEMETRY_INTERVAL_S") or "").strip()
    if raw:
        return max(1.0, float(raw))
    try:
        from config.settings import LOG_INTERVAL_S

        return float(LOG_INTERVAL_S)
    except Exception:
        return 120.0


def _poll_slice_s(interval: float) -> float:
    raw = (os.environ.get("ICCP_TELEMETRY_POLL_S") or "").strip()
    if raw:
        return max(0.05, float(raw))
    return min(5.0, max(0.25, interval / 4.0))


def _latest_path() -> Path:
    name = (os.environ.get("ICCP_LATEST_JSON_NAME") or "latest.json").strip()
    return _log_dir() / name


def _telemetry_topic(serial: str) -> str:
    return (os.environ.get("ICCP_MQTT_TELEMETRY_TOPIC") or "").strip() or (
        f"iccp/{serial}/telemetry/latest"
    )


def _inotify_wait_parent(path: Path, timeout_s: float) -> None:
    """Block up to ``timeout_s`` on inotify events under ``path``'s parent."""
    try:
        from inotify_simple import INotify, flags
    except ImportError:
        time.sleep(timeout_s)
        return
    ino = INotify()
    try:
        parent = str(path.parent.resolve())
        mask = flags.CLOSE_WRITE | flags.MOVED_TO | flags.MODIFY | flags.CREATE
        ino.add_watch(parent, mask)
        deadline = time.monotonic() + max(0.05, timeout_s)
        while time.monotonic() < deadline:
            ms = int(min(500, max(1, (deadline - time.monotonic()) * 1000)))
            for event in ino.read(timeout=ms):
                ename = event.name
                if ename is None:
                    return
                if isinstance(ename, bytes):
                    ename = ename.decode("utf-8", errors="replace")
                if ename == path.name or ename == "":
                    return
    finally:
        ino.close()


def _snapshot_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Curated subset for fleet / InfluxDB-style subscribers (extend with cloud)."""
    keys = (
        "ts_unix",
        "tick",
        "seq",
        "ref_raw_mv",
        "ref_shift_mv",
        "ref_status",
        "ref_hw_ok",
        "ref_hw_message",
        "ref_hint",
        "mode",
        "policy",
        "channels",
        "wet_channels",
        "all_protected",
        "temp_f",
        "native_mv",
        "diag",
        "commissioning_complete",
        "galvanic_offset_mv",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if k in raw:
            out[k] = raw[k]
    if not out:
        return dict(raw)
    return out


def run_loop(*, dry_run: bool = False) -> int:
    path = _latest_path()
    interval = _interval_s()
    serial = device_serial()
    topic = _telemetry_topic(serial)
    last_hash: str | None = None
    last_pub_mono = 0.0
    use_inotify = os.environ.get("ICCP_TELEMETRY_INOTIFY", "").strip() == "1"
    poll = _poll_slice_s(interval)

    if dry_run:
        print(
            json.dumps(
                {
                    "path": str(path),
                    "interval_s": interval,
                    "topic": topic,
                    "inotify": bool(use_inotify),
                    "poll_slice_s": poll,
                },
                indent=2,
            )
        )
        return 0

    pub: AwsIotMqttPublisher | None = None
    try:
        pub = AwsIotMqttPublisher(client_id=mqtt_client_id(serial, "telemetry"))
        pub.connect()
    except Exception as e:
        print(f"iccp-telemetry-mqtt: MQTT connect failed: {e}", file=sys.stderr)
        return 2

    try:
        while True:
            if use_inotify:
                _inotify_wait_parent(path, poll)
            else:
                time.sleep(poll)
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            now = time.monotonic()
            if now - last_pub_mono < interval:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                data = json.loads(text)
            except (OSError, json.JSONDecodeError):
                continue
            snap = _snapshot_payload(data if isinstance(data, dict) else {})
            body = {
                "schema": SCHEMA,
                "ts_unix": time.time(),
                "serial": serial,
                "latest_path": str(path),
                "source_mtime_unix": st.st_mtime,
                "snapshot": snap,
            }
            raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            if digest == last_hash:
                last_pub_mono = now
                continue
            try:
                pub.wait_publish(topic, raw, qos=1, timeout_s=15.0)
                last_hash = digest
                last_pub_mono = now
            except Exception as e:
                print(f"iccp-telemetry-mqtt: publish failed: {e}", file=sys.stderr)
    finally:
        pub.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ICCP latest.json → MQTT telemetry sidecar (AWS IoT TLS).",
        epilog=(
            "Set COILSHIELD_LOG_DIR (or ICCP_LOG_DIR) to match the controller. "
            "Optional: ICCP_TELEMETRY_INOTIFY=1 and pip install inotify-simple (Linux) "
            "for directory watches instead of fixed polling only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(argv)
    return run_loop(dry_run=ns.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
