"""tech_api — HMAC and routes (no dashboard process)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from flask import Flask


@pytest.fixture
def app_and_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 32-byte key as hex (64 chars)
    key_hex = "01" * 32
    monkeypatch.setenv("COILSHIELD_TECH_BOND_KEY", key_hex)
    monkeypatch.setenv("COILSHIELD_LOG_DIR", str(tmp_path))
    import importlib

    import config.settings as cfg

    importlib.reload(cfg)

    (tmp_path / "latest.json").write_text(json.dumps({"telemetry_seq": 42}), encoding="utf-8")

    import tech_api

    importlib.reload(tech_api)
    app = Flask(__name__)
    app.register_blueprint(tech_api.tech_bp)
    return app, app.test_client()


def _sign(key: bytes, ts: int, body: bytes) -> str:
    msg = f"{ts}\n".encode("utf-8") + body
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def test_info_unauthenticated(app_and_client):
    app, client = app_and_client
    r = client.get("/tech/info")
    assert r.status_code == 200
    data = r.get_json()
    assert "firmware_version" in data
    assert "uptime_seconds" in data


def test_status_rejects_missing_hmac(app_and_client):
    _, client = app_and_client
    r = client.get("/tech/status")
    assert r.status_code == 401


def test_status_rejects_clock_skew(app_and_client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COILSHIELD_TECH_HMAC_SKEW_S", "10")
    import importlib

    import config.settings as cfg
    import tech_api

    importlib.reload(cfg)
    importlib.reload(tech_api)
    app = Flask(__name__)
    app.register_blueprint(tech_api.tech_bp)
    client = app.test_client()
    key = bytes.fromhex("01" * 32)
    old_ts = int(time.time()) - 3600
    sig = _sign(key, old_ts, b"")
    r = client.get(
        "/tech/status",
        headers={
            "X-CoilShield-Signature": sig,
            "X-CoilShield-Timestamp": str(old_ts),
            "X-CoilShield-Tech-ID": "test-install",
        },
    )
    assert r.status_code == 401


def test_status_ok_with_hmac(app_and_client):
    _, client = app_and_client
    key = bytes.fromhex("01" * 32)
    ts = int(time.time())
    sig = _sign(key, ts, b"")
    r = client.get(
        "/tech/status",
        headers={
            "X-CoilShield-Signature": sig,
            "X-CoilShield-Timestamp": str(ts),
            "X-CoilShield-Tech-ID": "test-install",
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["latest"]["telemetry_seq"] == 42


def test_commission_status_matches_dashboard_merge_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``GET /tech/commission/status`` mirrors ``GET /commissioning/status`` telemetry merge."""
    import importlib
    import json

    import commissioning
    import config.settings as cfg
    import tech_api

    key_hex = "01" * 32
    monkeypatch.setenv("COILSHIELD_TECH_BOND_KEY", key_hex)
    monkeypatch.setenv("COILSHIELD_LOG_DIR", str(tmp_path))
    importlib.reload(cfg)
    golden = json.loads(
        (
            Path(__file__).resolve().parent / "fixtures" / "lan_commissioning_status_golden.json"
        ).read_text(encoding="utf-8")
    )
    (tmp_path / "latest.json").write_text(json.dumps(golden), encoding="utf-8")
    importlib.reload(tech_api)
    monkeypatch.setattr(commissioning, "needs_commissioning", lambda: True)

    app = Flask(__name__)
    app.register_blueprint(tech_api.tech_bp)
    client = app.test_client()
    key = bytes.fromhex(key_hex)
    ts = int(time.time())
    sig = _sign(key, ts, b"")
    r = client.get(
        "/tech/commission/status",
        headers={
            "X-CoilShield-Signature": sig,
            "X-CoilShield-Timestamp": str(ts),
            "X-CoilShield-Tech-ID": "test-install",
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body is not None
    assert body.get("needs_commissioning") is True
    assert body.get("telemetry_seq") == golden["telemetry_seq"]
    assert body.get("shift_mv") == golden["shift_mv"]


def test_commission_post_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key_hex = "01" * 32
    monkeypatch.setenv("COILSHIELD_TECH_BOND_KEY", key_hex)
    monkeypatch.setenv("COILSHIELD_LOG_DIR", str(tmp_path))
    import importlib

    import config.settings as cfg
    import tech_api

    importlib.reload(cfg)
    (tmp_path / "latest.json").write_text("{}", encoding="utf-8")
    importlib.reload(tech_api)
    app = Flask(__name__)
    app.register_blueprint(tech_api.tech_bp)
    client = app.test_client()
    key = bytes.fromhex(key_hex)
    ts = int(time.time())
    sig = _sign(key, ts, b"")
    r = client.post(
        "/tech/commission",
        headers={
            "X-CoilShield-Signature": sig,
            "X-CoilShield-Timestamp": str(ts),
            "X-CoilShield-Tech-ID": "test-install",
        },
    )
    assert r.status_code == 202
    body = r.get_json()
    assert body is not None
    assert body.get("accepted") is True
