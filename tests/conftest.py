"""Ensure simulator mode for tests (before sensors import)."""

import os
import sys

# Default tests to simulator so imports work on macOS CI and dev laptops.
if "COILSHIELD_SIM" not in os.environ:
    os.environ["COILSHIELD_SIM"] = "1"

# Project root on sys.path for `import config`, `import sensors`
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
