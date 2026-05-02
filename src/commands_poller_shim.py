"""Load and run ``device-firmware/pi-ble/commands_poller.py`` (single source of truth).

Installed layout: this file lives in ``src/``; the poller implementation stays in
``pi-ble/`` for bench scripts. ``iccp commands-poll`` and ``iccp-commands-poll`` invoke
``main()`` here. See ``.claude/commands.md`` for the contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    repo = here.parent
    poller = repo / "pi-ble" / "commands_poller.py"
    if not poller.is_file():
        print(f"commands_poller_shim: missing {poller}", file=sys.stderr)
        return 2
    spec = importlib.util.spec_from_file_location("_commands_poller_impl", poller)
    if spec is None or spec.loader is None:
        print("commands_poller_shim: could not load spec", file=sys.stderr)
        return 2
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_commands_poller_impl"] = mod
    spec.loader.exec_module(mod)
    return int(mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
