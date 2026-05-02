#!/usr/bin/env python3
"""
Commands poller — command-center → device control path.

Spec: ../../.claude/commands.md (canonical) + ../../schemas/commands.sql (table).

This module is the **in-this-repo reference implementation** of the poll loop
that lives on the Pi. The canonical Pi firmware lives in a separate repo; the
file here exists so:

  1. The contract is exercised by something runnable during validation work.
  2. Bench tests can drive it stand-alone (`python -m commands_poller --once`).
  3. The canonical firmware can port it line-for-line; the safety guard and
     the database round-trip are written to be hardware-agnostic.

What this file IS:
  - The polling loop itself (interval, filter, ack, terminal write).
  - The safety guard that refuses unsafe commands BEFORE the executor runs.
  - Stubbed per-command-type executors. Each stub returns a plausible payload
    so the round-trip + state machine is observable end-to-end.

What this file is NOT (must be implemented in the canonical firmware repo):
  - Real `clear_fault` clearing a non-latched fault on the channel.
  - Real `force_probe` driving a 3% duty pulse and reading wet detection.
  - Real `request_diagnostic` tarring the actual log files + readings CSV
    and uploading to the `installation-photos` (or follow-up) Storage bucket.
  - Real `reboot` invoking `systemctl reboot`.
  - Real `re_commission` re-entering the commissioning state machine.
  - Real `disable_channel` / `enable_channel` flipping the admin flag the
    main control loop reads.
  - Real `set_polarization_override` updating the per-channel target after
    the safety guard already cleared the value.

THE POLARIZATION HARD CUTOFF IS ENFORCED HERE AS A LAW.
A `set_polarization_override` below `POLARIZATION_HARD_CUTOFF_MV` is rejected
with state='invalid' and `result_payload.error` containing the reason.
The value is **never silently clamped**. Period.

Environment:
  SUPABASE_URL                  e.g. https://<ref>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY     device-side only — NEVER ship this in apps
  COILSHIELD_SERIAL             which device this Pi is
  COMMANDS_POLL_INTERVAL_S      default 15; clamped to [10, 30]
  POLARIZATION_HARD_CUTOFF_MV   default -1080 (Ag/AgCl); SAFETY LAW
  POLARIZATION_SAFETY_CEILING_MV default -200; reject above this too
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

# ---------------------------------------------------------------------------
# Constants — safety law (do not weaken without a DECISIONS.md entry)
# ---------------------------------------------------------------------------

POLARIZATION_HARD_CUTOFF_MV = int(os.environ.get("POLARIZATION_HARD_CUTOFF_MV", "-1080"))
POLARIZATION_SAFETY_CEILING_MV = int(os.environ.get("POLARIZATION_SAFETY_CEILING_MV", "-200"))

ALLOWED_COMMAND_TYPES = (
    "clear_fault",
    "force_probe",
    "request_diagnostic",
    "reboot",
    "re_commission",
    "disable_channel",
    "enable_channel",
    "set_polarization_override",
)

VALID_CHANNELS = (1, 2, 3, 4)


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only; matches sibling supabase_rest.py)
# ---------------------------------------------------------------------------

def _env_url() -> str:
    raw = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    ref = (os.environ.get("SUPABASE_PROJECT_REF") or "").strip()
    if ref:
        return f"https://{ref}.supabase.co"
    raise SystemExit("SUPABASE_URL or SUPABASE_PROJECT_REF must be set.")


def _headers() -> dict[str, str]:
    """PostgREST auth: per-device JWT + anon ``apikey`` when bound; else service role (bench).

    Raises ``ValueError`` when neither path is available (Pi before install-code exchange).
    """
    try:
        from cloud_bootstrap import current_device_jwt
        from cloud_sync import supabase_anon_key

        jwt = current_device_jwt()
        if jwt:
            anon = supabase_anon_key()
            if not anon:
                raise ValueError("SUPABASE_ANON_KEY is required when using a per-device JWT.")
            return {
                "apikey": anon,
                "Authorization": f"Bearer {jwt}",
                "Content-Type": "application/json",
            }
    except ImportError:
        pass
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if key:
        return {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
    raise ValueError(
        "no Supabase auth for commands poller — enter the install code over BLE (per-device JWT) "
        "or set SUPABASE_SERVICE_ROLE_KEY on bench hosts."
    )


def _request(method: str, path: str, *, query: Mapping[str, str] | None = None,
             body: Any = None, prefer: str | None = None, timeout: float = 30.0) -> tuple[int, str]:
    url = f"{_env_url()}/rest/v1/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = _headers()
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Commands API (filtered to one device serial)
# ---------------------------------------------------------------------------

def fetch_open_commands(serial: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """SELECT commands WHERE device_serial=:serial AND state IN (pending,acknowledged)"""
    try:
        code, body = _request(
            "GET", "commands",
            query={
                "select": "*",
                "device_serial": f"eq.{serial}",
                "state": "in.(pending,acknowledged)",
                "order": "issued_at.asc",
                "limit": str(limit),
            },
        )
    except ValueError:
        return []
    if code >= 400:
        raise RuntimeError(f"fetch_open_commands HTTP {code}: {body[:400]}")
    return json.loads(body) if body else []


def conditional_acknowledge(command_id: str) -> bool:
    """UPDATE … SET state='acknowledged' WHERE id=:id AND state='pending'.

    Returns True if exactly this process took the row from pending→acknowledged.
    """
    try:
        code, body = _request(
            "PATCH", "commands",
            query={"id": f"eq.{command_id}", "state": "eq.pending"},
            body={"state": "acknowledged", "acknowledged_at": _now_iso()},
            prefer="return=representation",
        )
    except ValueError:
        return False
    if code >= 400:
        raise RuntimeError(f"acknowledge HTTP {code}: {body[:400]}")
    rows = json.loads(body) if body else []
    return len(rows) == 1


def write_terminal(command_id: str, *, state: str, result_payload: Mapping[str, Any]) -> None:
    if state not in ("succeeded", "failed", "invalid"):
        raise ValueError(f"refusing to write non-terminal state {state!r}")
    try:
        code, body = _request(
            "PATCH", "commands",
            query={"id": f"eq.{command_id}"},
            body={
                "state": state,
                "result_payload": dict(result_payload),
                "executed_at": _now_iso(),
            },
            prefer="return=minimal",
        )
    except ValueError as e:
        raise RuntimeError(f"write_terminal: auth not ready ({e})") from e
    if code >= 400:
        raise RuntimeError(f"write_terminal HTTP {code}: {body[:400]}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


# ---------------------------------------------------------------------------
# Safety guard — runs BEFORE any executor
# ---------------------------------------------------------------------------

class SafetyReject(Exception):
    """Raised by safety_check when a command must be rejected with state='invalid'."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def safety_check(row: Mapping[str, Any]) -> None:
    """Raise SafetyReject(reason) if the command must be refused.

    The polarization hard cutoff is enforced here. NEVER weaken this without
    a DECISIONS.md entry, and never add a side channel that bypasses it.
    """
    cmd_type = row.get("command_type")
    params = row.get("params") or {}
    if cmd_type not in ALLOWED_COMMAND_TYPES:
        raise SafetyReject(f"unknown command_type {cmd_type!r}")

    if cmd_type == "set_polarization_override":
        target = params.get("target_mv")
        if not isinstance(target, (int, float)):
            raise SafetyReject("set_polarization_override.params.target_mv missing or not numeric")
        if target < POLARIZATION_HARD_CUTOFF_MV:
            raise SafetyReject(
                f"target_mv {target} mV below polarization hard cutoff "
                f"{POLARIZATION_HARD_CUTOFF_MV} mV — refusing (NEVER silently clamped)"
            )
        if target > POLARIZATION_SAFETY_CEILING_MV:
            raise SafetyReject(
                f"target_mv {target} mV above safety ceiling "
                f"{POLARIZATION_SAFETY_CEILING_MV} mV — refusing"
            )
        ch = params.get("channel")
        if ch not in VALID_CHANNELS:
            raise SafetyReject(f"channel {ch!r} not in {VALID_CHANNELS}")

    if cmd_type in ("clear_fault", "force_probe", "disable_channel", "enable_channel"):
        ch = params.get("channel")
        if ch not in VALID_CHANNELS:
            raise SafetyReject(f"channel {ch!r} not in {VALID_CHANNELS}")


