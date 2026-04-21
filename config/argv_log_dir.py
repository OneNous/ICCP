"""
Set COILSHIELD_LOG_DIR from argv before ``import config.settings`` (LOG_DIR is fixed at import).

Used by main.py, iccp_cli.py, and dashboard.py so telemetry paths match without relying on
shell environment alone.
"""

from __future__ import annotations

import os


def apply_coilshield_log_dir_from_argv(argv: list[str]) -> None:
    """If argv contains ``--log-dir <path>`` or ``--log-dir=<path>``, set ``COILSHIELD_LOG_DIR``."""
    for i, a in enumerate(argv):
        if a == "--log-dir" and i + 1 < len(argv):
            os.environ["COILSHIELD_LOG_DIR"] = argv[i + 1].strip().strip('"').strip("'")
            return
        if a.startswith("--log-dir="):
            os.environ["COILSHIELD_LOG_DIR"] = a.split("=", 1)[1].strip().strip('"').strip(
                "'"
            )
            return
