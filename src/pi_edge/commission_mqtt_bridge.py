"""
Run ``iccp commission`` with ``ICCP_OUTPUT=jsonl`` and publish each JSON line to MQTT.

Uses the same event family as ``cli_events`` (``iccp.cli.event.v1`` on each line).

If ``ICCP_COMMISSION_MQTT_SPOOL`` is a directory, failed publishes are appended to
``pending.jsonl`` and drained at the start of the next run (plan: short-outage buffer).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import TextIO

from pi_edge import mqtt_line_spool as line_spool
from pi_edge.device_identity import device_serial
from pi_edge.mqtt_client import AwsIotMqttPublisher, mqtt_client_id


def _iccp_argv() -> list[str]:
    override = (os.environ.get("ICCP_COMMISSION_CMD") or "").strip()
    if override:
        return override.split()
    exe = shutil.which("iccp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "iccp_cli"]


def _publish_line(
    pub: AwsIotMqttPublisher, topic: str, line: str, *, timeout_s: float = 15.0
) -> None:
    pub.wait_publish(topic, line.encode("utf-8"), qos=1, timeout_s=timeout_s)


def run(
    commission_argv: list[str],
    *,
    topic: str | None = None,
    dry_run: bool = False,
    stdout_copy: TextIO | None = None,
) -> int:
    serial = device_serial()
    t = topic or os.environ.get("ICCP_MQTT_COMMISSION_TOPIC")
    if not t:
        t = f"iccp/{serial}/commission/jsonl"

    cmd = _iccp_argv() + commission_argv
    env = os.environ.copy()
    env["ICCP_OUTPUT"] = "jsonl"

    if dry_run:
        print(f"would run: {cmd}")
        print(f"topic: {t}")
        sp = line_spool.spool_dir()
        if sp:
            print(f"spool: {sp}")
        return 0

    pub: AwsIotMqttPublisher | None = None
    try:
        pub = AwsIotMqttPublisher(client_id=mqtt_client_id(serial, "commission"))
        pub.connect()
    except Exception as e:
        print(f"iccp-commission-mqtt: MQTT connect failed: {e}", file=sys.stderr)
        return 2

    rc = 0
    try:
        if line_spool.spool_dir() is not None:
            ok, failed = line_spool.drain_lines(lambda s: _publish_line(pub, t, s))
            if ok or failed:
                print(
                    f"iccp-commission-mqtt: spool drain published={ok} "
                    f"failed_remain={failed}",
                    file=sys.stderr,
                )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            env=env,
            bufsize=1,
        )
        assert proc.stdout is not None
        line_no = 0
        for line in proc.stdout:
            line_no += 1
            s = line.rstrip("\n\r")
            if not s:
                continue
            try:
                _publish_line(pub, t, s)
            except Exception as e:
                print(
                    f"iccp-commission-mqtt: publish failed line {line_no}: {e}",
                    file=sys.stderr,
                )
                line_spool.enqueue_line(s)
            if stdout_copy is not None:
                stdout_copy.write(line)
                stdout_copy.flush()
        proc.wait(timeout=600)
        rc = int(proc.returncode or 0)
    finally:
        pub.disconnect()
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Bridge ICCP commission JSONL stdout to MQTT (AWS IoT TLS).",
        epilog=(
            "Example:\n"
            "  iccp-commission-mqtt -- commission --force\n\n"
            "Optional env:\n"
            "  ICCP_MQTT_COMMISSION_TOPIC — default iccp/<serial>/commission/jsonl;\n"
            "    cloud may instead use devices/<serial>/commission/events.\n"
            "  ICCP_COMMISSION_MQTT_SPOOL — directory for pending.jsonl on publish failure."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--topic",
        default=None,
        help="Override topic (default iccp/<serial>/commission/jsonl)",
    )
    ap.add_argument(
        "--tee",
        action="store_true",
        help="Also copy JSON lines to stdout",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print command and topic only",
    )
    ap.add_argument(
        "commission_args",
        nargs=argparse.REMAINDER,
        help="Arguments after optional '--' are passed to iccp (e.g. commission …)",
    )
    ns = ap.parse_args(argv)
    rest = list(ns.commission_args)
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        print(
            "iccp-commission-mqtt: need commission args, e.g. "
            "iccp-commission-mqtt -- commission --help",
            file=sys.stderr,
        )
        return 2
    return run(
        rest,
        topic=ns.topic,
        dry_run=ns.dry_run,
        stdout_copy=sys.stdout if ns.tee else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
