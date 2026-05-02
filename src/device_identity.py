"""Stable per-Pi device identity (claude.md / .claude/firmware.md).

The Supabase `devices.serial` column has a `CHECK (length(serial) >= 8)` constraint,
and many subsystems (BLE device_info, cloud sync, commands poller, tech API) all need
the *same* string for the life of the controller. Hand-setting `COILSHIELD_SERIAL` per
Pi is the historical path and remains a valid override, but a flash-and-go install
should derive a stable serial from the Pi hardware itself.

Order of precedence (first hit wins):

1. ``COILSHIELD_SERIAL`` environment variable (≥ 8 chars after `CS-` normalization).
   Tests, simulators, and dev hosts use this.
2. Cached file ``/etc/coilshield/serial`` written on first boot.
3. ``/proc/cpuinfo`` ``Serial`` field (Pi CPU serial — 16 hex chars on Pi 3 / 4 / Zero 2 W).
4. Primary network MAC (``/sys/class/net/<iface>/address``) for non-Pi dev hosts.
5. ``socket.gethostname()`` fallback (still ≥ 8 chars after normalization).

The derived value is cached to ``/etc/coilshield/serial`` (0644) so subsequent boots
stay stable even if /proc/cpuinfo changes shape (e.g. kernel upgrade).
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

_LOG = logging.getLogger(__name__)

DEFAULT_SERIAL_CACHE = Path("/etc/coilshield/serial")
SERIAL_PREFIX = "CS-"
MIN_SERIAL_LEN = 8

_CACHED_SERIAL: str | None = None


def _serial_cache_path() -> Path:
    """Resolve cache file at call time so ``COILSHIELD_SERIAL_CACHE`` can be patched per test."""
    override = os.environ.get("COILSHIELD_SERIAL_CACHE")
    return Path(override) if override else DEFAULT_SERIAL_CACHE


# Backwards-compatible alias for callers that referenced the constant.
SERIAL_CACHE_PATH = DEFAULT_SERIAL_CACHE


def _normalize(raw: str) -> str | None:
    """Return ``CS-..``-prefixed serial of the right length, or ``None`` if too short.

    Only used for *derived* sources (CPU serial, MAC, hostname). User-supplied serials
    via ``COILSHIELD_SERIAL`` or the cache file are taken as-is — the bench installer
    and tests own that string.
    """
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        return None
    if not s.startswith(SERIAL_PREFIX):
        s = SERIAL_PREFIX + s
    if len(s) < MIN_SERIAL_LEN:
        return None
    return s


def _accept_explicit(raw: str) -> str | None:
    """Trust an explicit user-supplied serial verbatim if it satisfies the length CHECK."""
    s = (raw or "").strip()
    return s if len(s) >= MIN_SERIAL_LEN else None


def _try_env() -> str | None:
    return _accept_explicit(os.environ.get("COILSHIELD_SERIAL", ""))


def _try_cache_file(path: Path) -> str | None:
    try:
        if path.is_file():
            return _accept_explicit(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return None


def _try_cpuinfo(path: Path = Path("/proc/cpuinfo")) -> str | None:
    """Parse ``Serial`` line from /proc/cpuinfo (Pi CPU serial — 16 hex chars)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.lower().startswith("serial"):
            _, _, rhs = line.partition(":")
            cpu = rhs.strip()
            if cpu and set(cpu) != {"0"}:
                return _normalize(cpu)
    return None


def _try_mac(sys_class_net: Path = Path("/sys/class/net")) -> str | None:
    """First non-loopback MAC address (e.g. ``b8:27:eb:xx:xx:xx`` on Pi)."""
    try:
        ifaces = sorted(p.name for p in sys_class_net.iterdir() if p.name != "lo")
    except OSError:
        return None
    for name in ifaces:
        try:
            addr = (sys_class_net / name / "address").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if addr and addr != "00:00:00:00:00:00":
            return _normalize(addr.replace(":", ""))
    return None


def _try_hostname() -> str | None:
    return _normalize(socket.gethostname())


def _persist_cache(path: Path, value: str) -> None:
    """Best-effort cache write; never raises and stays quiet on read-only dev hosts."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
    except OSError as e:
        # Dev hosts (no /etc/coilshield writable) hit this every test run; debug-only.
        _LOG.debug("could not write %s: %s", path, e)


def derive_device_serial(*, refresh: bool = False) -> str:
    """Resolve the stable device serial; cache to ``SERIAL_CACHE_PATH`` on first call.

    Always returns a non-empty string (final fallback is ``CS-UNKNOWN-<pid>``) so callers
    never need to handle ``None``. Validity for the Supabase `devices.serial` constraint
    is checked separately by ``has_valid_serial()``.
    """
    global _CACHED_SERIAL
    if not refresh and _CACHED_SERIAL is not None:
        return _CACHED_SERIAL

    cache_path = _serial_cache_path()
    for source in (_try_env, lambda: _try_cache_file(cache_path), _try_cpuinfo, _try_mac, _try_hostname):
        try:
            value = source()
        except Exception as e:
            _LOG.debug("serial source %s failed: %s", getattr(source, "__name__", "?"), e)
            value = None
        if value:
            _CACHED_SERIAL = value
            if not _try_cache_file(cache_path):
                _persist_cache(cache_path, value)
            return value

    fallback = _normalize(f"UNKNOWN-{os.getpid():06d}") or "CS-UNKNOWN"
    _CACHED_SERIAL = fallback
    return fallback


def has_valid_serial(value: str | None = None) -> bool:
    """True when the serial satisfies Supabase ``devices.serial CHECK (length >= 8)``."""
    s = (value or derive_device_serial()).strip()
    return len(s) >= MIN_SERIAL_LEN and not s.upper().startswith("CS-UNKNOWN")


def reset_for_tests() -> None:
    """Clear the in-process cache so tests can monkey-patch sources between calls."""
    global _CACHED_SERIAL
    _CACHED_SERIAL = None
