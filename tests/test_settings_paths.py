"""LOG_DIR resolution and resolved_telemetry_paths()."""

from __future__ import annotations

from pathlib import Path

from config.settings import PROJECT_ROOT, _resolve_log_dir, resolved_telemetry_paths


def test_resolve_log_dir_default() -> None:
    d = _resolve_log_dir(PROJECT_ROOT, {})
    assert d == (PROJECT_ROOT / "logs").resolve()


def test_resolve_log_dir_absolute(tmp_path: Path) -> None:
    custom = tmp_path / "x" / "y"
    d = _resolve_log_dir(PROJECT_ROOT, {"COILSHIELD_LOG_DIR": str(custom)})
    assert d == custom.resolve()


def test_resolve_log_dir_relative_to_project(tmp_path: Path) -> None:
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    d = _resolve_log_dir(fake_root, {"ICCP_LOG_DIR": "var/iccp"})
    assert d == (fake_root / "var" / "iccp").resolve()


def test_resolved_telemetry_paths_shape() -> None:
    tp = resolved_telemetry_paths()
    for k in ("project_root", "log_dir", "latest_json", "sqlite_db", "log_dir_source"):
        assert k in tp
        assert isinstance(tp[k], str)
        assert len(tp[k]) > 0
