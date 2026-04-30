"""
Append-only JSONL spool for MQTT publish failures (commission bridge).

When ``ICCP_COMMISSION_MQTT_SPOOL`` is set to a directory, failed lines are
appended to ``pending.jsonl``; on the next successful broker session they are
drained in order before new subprocess output is published.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def spool_dir() -> Path | None:
    raw = (os.environ.get("ICCP_COMMISSION_MQTT_SPOOL") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def pending_path() -> Path | None:
    d = spool_dir()
    if d is None:
        return None
    return d / "pending.jsonl"


def enqueue_line(line: str) -> None:
    """Append one JSONL line (no trailing newline required)."""
    p = pending_path()
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n\r") + "\n")


def drain_lines(publish: Callable[[str], None]) -> tuple[int, int]:
    """
    Attempt ``publish(line)`` for each queued line in order.

    Returns ``(published_ok_count, failed_remain_count)``. Failed lines are
    rewritten to ``pending.jsonl`` so a later run can retry.
    """
    p = pending_path()
    if p is None or not p.is_file():
        return (0, 0)
    try:
        lines = [
            ln.strip()
            for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    except OSError:
        return (0, 0)
    if not lines:
        try:
            p.unlink()
        except OSError:
            pass
        return (0, 0)

    failed: list[str] = []
    ok = 0
    for line in lines:
        try:
            publish(line)
            ok += 1
        except Exception:
            failed.append(line)

    if failed:
        p.write_text("\n".join(failed) + "\n", encoding="utf-8")
    else:
        try:
            p.unlink()
        except OSError:
            pass
    return (ok, len(failed))
