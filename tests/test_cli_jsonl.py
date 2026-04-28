from __future__ import annotations

import json
import sys

import pytest

import iccp_cli


def _parse_jsonl(stdout: str) -> list[dict]:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    out: list[dict] = []
    for ln in lines:
        out.append(json.loads(ln))
    return out


def test_version_default_is_jsonl(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(iccp_cli, "running_on_raspberry_pi", lambda: False)
    monkeypatch.setattr(sys, "argv", ["iccp", "version"])
    rc = iccp_cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    events = _parse_jsonl(out)
    assert any(e.get("event") == "cmd.begin" for e in events)
    assert any(e.get("event") == "version" for e in events)
    assert any(e.get("event") == "cmd.end" for e in events)
    for e in events:
        assert e.get("schema") == "iccp.cli.event.v1"
        assert isinstance(e.get("ts_unix"), (int, float))


def test_version_human_flag_preserves_text(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(iccp_cli, "running_on_raspberry_pi", lambda: False)
    monkeypatch.setattr(sys, "argv", ["iccp", "--human", "version"])
    rc = iccp_cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "coilshield-iccp" in out
    # Human mode should not emit JSONL command lifecycle events.
    assert "iccp.cli.event.v1" not in out

