"""
Thin MQTT over TLS (AWS IoT–style client certs) for Pi edge publishers.
"""

from __future__ import annotations

import os
import ssl
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError as e:  # pragma: no cover
    mqtt = None  # type: ignore[assignment]
    _import_error = e
else:
    _import_error = None


def _require_paho() -> None:
    if mqtt is None:
        raise ImportError(
            "paho-mqtt is required — pip install 'coilshield-iccp[cloud]'"
        ) from _import_error


def iot_paths_from_env() -> tuple[str, str, str, str]:
    """
    Return ``(endpoint, ca_path, cert_path, key_path)`` from conventional
    ``ICCP_IOT_*`` environment variables (files under ``/etc/iccp/aws-iot/`` on device).
    """
    end = (
        os.environ.get("ICCP_IOT_ENDPOINT")
        or os.environ.get("ICCP_MQTT_HOST")
        or ""
    ).strip()
    ca = (
        os.environ.get("ICCP_IOT_CA_PATH")
        or "/etc/iccp/aws-iot/AmazonRootCA1.pem"
    ).strip()
    cert = (
        os.environ.get("ICCP_IOT_CERT_PATH")
        or "/etc/iccp/aws-iot/device.pem.crt"
    ).strip()
    key = (
        os.environ.get("ICCP_IOT_KEY_PATH")
        or "/etc/iccp/aws-iot/private.pem.key"
    ).strip()
    return end, ca, cert, key


def iot_paths_resolved() -> tuple[str, str, str, str, int]:
    """
    TLS paths from env, endpoint/port from env with optional ``cloud.conf`` fill-in.

    Set ``ICCP_MERGE_CLOUD_CONF=0`` to skip reading ``/etc/iccp/cloud.conf``.
    """
    from pi_edge.cloud_conf import load_cloud_conf, mqtt_endpoint_from_conf, mqtt_port_from_conf

    end, ca, cert, key = iot_paths_from_env()
    port_raw = os.environ.get("ICCP_IOT_PORT")
    port_from_env = port_raw is not None and str(port_raw).strip() != ""
    port = int(port_raw) if port_from_env else 8883

    if os.environ.get("ICCP_MERGE_CLOUD_CONF", "1").strip() == "0":
        return end, ca, cert, key, port

    conf = load_cloud_conf()
    if not end.strip():
        end = mqtt_endpoint_from_conf(conf)
    if not port_from_env and conf:
        port = mqtt_port_from_conf(conf, default=port)
    return end, ca, cert, key, port


class AwsIotMqttPublisher:
    """Connect once with TLS client auth; publish with optional Paho loop thread."""

    def __init__(
        self,
        *,
        client_id: str,
        endpoint: str | None = None,
        port: int | None = None,
        ca_path: str | None = None,
        cert_path: str | None = None,
        key_path: str | None = None,
    ) -> None:
        _require_paho()
        end, ca, cert, key, resolved_port = iot_paths_resolved()
        self.endpoint = (endpoint or end).strip()
        if not self.endpoint:
            raise ValueError(
                "MQTT endpoint missing — set ICCP_IOT_ENDPOINT or ICCP_MQTT_HOST "
                "(or run iccp-cloud-register and keep ICCP_MERGE_CLOUD_CONF=1)"
            )
        self.port = int(port if port is not None else resolved_port)
        self.ca_path = ca_path or ca
        self.cert_path = cert_path or cert
        self.key_path = key_path or key
        # Paho 1.x: Client(client_id=...); 2.x adds CallbackAPIVersion (optional).
        try:
            cbv = mqtt.CallbackAPIVersion.VERSION2  # type: ignore[attr-defined]
            self._client = mqtt.Client(
                cbv, client_id=client_id, protocol=mqtt.MQTTv311
            )
        except (AttributeError, TypeError):
            self._client = mqtt.Client(client_id=client_id)
        self._client.tls_set(
            ca_certs=self.ca_path,
            certfile=self.cert_path,
            keyfile=self.key_path,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

    def connect(self, keepalive: int = 60) -> None:
        assert mqtt is not None
        self._client.connect(self.endpoint, self.port, keepalive)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish(
        self, topic: str, payload: str | bytes, *, qos: int = 1, retain: bool = False
    ) -> Any:
        body: bytes | str = (
            payload if isinstance(payload, (bytes, bytearray)) else payload
        )
        return self._client.publish(topic, body, qos=qos, retain=retain)

    def wait_publish(
        self, topic: str, payload: str | bytes, *, qos: int = 1, timeout_s: float = 10.0
    ) -> None:
        info = self.publish(topic, payload, qos=qos)
        info.wait_for_publish(timeout=timeout_s)


def mqtt_client_id(serial: str, suffix: str = "edge") -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in serial)[:96]
    return f"iccp-{suffix}-{safe}"[:128]
