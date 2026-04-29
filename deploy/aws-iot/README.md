# AWS IoT Core TLS layout (on-device)

Place files here (default **`/etc/iccp/aws-iot/`**) so `iccp-commission-mqtt` and `iccp-telemetry-mqtt` can use mutual TLS.

| File | Env override | Purpose |
|------|----------------|---------|
| `AmazonRootCA1.pem` | `ICCP_IOT_CA_PATH` | Amazon Root CA 1 (download from AWS). |
| `device.pem.crt` | `ICCP_IOT_CERT_PATH` | Device certificate from fleet provisioning / JITP. |
| `private.pem.key` | `ICCP_IOT_KEY_PATH` | Device private key (0600). |

Broker hostname: **`ICCP_IOT_ENDPOINT`** or **`ICCP_MQTT_HOST`** (e.g. `xxxxxx-ats.iot.us-east-1.amazonaws.com`). Port: **`ICCP_IOT_PORT`** (default `8883`).

After **`iccp-cloud-register`**, if the API returns `mqtt_endpoint` / `mqtt_port` in JSON, they are stored in **`/etc/iccp/cloud.conf`** and picked up automatically unless **`ICCP_MERGE_CLOUD_CONF=0`**.

```bash
sudo mkdir -p /etc/iccp/aws-iot
sudo install -m 0644 AmazonRootCA1.pem /etc/iccp/aws-iot/
sudo install -m 0644 device.pem.crt /etc/iccp/aws-iot/
sudo install -m 0600 private.pem.key /etc/iccp/aws-iot/
```

Run **`iccp-edge-doctor --strict`** to verify endpoint + files before enabling MQTT units.
