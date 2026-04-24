"""
Set ``COILSHIELD_ACTIVE_CHANNELS`` from argv before ``import config.settings`` (same idea as
``argv_log_dir``) so a subset of anode indices (0-based) is fixed at import time.

``iccp <subcommand> --channels 0,2`` or ``--anodes 1,3`` (1-based, matches UI "Anode N").
Singles: ``--anode 1`` (same as ``--anodes 1``), ``--channel 0`` (same as ``--channels 0``).

.. note::
   Partial anode selection with **shared bank PWM** (``SHARED_RETURN_PWM = True``) is
   rejected at startup: bank mode drives every gate to the same duty. Use
   ``SHARED_RETURN_PWM = False`` to run a subset of anodes in software.
"""

from __future__ import annotations

import os
import sys


def apply_coilshield_active_channels_from_argv(argv: list[str]) -> int | None:
    """
    Parse 0-based ``--channels`` / ``--channel`` or 1-based ``--anodes`` / ``--anode``.

    At most one selector group may be present, or we return **2** (exit code).
    When a flag is present, sets ``COILSHIELD_ACTIVE_CHANNELS``. When **no** anode
    flag is in ``argv``, leaves ``COILSHIELD_ACTIVE_CHANNELS`` unchanged.
    """
    c_list = _extract_flag_value(argv, "--channels")
    c_one = _extract_flag_value(argv, "--channel")
    a_list = _extract_flag_value(argv, "--anodes")
    a_one = _extract_flag_value(argv, "--anode")
    c_raw = c_list or c_one
    a_raw = a_list or a_one
    if c_list and c_one:
        print(
            "ERROR: use only one of --channels and --channel (not both).",
            file=sys.stderr,
        )
        return 2
    if a_list and a_one:
        print("ERROR: use only one of --anodes and --anode (not both).", file=sys.stderr)
        return 2
    if c_raw and a_raw:
        print(
            "ERROR: use only one 0-based selector (--channels / --channel) or "
            "1-based (--anodes / --anode), not both.",
            file=sys.stderr,
        )
        return 2
    s = c_raw or a_raw
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    parts = [p.strip() for p in s.replace(" ", "").split(",") if p.strip()]
    if not parts:
        return None
    out: list[int] = []
    for p in parts:
        n = int(p, 10)
        if a_raw is not None:
            n -= 1  # 1-based "Anode N" → 0-based idx
        out.append(n)
    os.environ["COILSHIELD_ACTIVE_CHANNELS"] = ",".join(str(x) for x in out)
    return None


def _extract_flag_value(argv: list[str], flag: str) -> str | None:
    """``--f v`` or ``--f=v``; returns value string or None."""
    eq = f"{flag}="
    for i, a in enumerate(argv):
        if a == flag:
            if i + 1 < len(argv):
                return argv[i + 1]
            return None
        if a.startswith(eq):
            return a[len(eq) :]
    return None
