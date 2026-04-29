from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Any, TextIO

SCHEMA = "iccp.cli.event.v1"


def output_mode() -> str:
    """
    Output mode for CLI-facing logs.

    - "human" (default when ``ICCP_OUTPUT`` is unset): operator console text.
    - "jsonl": one JSON object per line — use ``iccp --jsonl``, or set ``ICCP_OUTPUT=jsonl``
      (e.g. ICCP-APP Pi Console), or export before the command.
    """
    m = (os.environ.get("ICCP_OUTPUT") or "human").strip().lower()
    return "human" if m == "human" else "jsonl"


def now_ts_unix() -> float:
    return time.time()


def exception_to_err(e: BaseException) -> dict[str, Any]:
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    if len(tb) > 12000:
        tb = tb[:12000] + "\n…(traceback truncated)"
    return {
        "type": type(e).__name__,
        "message": str(e),
        "traceback": tb,
    }


def emit(event: dict[str, Any], *, stream: TextIO | None = None) -> None:
    """
    Emit one JSONL event line. Adds `schema` and `ts_unix` if missing.

    This function must never raise (logging should not crash control/probe flows).
    """
    if output_mode() != "jsonl":
        # In human mode, callers should use existing print paths; keep emit a no-op.
        return
    out = stream if stream is not None else sys.stdout
    payload = dict(event)
    payload.setdefault("schema", SCHEMA)
    payload.setdefault("ts_unix", now_ts_unix())
    try:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        fallback = {
            "schema": SCHEMA,
            "ts_unix": now_ts_unix(),
            "level": "error",
            "cmd": "iccp",
            "source": "cli_events",
            "event": "emit.failed",
            "msg": "failed to json-encode event",
            "err": exception_to_err(e),
        }
        line = json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
    try:
        out.write(line + "\n")
        out.flush()
    except Exception:
        # Swallow write errors (broken pipe, etc.).
        return