# ---------------------------------------------------------------------------
# Executors (stubbed — port real ones in canonical firmware repo)
# ---------------------------------------------------------------------------

ExecutorResult = tuple[str, dict[str, Any]]  # (state, result_payload)


def _stub(payload: dict[str, Any]) -> ExecutorResult:
    payload.setdefault("scaffolding", "device-firmware/pi-ble — port to canonical firmware repo")
    return "succeeded", payload


def exec_clear_fault(row: Mapping[str, Any]) -> ExecutorResult:
    ch = row["params"]["channel"]
    # CANONICAL: clear non-latched fault on channel <ch>; refuse if latched by cutoff.
    return _stub({"channel": ch, "cleared": True})


def exec_force_probe(row: Mapping[str, Any]) -> ExecutorResult:
    ch = row["params"]["channel"]
    # CANONICAL: drive 3% duty pulse on <ch>; read current; report wet/dry.
    return _stub({"channel": ch, "probe_ma": 0.0, "wet_detected": False})


def exec_request_diagnostic(row: Mapping[str, Any]) -> ExecutorResult:
    hours = int((row.get("params") or {}).get("hours") or 24)
    # CANONICAL: tar /var/log/coilshield/*.log + last <hours>h readings as CSV;
    # upload to a Storage bucket; sign URL; return.
    return _stub({"hours": hours, "url": "stub://upload-pending"})


