#!/usr/bin/env python3
"""
Publish Pi `latest.json` snapshot to MQTT on an interval (LOG_INTERVAL seconds).

Topic: devices/{serial}/telemetry
Fleet stub and AWS IoT rules can fan this to InfluxDB / APIs.

Env: COILSHIELD_SERIAL, LATEST_JSON path, LOG_INTERVAL, MQTT_HOST, MQTT_PORT, TLS certs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

TOPIC_TMPL = "devices/{serial}/telemetry"


def load_latest(path: Path) -> dict:
    if not path.is_file():
        return {"error": "missing_latest", "path": str(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"error": "invalid_json", "detail": str(e)}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("mqtt_telemetry")
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default=os.environ.get("COILSHIELD_SERIAL", "CS-00-00-00-00"))
    p.add_argument(
        "--latest",
        type=Path,
        default=Path(os.environ.get("LATEST_JSON", "/var/lib/coils/latest.json")),
    )
    p.add_argument("--interval", type=float, default=float(os.environ.get("LOG_INTERVAL", "5")))
    p.add_argument("--host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    args = p.parse_args()
    topic = TOPIC_TMPL.format(serial=args.serial)
    cid = f"pi-telemetry-{uuid.uuid4().hex[:8]}"
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)  # type: ignore[attr-defined]
    except AttributeError:
        client = mqtt.Client(client_id=cid)
    if args.port == 8883:
        ca = os.environ.get("MQTT_CA")
        cert = os.environ.get("MQTT_CERT")
        key = os.environ.get("MQTT_KEY")
        if not all([ca, cert, key]):
            log.error("TLS port 8883 requires MQTT_CA, MQTT_CERT, MQTT_KEY")
            raise SystemExit(2)
        client.tls_set(ca_certs=ca, certfile=cert, keyfile=key)
    client.connect(args.host, args.port, keepalive=60)
    client.loop_start()
    log.info("Publishing %s every %ss", topic, args.interval)
    try:
        while True:
            payload = load_latest(args.latest)
            payload.setdefault("serial", args.serial)
            payload["time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            body = json.dumps(payload, separators=(",", ":"))
            client.publish(topic, body, qos=0)
            log.debug("published %s", body[:200])
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
