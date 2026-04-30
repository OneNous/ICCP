"""cloud_sync — env wiring and ping (no live network in CI)."""

from __future__ import annotations

import os

import pytest


def test_supabase_ping_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    import cloud_sync

    ok, msg = cloud_sync.supabase_ping()
    assert ok is False
    assert "not set" in msg.lower()


def test_is_supabase_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import cloud_sync

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "x")
    assert cloud_sync.is_supabase_configured() is True
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "y")
    assert cloud_sync.is_supabase_configured() is True


@pytest.mark.skipif(
    os.environ.get("ICCP_LIVE_SUPABASE_TEST") != "1",
    reason="set ICCP_LIVE_SUPABASE_TEST=1 and valid SUPABASE_* env to hit the network",
)
def test_supabase_ping_live() -> None:
    import cloud_sync

    if not os.environ.get("SUPABASE_URL") or not (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    ):
        pytest.skip("SUPABASE_URL and service key required")
    cloud_sync.load_dotenv_if_present()
    ok, msg = cloud_sync.supabase_ping()
    assert ok, msg