def exec_reboot(_row: Mapping[str, Any]) -> ExecutorResult:
    # CANONICAL: write succeeded BEFORE invoking reboot; subprocess.run(["systemctl","reboot"]).
    return _stub({"rebooting_at": _now_iso()})


def exec_re_commission(_row: Mapping[str, Any]) -> ExecutorResult:
    # CANONICAL: re-enter commissioning state machine.
    return _stub({"phase": "started"})


def exec_disable_channel(row: Mapping[str, Any]) -> ExecutorResult:
    ch = row["params"]["channel"]
    # CANONICAL: set admin_disabled[ch]=True; persist; main loop reads on next tick.
    return _stub({"channel": ch, "enabled": False})


def exec_enable_channel(row: Mapping[str, Any]) -> ExecutorResult:
    ch = row["params"]["channel"]
    return _stub({"channel": ch, "enabled": True})


def exec_set_polarization_override(row: Mapping[str, Any]) -> ExecutorResult:
    ch = row["params"]["channel"]
    target = row["params"]["target_mv"]
    # CANONICAL: persist target_mv for channel; main control loop picks it up.
    # The safety guard already verified target is within [HARD_CUTOFF, SAFETY_CEILING].
    return _stub({"channel": ch, "target_mv": target})


EXECUTORS: dict[str, Callable[[Mapping[str, Any]], ExecutorResult]] = {
    "clear_fault": exec_clear_fault,
    "force_probe": exec_force_probe,
    "request_diagnostic": exec_request_diagnostic,
    "reboot": exec_reboot,
    "re_commission": exec_re_commission,
    "disable_channel": exec_disable_channel,
    "enable_channel": exec_enable_channel,
    "set_polarization_override": exec_set_polarization_override,
}


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def process_one(row: Mapping[str, Any], log: logging.Logger) -> None:
    cmd_id = row["id"]
    cmd_type = row.get("command_type", "?")

    # Step 1 — conditional acknowledge. Skip if already acknowledged by us or someone else.
    if row.get("state") == "pending":
        if not conditional_acknowledge(cmd_id):
            log.info("commands %s already taken by another worker; skipping", cmd_id)
            return
        log.info("commands %s ACK (%s)", cmd_id, cmd_type)
    else:
        log.info("commands %s resuming from acknowledged (%s)", cmd_id, cmd_type)

    # Step 2 — safety guard.
    try:
        safety_check(row)
    except SafetyReject as rej:
        log.warning("commands %s INVALID: %s", cmd_id, rej.reason)
        write_terminal(cmd_id, state="invalid", result_payload={"error": rej.reason})
        return

    # Step 3 — execute.
    fn = EXECUTORS.get(cmd_type)
    if fn is None:
        write_terminal(cmd_id, state="invalid",
                       result_payload={"error": f"no executor for {cmd_type}"})
        return
    try:
        state, payload = fn(row)
    except Exception as exc:  # noqa: BLE001 — terminal-state guarantee
        log.exception("commands %s FAILED", cmd_id)
        write_terminal(cmd_id, state="failed",
                       result_payload={"error": f"{type(exc).__name__}: {exc}"})
        return

    write_terminal(cmd_id, state=state, result_payload=payload)
    log.info("commands %s %s %s", cmd_id, state.upper(), json.dumps(payload)[:200])


