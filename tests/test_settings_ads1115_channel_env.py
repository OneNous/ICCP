"""COILSHIELD_ADS1115_CHANNEL is applied at settings import (subprocess isolation)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_ads1115_channel_env_sets_ain() -> None:
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(_ROOT)!r})\n"
        "os.environ['COILSHIELD_ADS1115_CHANNEL'] = '3'\n"
        "import importlib\n"
        "import config.settings as cfg\n"
        "assert cfg.ADS1115_CHANNEL == 3\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_ads1115_channel_env_invalid_raises() -> None:
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(_ROOT)!r})\n"
        "os.environ['COILSHIELD_ADS1115_CHANNEL'] = '9'\n"
        "import importlib\n"
        "import config.settings\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0
    assert "COILSHIELD_ADS1115_CHANNEL" in (r.stderr + r.stdout)

