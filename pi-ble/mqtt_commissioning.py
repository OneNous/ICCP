#!/usr/bin/env python3
"""
Publish commissioning progress to AWS IoT Core / Mosquitto (stub-compatible).

Topic: devices/{serial}/commissioning
Payload JSON: step, percent, status (pending|running|passed|failed), optional error.

Env: MQTT_HOST, MQTT_PORT (8883 TLS or 1883 plain), COILSHIELD_SERIAL, MQTT_CA,
     MQTT_CERT, MQTT_KEY for TLS client auth.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid

import paho.mqtt.client as mqtt

TOPIC_TMPL = "devices/{serial}/commissioning"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("mqtt_commissioning")
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default=os.environ.get("COILSHIELD_SERIAL", "CS-00-00-00-00"))
    p.add_argument("--host", default=os.environ.get("MQTT_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--demo", action="store_true", help="Publish a short demo sequence then exit")
    args = p.parse_args()
    serial = args.serial
    topic = TOPIC_TMPL.format(serial=serial)
    cid = f"pi-commission-{uuid.uuid4().hex[:8]}"
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

    def pub(payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":"))
        log.info("publish %s %s", topic, body)
        client.publish(topic, body, qos=1)

    if args.demo:
        pub({"step": "self_test", "percent": 10, "status": "running"})
        time.sleep(0.4)
        pub({"step": "calibration", "percent": 55, "status": "running"})
        time.sleep(0.4)
        pub({"step": "done", "percent": 100, "status": "passed"})
        client.loop_stop()
        client.disconnect()
        return

    log.info("Connected; publish progress by calling pub() from your commissioning runner.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