def poll_once(serial: str, log: logging.Logger) -> int:
    rows = fetch_open_commands(serial)
    for row in rows:
        try:
            process_one(row, log)
        except Exception:  # noqa: BLE001 — never let the loop die
            log.exception("commands processing crashed; continuing")
    return len(rows)


def run_forever(serial: str, interval_s: float, log: logging.Logger) -> int:
    stop = {"flag": False}

    def _on_signal(_sig, _frame):  # type: ignore[no-untyped-def]
        stop["flag"] = True
        log.info("commands poller: shutdown signal received")

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _on_signal)

    log.info(
        "commands poller: serial=%s interval=%.0fs cutoff=%dmV ceiling=%dmV",
        serial, interval_s, POLARIZATION_HARD_CUTOFF_MV, POLARIZATION_SAFETY_CEILING_MV,
    )
    while not stop["flag"]:
        try:
            n = poll_once(serial, log)
            if n:
                log.info("commands poller: processed %d row(s)", n)
        except Exception:  # noqa: BLE001
            log.exception("commands poller: poll cycle failed")
        # Sleep in 1-s slices so SIGINT is responsive.
        sliced = 0.0
        while sliced < interval_s and not stop["flag"]:
            time.sleep(min(1.0, interval_s - sliced))
            sliced += 1.0
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_serial() -> str | None:
    """Best-effort: env override → cached identity. ``None`` if no stable serial available."""
    env = (os.environ.get("COILSHIELD_SERIAL") or "").strip()
    if env:
        return env
    try:
        # ``device_identity`` lives next to ``src/``; pyproject ships it under PYTHONPATH=src.
        from device_identity import derive_device_serial, has_valid_serial  # type: ignore[import-not-found]

        s = derive_device_serial()
        return s if has_valid_serial(s) else None
    except ImportError:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description="CoilShield commands poller (Pi-side)")
    p.add_argument("--serial", default=_default_serial())
    p.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("COMMANDS_POLL_INTERVAL_S", "15")),
        help="Poll interval in seconds (clamped to [10, 30])",
    )
    p.add_argument("--once", action="store_true", help="Poll once and exit (bench / smoke).")
    args = p.parse_args()
    if not args.serial:
        p.error("--serial or COILSHIELD_SERIAL required (or write /etc/coilshield/serial)")
    interval = max(10.0, min(30.0, float(args.interval)))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("commands_poller")

    if args.once:
        return 0 if poll_once(args.serial, log) >= 0 else 1
    return run_forever(args.serial, interval, log)


if __name__ == "__main__":
    sys.exit(main())
