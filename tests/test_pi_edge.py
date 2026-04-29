"""Pi edge helpers (serial + register URL) without Linux-only imports."""

from __future__ import annotations

import json
import stat

import pytest

import pi_edge.cloud_conf as cconf
import pi_edge.cloud_register as cr
import pi_edge.device_identity as di
import pi_edge.edge_doctor as ed
import pi_edge.mqtt_client as mc


def test_device_serial_from_cpuinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(di, "_read_cpuinfo_serial", lambda: "00AABBCCDD")
    monkeypatch.setattr(di, "_read_machine_id", lambda: None)
    assert di.device_serial() == "00aabbccdd"


def test_device_serial_machine_id_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(di, "_read_cpuinfo_serial", lambda: None)
    monkeypatch.setattr(di, "_read_rpi_serial_tool", lambda: None)
    monkeypatch.setattr(di, "_read_machine_id", lambda: "deadbeef" * 4)
    assert di.device_serial() == "deadbeef" * 4


def test_register_url_joins_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICCP_CLOUD_REGISTER_URL", raising=False)
    monkeypatch.setenv("ICCP_CLOUD_API_URL", "https://x.example.com/v1")
    assert cr._register_url() == "https://x.example.com/v1/devices/register"


def test_register_url_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICCP_CLOUD_REGISTER_URL", raising=False)
    monkeypatch.setenv(
        "ICCP_CLOUD_API_URL", "https://x.example.com/devices/register"
    )
    assert cr._register_url() == "https://x.example.com/devices/register"


def test_register_url_full_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICCP_CLOUD_REGISTER_URL", "https://reg.example.org/v2/enroll")
    monkeypatch.delenv("ICCP_CLOUD_API_URL", raising=False)
    assert cr._register_url() == "https://reg.example.org/v2/enroll"


def test_validate_credentials_errors() -> None:
    from pi_edge.wifi_wpa import WpaApplyError, WifiCredentials, validate_credentials

    with pytest.raises(WpaApplyError):
        validate_credentials(WifiCredentials(ssid="", password="12345678"))
    with pytest.raises(WpaApplyError):
        validate_credentials(WifiCredentials(ssid="x" * 40, password="12345678"))
    with pytest.raises(WpaApplyError):
        validate_credentials(WifiCredentials(ssid="okssid", password="short"))


def test_mqtt_line_spool(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pi_edge import mqtt_line_spool as sp

    d = tmp_path / "sp"
    monkeypatch.setenv("ICCP_COMMISSION_MQTT_SPOOL", str(d))
    sp.enqueue_line('{"a":1}')
    sp.enqueue_line('{"b":2}')
    seen: list[str] = []

    def pub(line: str) -> None:
        if "b" in line:
            raise RuntimeError("fail")
        seen.append(line)

    ok, fail = sp.drain_lines(pub)
    assert ok == 1 and fail == 1
    assert seen == ['{"a":1}']
    pending = sp.pending_path()
    assert pending is not None and pending.read_text().strip() == '{"b":2}'


def test_device_serial_vcgencmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(di, "_read_cpuinfo_serial", lambda: None)

    def fake_otp() -> str | None:
        return "abcd1234"

    monkeypatch.setattr(di, "_read_vcgencmd_serial", fake_otp)
    monkeypatch.setattr(di, "_read_rpi_serial_tool", lambda: None)
    monkeypatch.setattr(di, "_read_machine_id", lambda: None)
    assert di.device_serial() == "abcd1234"


def test_telemetry_snapshot_subset() -> None:
    from pi_edge.telemetry_mqtt_sidecar import _snapshot_payload

    raw = {
        "ts_unix": 1.0,
        "channels": {"0": {}},
        "diag": {"x": 1},
        "noise": "drop-me",
    }
    snap = _snapshot_payload(raw)
    assert "channels" in snap and "diag" in snap
    assert "noise" not in snap


def test_cloud_conf_load(tmp_path) -> None:
    p = tmp_path / "c.json"
    p.write_text('{"mqtt_endpoint":"e.example","mqtt_port":4433}', encoding="utf-8")
    d = cconf.load_cloud_conf(p)
    assert cconf.mqtt_endpoint_from_conf(d) == "e.example"
    assert cconf.mqtt_port_from_conf(d) == 4433


def test_iot_paths_merge_cloud_conf(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICCP_IOT_ENDPOINT", raising=False)
    monkeypatch.delenv("ICCP_MQTT_HOST", raising=False)
    monkeypatch.delenv("ICCP_IOT_PORT", raising=False)
    monkeypatch.setenv("ICCP_MERGE_CLOUD_CONF", "1")
    p = tmp_path / "cloud.conf"
    p.write_text(
        '{"mqtt_endpoint":"iot.merge.example","mqtt_port":8883}', encoding="utf-8"
    )
    monkeypatch.setenv("ICCP_CLOUD_CONF", str(p))
    end, _ca, _cert, _key, port = mc.iot_paths_resolved()
    assert end == "iot.merge.example"
    assert port == 8883


def test_iot_paths_merge_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICCP_IOT_ENDPOINT", raising=False)
    monkeypatch.setenv("ICCP_MERGE_CLOUD_CONF", "0")
    end, _, _, _, port = mc.iot_paths_resolved()
    assert end == ""
    assert port == 8883


def test_edge_doctor_json_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICCP_MERGE_CLOUD_CONF", "0")
    code = ed.main(["--json"])
    assert code == 0


def test_edge_doctor_strict_fails_without_mqtt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICCP_IOT_ENDPOINT", raising=False)
    monkeypatch.delenv("ICCP_MQTT_HOST", raising=False)
    monkeypatch.setenv("ICCP_MERGE_CLOUD_CONF", "0")
    monkeypatch.setenv("ICCP_IOT_CA_PATH", "/nonexistent/ca.pem")
    code = ed.main(["--strict"])
    assert code == 1


def test_register_bearer_from_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    tok = tmp_path / "t.txt"
    tok.write_text("secret-from-file\n", encoding="utf-8")
    monkeypatch.delenv("ICCP_CLOUD_REGISTER_TOKEN", raising=False)
    monkeypatch.setenv("ICCP_CLOUD_REGISTER_TOKEN_FILE", str(tok))
    assert cr._register_bearer_token() == "secret-from-file"


def test_register_bearer_env_over_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    tok = tmp_path / "t.txt"
    tok.write_text("fromfile", encoding="utf-8")
    monkeypatch.setenv("ICCP_CLOUD_REGISTER_TOKEN", "fromenv")
    monkeypatch.setenv("ICCP_CLOUD_REGISTER_TOKEN_FILE", str(tok))
    assert cr._register_bearer_token() == "fromenv"


def test_persist_cloud_conf(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICCP_CLOUD_CONF", raising=False)
    path = tmp_path / "cloud.conf"
    cr.persist_cloud_conf({"serial": "abc", "device_token": "t"}, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["serial"] == "abc"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
