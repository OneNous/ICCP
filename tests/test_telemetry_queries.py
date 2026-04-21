"""telemetry_queries module (history sampling)."""

from __future__ import annotations

from telemetry_queries import downsample_readings, history_payload


def test_downsample_empty() -> None:
    assert downsample_readings([], max_points=10) == []


def test_downsample_step() -> None:
    rows = list(range(100))
    out = downsample_readings(rows, max_points=10)
    assert len(out) <= 10
    assert out[0] == 0


def test_history_payload_no_database(monkeypatch, tmp_path) -> None:
    import config.settings as settings

    monkeypatch.setattr(settings, "LOG_DIR", tmp_path, raising=False)
    out = history_payload(5)
    assert out.get("error") == "database not ready"
