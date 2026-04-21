"""config.argv_log_dir — COILSHIELD_LOG_DIR from argv before settings import."""

from __future__ import annotations

import os

import pytest

from config.argv_log_dir import apply_coilshield_log_dir_from_argv


def test_apply_log_dir_two_arg_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_LOG_DIR", raising=False)
    apply_coilshield_log_dir_from_argv(["--log-dir", "/tmp/iccp_logs"])
    assert os.environ["COILSHIELD_LOG_DIR"] == "/tmp/iccp_logs"


def test_apply_log_dir_equals_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_LOG_DIR", raising=False)
    apply_coilshield_log_dir_from_argv(["-start", "--log-dir=/var/lib/iccp/logs"])
    assert os.environ["COILSHIELD_LOG_DIR"] == "/var/lib/iccp/logs"
