"""iccp_cli: optional automatic systemctl daemon-reload / restart on Pi."""

from __future__ import annotations

import sys

import pytest

import iccp_cli


def test_sync_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(iccp_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(iccp_cli, "_running_on_raspberry_pi", lambda: True)
    monkeypatch.setenv("ICCP_SYSTEMD_SYNC", "0")
    iccp_cli._sync_systemd_for_iccp_cli("tui")
    assert calls == []


def test_sync_commission_stops_not_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(iccp_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(iccp_cli, "_running_on_raspberry_pi", lambda: True)
    monkeypatch.delenv("ICCP_SYSTEMD_SYNC", raising=False)
    iccp_cli._sync_systemd_for_iccp_cli("commission")
    assert calls == [
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "stop", "iccp"],
    ]


def test_sync_tui_restarts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(iccp_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(iccp_cli, "_running_on_raspberry_pi", lambda: True)
    monkeypatch.delenv("ICCP_SYSTEMD_SYNC", raising=False)
    iccp_cli._sync_systemd_for_iccp_cli("live")
    assert calls == [
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "restart", "iccp"],
    ]


def test_sync_start_only_daemon_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(iccp_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(iccp_cli, "_running_on_raspberry_pi", lambda: True)
    monkeypatch.delenv("ICCP_SYSTEMD_SYNC", raising=False)
    iccp_cli._sync_systemd_for_iccp_cli("-start")
    assert calls == [["sudo", "systemctl", "daemon-reload"]]


def test_main_unknown_command_exits_before_chdir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iccp_cli, "_running_on_raspberry_pi", lambda: True)
    called: list[str] = []

    def no_sync(cmd: str) -> None:
        called.append(cmd)

    monkeypatch.setattr(iccp_cli, "_sync_systemd_for_iccp_cli", no_sync)
    monkeypatch.setattr(sys, "argv", ["iccp", "not-a-real-subcommand"])
    assert iccp_cli.main() == 2
    assert called == []


def test_custom_systemd_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(iccp_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(iccp_cli, "_running_on_raspberry_pi", lambda: True)
    monkeypatch.delenv("ICCP_SYSTEMD_SYNC", raising=False)
    monkeypatch.setenv("ICCP_SYSTEMD_UNIT", "coilshield")
    iccp_cli._sync_systemd_for_iccp_cli("version")
    assert calls[-1] == ["sudo", "systemctl", "restart", "coilshield"]
