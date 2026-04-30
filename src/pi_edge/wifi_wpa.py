"""
Apply Wi‑Fi credentials on Raspberry Pi OS using ``wpa_cli`` (wpa_supplicant) or
``nmcli`` (NetworkManager).

Does not log passwords. Validates SSID/password lengths and SSID charset.

Backend: ``COILSHIELD_WIFI_BACKEND`` or ``ICCP_WIFI_BACKEND`` — ``wpa_cli`` (default)
or ``nmcli`` / ``networkmanager`` / ``nm``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass


class WpaApplyError(RuntimeError):
    pass


@dataclass
class WifiCredentials:
    ssid: str
    password: str
    interface: str = "wlan0"


_SSID_MAX = 32
_PASS_MIN = 8
_PASS_MAX = 63


def validate_credentials(c: WifiCredentials) -> None:
    ssid = (c.ssid or "").strip()
    pw = c.password or ""
    if not ssid:
        raise WpaApplyError("SSID is empty")
    if len(ssid) > _SSID_MAX:
        raise WpaApplyError(f"SSID longer than {_SSID_MAX} bytes")
    if len(pw) < _PASS_MIN or len(pw) > _PASS_MAX:
        raise WpaApplyError(
            f"password must be {_PASS_MIN}–{_PASS_MAX} chars (WPA2-PSK)"
        )
    # Printable ASCII excluding control chars (conservative; UTF-8 SSID rare on Pi setup)
    if not re.fullmatch(r"[\x20-\x7e]+", ssid):
        raise WpaApplyError("SSID must be printable ASCII for this provisioner")


def _run_wpa_cli(iface: str, *args: str, timeout: float = 30.0) -> str:
    cmd = ["wpa_cli", "-i", iface, *args]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise WpaApplyError(
            "wpa_cli not found — install wpasupplicant or use NetworkManager path"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise WpaApplyError("wpa_cli timeout") from e
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        msg = err or out or f"exit {r.returncode}"
        raise WpaApplyError(f"wpa_cli failed: {msg}")
    return out


NMCLI_CONN_NAME = "CoilShield-ICCP"


def _wifi_backend() -> str:
    raw = (
        os.environ.get("COILSHIELD_WIFI_BACKEND")
        or os.environ.get("ICCP_WIFI_BACKEND")
        or "wpa_cli"
    ).strip().lower()
    if raw in ("nm", "nmcli", "networkmanager"):
        return "nmcli"
    return "wpa_cli"


def _run_nmcli(*args: str, timeout: float = 120.0) -> str:
    cmd = ["nmcli", *args]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise WpaApplyError(
            "nmcli not found — install NetworkManager or use COILSHIELD_WIFI_BACKEND=wpa_cli"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise WpaApplyError("nmcli timeout") from e
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        msg = err or out or f"exit {r.returncode}"
        raise WpaApplyError(f"nmcli failed: {msg}")
    return out


def _nmcli_delete_profile() -> None:
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME", "connection", "show"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return
    if r.returncode != 0 or not r.stdout:
        return
    for line in r.stdout.splitlines():
        if line.strip() == NMCLI_CONN_NAME:
            subprocess.run(
                ["nmcli", "connection", "delete", NMCLI_CONN_NAME],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return


def apply_credentials_nmcli(
    c: WifiCredentials, *, replace_all_networks: bool = True
) -> None:
    """Create or replace a saved WPA2-PSK connection via NetworkManager."""
    validate_credentials(c)
    iface = c.interface.strip() or "wlan0"
    _run_nmcli("radio", "wifi", "on", timeout=15.0)
    if replace_all_networks:
        _nmcli_delete_profile()
    # Password only in argv — never logged here.
    _run_nmcli(
        "device",
        "wifi",
        "connect",
        c.ssid,
        "password",
        c.password,
        "ifname",
        iface,
        "name",
        NMCLI_CONN_NAME,
    )


def _apply_credentials_wpa_cli(
    c: WifiCredentials, *, replace_all_networks: bool = True
) -> None:
    validate_credentials(c)
    iface = c.interface.strip() or "wlan0"

    _run_wpa_cli(iface, "reconfigure")  # ensure daemon is up; may fail if down — ignore
    try:
        _run_wpa_cli(iface, "ping")
    except WpaApplyError:
        pass

    if replace_all_networks:
        # Remove networks from highest id downward (wpa_cli remove_network <id>)
        list_out = _run_wpa_cli(iface, "list_networks")
        # header: network id / ssid / bssid / flags
        ids: list[int] = []
        for line in list_out.splitlines()[1:]:
            parts = line.split("\t")
            if parts and parts[0].isdigit():
                ids.append(int(parts[0]))
        for nid in sorted(ids, reverse=True):
            _run_wpa_cli(iface, "remove_network", str(nid))

    nid_s = _run_wpa_cli(iface, "add_network").strip()
    if not nid_s.isdigit():
        raise WpaApplyError(f"unexpected add_network output: {nid_s!r}")
    nid = nid_s

    # Do not echo password — set_network via argv only in process table briefly.
    _run_wpa_cli(iface, "set_network", nid, "ssid", f'"{_escape_ssid(c.ssid)}"')
    _run_wpa_cli(iface, "set_network", nid, "psk", f'"{_escape_psk(c.password)}"')
    _run_wpa_cli(iface, "set_network", nid, "key_mgmt", "WPA-PSK")
    _run_wpa_cli(iface, "enable_network", nid)
    _run_wpa_cli(iface, "save_config")


def apply_credentials(
    c: WifiCredentials, *, replace_all_networks: bool = True
) -> None:
    """
    Add (or replace) a WPA-PSK network and enable it.

    If ``replace_all_networks`` is True, removes existing CoilShield / wpa
    networks first (factory-style provisioning). Set False to append (wpa_cli
    only meaningful for wpa_cli backend).
    """
    backend = _wifi_backend()
    if backend == "nmcli":
        apply_credentials_nmcli(c, replace_all_networks=replace_all_networks)
    else:
        _apply_credentials_wpa_cli(c, replace_all_networks=replace_all_networks)


def _escape_ssid(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _escape_psk(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def wait_for_ipv4(iface: str = "wlan0", timeout_s: float = 90.0) -> str | None:
    """Return first IPv4 address on ``iface`` if it appears within timeout (best-effort)."""
    import socket
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", "dev", iface],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            time.sleep(2)
            continue
        if r.returncode != 0:
            time.sleep(2)
            continue
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1].startswith("inet"):
                addr = parts[3].split("/")[0]
                try:
                    socket.inet_aton(addr)
                except OSError:
                    continue
                if not addr.startswith("127."):
                    return addr
        time.sleep(2)
    return None


def main_cli() -> int:
    """Minimal CLI for testing: read SSID/password from env (not for production)."""
    ssid = os.environ.get("COILSHIELD_WIFI_SSID", "").strip()
    pw = os.environ.get("COILSHIELD_WIFI_PASSWORD", "")
    iface = os.environ.get("COILSHIELD_WIFI_IFACE", "wlan0").strip()
    if not ssid:
        print("Set COILSHIELD_WIFI_SSID and COILSHIELD_WIFI_PASSWORD", file=sys.stderr)
        return 2
    try:
        apply_credentials(WifiCredentials(ssid=ssid, password=pw, interface=iface))
    except WpaApplyError as e:
        print(e, file=sys.stderr)
        return 1
    ip = wait_for_ipv4(iface)
    print(f"OK ip={ip or 'unknown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
