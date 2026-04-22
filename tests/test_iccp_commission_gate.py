"""iccp commission concurrent-controller gate and --force stripping."""

from __future__ import annotations

import json
import os
import time

import pytest

import config.settings as cfg
import iccp_cli


def test_split_commission_force_flag() -> None:
    r, f = iccp_cli._split_force_flag(["--sim", "--force"])
    assert f is True
    assert r == ["--sim"]
    r2, f2 = iccp_cli._split_force_flag(["--sim", "x"])
    assert f2 is False
    assert r2 == ["--sim", "x"]


def test_abort_skipped_when_force() -> None:
    assert iccp_cli._abort_if_concurrent_controller_active(force=True, on_pi_hw=True) is None


def test_abort_skipped_when_not_hw(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    p = tmp_path / "latest.json"
    p.write_text(json.dumps({"ts": 1}))
    os.utime(p, (time.time(), time.time()))
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "LATEST_JSON_NAME", "latest.json")
    monkeypatch.setattr(cfg, "SAMPLE_INTERVAL_S", 0.5)
    assert iccp_cli._abort_if_concurrent_controller_active(force=False, on_pi_hw=False) is None


def test_abort_when_latest_very_fresh(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    p = tmp_path / "latest.json"
    p.write_text("{}")
    os.utime(p, (time.time(), time.time()))
    monkeypatch.setattr(cfg, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "LATEST_JSON_NAME", "latest.json")
    monkeypatch.setattr(cfg, "SAMPLE_INTERVAL_S", 0.5)
    assert iccp_cli._abort_if_concurrent_controller_active(force=False, on_pi_hw=True) == 1
