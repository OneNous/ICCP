"""cloud_bootstrap — JWT persistence + expiry (no live Edge Function)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import cloud_bootstrap


@pytest.fixture(autouse=True)
def _paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("COILSHIELD_CLOUD_CONF", str(tmp_path / "cloud.conf"))
    monkeypatch.setenv("COILSHIELD_BLE_PROVISION_FLAG", str(tmp_path / "ble.flag"))
    return tmp_path


def test_persist_roundtrip(_paths: Path) -> None:
    creds = cloud_bootstrap.CloudCredentials(
        token="tok.test",
        exp=int(time.time()) + 3600,
        serial="SMOKE00000001",
        tech_id="tech-1",
    )
    p = cloud_bootstrap.persist_jwt(creds)
    loaded = cloud_bootstrap.load_jwt(p)
    assert loaded is not None
    assert loaded.token == "tok.test"
    assert loaded.serial == "SMOKE00000001"
    assert loaded.tech_id == "tech-1"
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["tech_id"] == "tech-1"


def test_current_device_jwt_none_when_expired(_paths: Path) -> None:
    past = int(time.time()) - 10
    cloud_bootstrap.persist_jwt(
        cloud_bootstrap.CloudCredentials(token="x", exp=past, serial="SMOKE00000001")
    )
    assert cloud_bootstrap.current_device_jwt() is None
