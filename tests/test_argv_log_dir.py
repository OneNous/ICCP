"""config.argv_log_dir — COILSHIELD_LOG_DIR from argv before settings import."""

from __future__ import annotations

import os

import pytest

from config import argv_log_dir as argv_log_dir_mod
from config.argv_log_dir import (
    apply_coilshield_log_dir_from_argv,
    apply_coilshield_log_dir_from_running_controller_if_unset,
)


def test_apply_log_dir_two_arg_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_LOG_DIR", raising=False)
    apply_coilshield_log_dir_from_argv(["--log-dir", "/tmp/iccp_logs"])
    assert os.environ["COILSHIELD_LOG_DIR"] == "/tmp/iccp_logs"


def test_apply_log_dir_equals_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_LOG_DIR", raising=False)
    apply_coilshield_log_dir_from_argv(["-start", "--log-dir=/var/lib/iccp/logs"])
    assert os.environ["COILSHIELD_LOG_DIR"] == "/var/lib/iccp/logs"


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ([], False),
        (["/usr/bin/iccp"], False),
        (["/usr/bin/iccp", "commission"], False),
        (["/usr/bin/iccp", "dashboard"], False),
        (["/usr/bin/iccp", "start"], True),
        (["iccp", "start", "--real"], True),
        (["/venv/bin/python", "/app/main.py"], True),
    ],
)
def test_is_controller_cmdline(parts: list[str], expected: bool) -> None:
    assert argv_log_dir_mod._is_controller_cmdline(parts) is expected


def test_resolve_log_dir_for_project_default(tmp_path) -> None:
    d = argv_log_dir_mod._resolve_log_dir_for_project(tmp_path, {})
    assert d == (tmp_path / "logs").resolve()


def test_resolve_log_dir_for_project_relative(tmp_path) -> None:
    d = argv_log_dir_mod._resolve_log_dir_for_project(
        tmp_path, {"COILSHIELD_LOG_DIR": "telemetry"}
    )
    assert d == (tmp_path / "telemetry").resolve()


def test_apply_from_running_controller_skips_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COILSHIELD_LOG_DIR", "/already/set")
    apply_coilshield_log_dir_from_running_controller_if_unset()
    assert os.environ["COILSHIELD_LOG_DIR"] == "/already/set"


def test_apply_from_running_controller_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COILSHIELD_LOG_DIR", raising=False)
    monkeypatch.delenv("ICCP_LOG_DIR", raising=False)
    monkeypatch.setattr(argv_log_dir_mod.sys, "platform", "darwin")
    called: list[int] = []

    def _boom() -> list:
        called.append(1)
        raise AssertionError("should not scan /proc off Linux")

    monkeypatch.setattr(argv_log_dir_mod, "_gather_controller_log_dirs_linux", _boom)
    apply_coilshield_log_dir_from_running_controller_if_unset()
    assert called == []
    assert "COILSHIELD_LOG_DIR" not in os.environ


def test_apply_from_running_controller_sets_from_gather(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("COILSHIELD_LOG_DIR", raising=False)
    monkeypatch.delenv("ICCP_LOG_DIR", raising=False)
    monkeypatch.setattr(argv_log_dir_mod.sys, "platform", "linux")
    want = tmp_path / "logs"
    want.mkdir()
    monkeypatch.setattr(
        argv_log_dir_mod,
        "_gather_controller_log_dirs_linux",
        lambda: [(want, 1.0), (want, 2.0)],
    )
    apply_coilshield_log_dir_from_running_controller_if_unset()
    assert os.environ["COILSHIELD_LOG_DIR"] == str(want.resolve())
