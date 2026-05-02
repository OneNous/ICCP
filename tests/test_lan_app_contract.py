"""LAN routes tech apps use: /health, /commissioning/* — Flask test client + golden merge shape.

Parity target: iOS/Android ``LanLivePayload`` / ``LanLiveParsed`` accept ``shift_mv``,
``total_ma``, and ``channels`` as a list of maps with ``ma`` (see LanLivePayloadTests).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config.settings as cfg

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lan_commissioning_status_golden.json"


@pytest.fixture()
def log_and_dashboard_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Same pattern as test_dashboard_api — temp log DB so ``dashboard`` imports cleanly."""
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SQLITE_PURGE_EVERY_N_INSERTS", 999_999_999)

    from logger import DataLogger

    readings = {
        i: {"ok": True, "current": 0.2 * (i + 1), "bus_v": 11.5}
        for i in range(cfg.NUM_CHANNELS)
    }
    duties = {i: 5.0 for i in range(cfg.NUM_CHANNELS)}
    log = DataLogger()
    log.record(
        readings,
        False,
        [],
        duties,
        False,
        {i: "OPEN" for i in range(cfg.NUM_CHANNELS)},
        ref_shift_mv=10.0,
        ref_status="OK",
        temp_f=72.5,
        ref_raw_mv=200.0,
        ref_hw_ok=True,
        ref_hint="",
        ref_hw_message="sim",
        ref_baseline_set=True,
    )
    log.close()

    import dashboard

    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _golden_latest() -> dict:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _assert_lan_live_keys(body: dict) -> None:
    """Fields technician LanLive parsers need (see apps/.../LanLivePayload*)."""
    assert "shift_mv" in body
    assert isinstance(body["shift_mv"], (int, float))
    assert "total_ma" in body
    assert isinstance(body["total_ma"], (int, float))
    ch = body["channels"]
    assert isinstance(ch, list) and len(ch) >= 1
    assert isinstance(ch[0], dict)
    assert "ma" in ch[0]


def test_health_and_api_health_ok(log_and_dashboard_client) -> None:
    c = log_and_dashboard_client
    for path in ("/health", "/api/health"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.get_json() == {"ok": True}


def test_commissioning_status_merges_latest_json(
    log_and_dashboard_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import commissioning
    import dashboard

    monkeypatch.setattr(commissioning, "needs_commissioning", lambda: True)
    golden = _golden_latest()
    monkeypatch.setattr(dashboard, "_latest", lambda: golden)

    r = log_and_dashboard_client.get("/commissioning/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body is not None
    assert body.get("needs_commissioning") is True
    assert body.get("telemetry_seq") == golden["telemetry_seq"]
    _assert_lan_live_keys(body)
    assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_commissioning_status_skips_latest_error_field(
    log_and_dashboard_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import commissioning
    import dashboard

    monkeypatch.setattr(commissioning, "needs_commissioning", lambda: False)
    monkeypatch.setattr(
        dashboard,
        "_latest",
        lambda: {"error": "no data", "shift_mv": 99.0},
    )
    r = log_and_dashboard_client.get("/commissioning/status")
    body = r.get_json()
    assert body is not None
    assert "error" not in body
    assert body.get("needs_commissioning") is False


def test_commissioning_start_accepted(log_and_dashboard_client) -> None:
    r = log_and_dashboard_client.post("/commissioning/start")
    assert r.status_code == 202
    body = r.get_json()
    assert body is not None
    assert body.get("accepted") is True
    assert "detail" in body


def test_commissioning_options_cors(log_and_dashboard_client) -> None:
    r = log_and_dashboard_client.open("/commissioning/status", method="OPTIONS")
    assert r.status_code == 204
    assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_golden_fixture_matches_commissioning_status_merge(
    log_and_dashboard_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On-disk golden file stays aligned with merged /commissioning/status response."""
    import commissioning
    import dashboard

    monkeypatch.setattr(commissioning, "needs_commissioning", lambda: False)
    golden = _golden_latest()
    monkeypatch.setattr(dashboard, "_latest", lambda: golden)

    r = log_and_dashboard_client.get("/commissioning/status")
    body = r.get_json()
    assert body is not None
    for k, v in golden.items():
        assert body.get(k) == v
    assert body.get("needs_commissioning") is False
