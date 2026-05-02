"""
BlueZ GATT peripheral for Wi‑Fi provisioning (Linux + dbus-python + GLib).

Adapted from BlueZ ``example-gatt-server`` / ``example-advertisement`` patterns.
Does not log Wi‑Fi passwords.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time
from collections.abc import Sequence
from typing import Any, Callable

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from pi_edge import uuids as u
from pi_edge.ensure_bluetooth import ensure_bluetooth_enabled, wait_for_ble_adapter_path
from pi_edge.wifi_wpa import WpaApplyError, WifiCredentials, apply_credentials, wait_for_ipv4

BLUEZ_SERVICE_NAME = "org.bluez"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"


class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class FailedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.Failed"


def _firmware_version() -> str:
    try:
        from importlib.metadata import version

        return version("coilshield-iccp")
    except Exception:
        return "unknown"


# Status bytes — packages/api-contract/ble-protocol.md § Status
ST_IDLE = 0
ST_WIFI_RECEIVED = 1
ST_CONNECTING = 2
ST_ONLINE = 3
ST_CLOUD_BOUND = 4
ST_ERROR = 255
ERR_PARSE = 1
ERR_VALIDATION = 2
ERR_JOIN = 3
ERR_INSTALL_CODE = 4

# Command opcodes — must stay in sync with packages/api-contract/ble-protocol.md
OP_HEARTBEAT = 0x05
OP_SET_INSTALL_CODE = 0x06
OP_WIFI_SCAN = 0x07


def _dbus_bytes(raw: bytes) -> dbus.Array:
    return dbus.Array([dbus.Byte(b) for b in raw], signature="y")


def _device_info_json() -> bytes:
    from device_identity import derive_device_serial

    serial = derive_device_serial()
    model = (os.environ.get("COILSHIELD_MODEL") or "ICCP").strip()
    payload = {"serial": serial, "model": model, "fw": _firmware_version()}
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")[:240]


def _wifi_scan_json_bytes(ssids: list[str]) -> bytes:
    raw = json.dumps({"ssids": ssids}, separators=(",", ":")).encode("utf-8")
    return raw[:512]


def _scan_wifi_ssids_nmcli() -> list[str]:
    """SSID list from Pi radio via NetworkManager (ble-protocol.md § wifi_scan_results)."""
    import subprocess

    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "SSID", "device", "wifi", "list"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if not s or s in ("--", "\\"):
            continue
        if len(s) > 32:
            s = s[:32]
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:24]


class Application(dbus.service.Object):
    """Root object: ObjectManager only (BlueZ GattApplication pattern)."""

    def __init__(self, bus: dbus.SystemBus, app_path: str) -> None:
        self.path = app_path
        self.services: list[Service] = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self) -> dbus.ObjectPath:
        return dbus.ObjectPath(self.path)

    def add_service(self, service: Service) -> None:
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self) -> dict:
        response: dict = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            for chrc in service.get_characteristics():
                response[chrc.get_path()] = chrc.get_properties()
        return response


class Service(dbus.service.Object):
    PATH_BASE = "/com/coilshield/iccp/svc"

    def __init__(
        self, bus: dbus.SystemBus, index: int, uuid: str, primary: bool
    ) -> None:
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics: list[Characteristic] = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self) -> dict:
        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": dbus.Boolean(self.primary),
                "Characteristics": dbus.Array(
                    self.get_characteristic_paths(), signature="o"
                ),
            }
        }

    def get_path(self) -> dbus.ObjectPath:
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, characteristic: Characteristic) -> None:
        self.characteristics.append(characteristic)

    def get_characteristic_paths(self) -> list[dbus.ObjectPath]:
        return [c.get_path() for c in self.characteristics]

    def get_characteristics(self) -> list[Characteristic]:
        return self.characteristics

    @dbus.service.method(
        DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}"
    )
    def GetAll(self, interface: str) -> dict:
        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_SERVICE_IFACE]


class Characteristic(dbus.service.Object):
    def __init__(
        self,
        bus: dbus.SystemBus,
        index: int,
        uuid: str,
        flags: Sequence[str],
        service: Service,
    ) -> None:
        self.path = service.path + "/ch" + str(index)
        self.bus = bus
        self.uuid = uuid
        self.service = service
        self.flags = list(flags)
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self) -> dict:
        return {
            GATT_CHRC_IFACE: {
                "Service": self.service.get_path(),
                "UUID": self.uuid,
                "Flags": self.flags,
                "Descriptors": dbus.Array([], signature="o"),
            }
        }

    def get_path(self) -> dbus.ObjectPath:
        return dbus.ObjectPath(self.path)

    @dbus.service.method(
        DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}"
    )
    def GetAll(self, interface: str) -> dict:
        if interface != GATT_CHRC_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_CHRC_IFACE]

    @dbus.service.method(
        GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay"
    )
    def ReadValue(self, options: dict) -> dbus.Array:
        raise dbus.exceptions.DBusException(
            "org.bluez.Error.NotSupported", "read not supported"
        )

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: dbus.Array, options: dict) -> None:
        raise dbus.exceptions.DBusException(
            "org.bluez.Error.NotSupported", "write not supported"
        )

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self) -> None:
        raise dbus.exceptions.DBusException(
            "org.bluez.Error.NotSupported", "notify not supported"
        )

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self) -> None:
        raise dbus.exceptions.DBusException(
            "org.bluez.Error.NotSupported", "notify not supported"
        )

    @dbus.service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
    def PropertiesChanged(
        self, interface: str, changed: dict, invalidated: list
    ) -> None:
        pass


class DeviceInfoCharacteristic(Characteristic):
    """Read JSON ``serial`` / ``model`` / ``fw`` — ble-protocol.md."""

    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self, bus, index, u.CHAR_DEVICE_INFO, ["read"], service
        )

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        return _dbus_bytes(_device_info_json())


class WifiCredentialsCharacteristic(Characteristic):
    """Write UTF-8 JSON ``{\"ssid\",\"psk\"}`` — ble-protocol.md."""

    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self,
            bus,
            index,
            u.CHAR_WIFI_CREDENTIALS,
            ["write", "encrypt-write"],
            service,
        )
        self._service = service

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: dbus.Array, options: dict) -> None:
        raw = bytes(bytearray(int(b) for b in value))
        self._service.on_wifi_json_written(raw)


class ContractStatusCharacteristic(Characteristic):
    """Read + notify: 2-byte LE ``state``, ``code`` — ble-protocol.md."""

    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self, bus, index, u.CHAR_STATUS, ["read", "notify"], service
        )
        self._service = service
        self.notifying = False

    def _emit_u8_pair(self) -> None:
        if not self.notifying:
            return
        b = self._service.status_bytes()
        self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": _dbus_bytes(b)}, [])

    def push_u8(self) -> None:
        GLib.idle_add(self._do_push)

    def _do_push(self) -> bool:
        self._emit_u8_pair()
        return False

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        return _dbus_bytes(self._service.status_bytes())

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self) -> None:
        if self.notifying:
            return
        self.notifying = True
        # First notify edge: idle (0,0) per contract
        self._service.set_status(ST_IDLE, 0, notify=False)
        self._emit_u8_pair()

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self) -> None:
        self.notifying = False


class WifiScanResultsCharacteristic(Characteristic):
    """Read + notify: UTF-8 JSON ``ssids`` — ble-protocol.md § wifi_scan_results."""

    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self,
            bus,
            index,
            u.CHAR_WIFI_SCAN_RESULTS,
            ["read", "notify"],
            service,
        )
        self._service = service
        self._value = _wifi_scan_json_bytes([])
        self.notifying = False

    def set_payload(self, payload: bytes) -> None:
        self._value = payload[:512]
        if self.notifying:
            self.PropertiesChanged(
                GATT_CHRC_IFACE, {"Value": _dbus_bytes(self._value)}, []
            )

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options: dict) -> dbus.Array:
        return _dbus_bytes(self._value)

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self) -> None:
        if self.notifying:
            return
        self.notifying = True
        self.PropertiesChanged(
            GATT_CHRC_IFACE, {"Value": _dbus_bytes(self._value)}, []
        )

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self) -> None:
        self.notifying = False


class CommandCharacteristic(Characteristic):
    """Write commissioning opcodes (binary) — ble-protocol.md § Commands."""

    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self, bus, index, u.CHAR_COMMAND, ["write", "encrypt-write"], service
        )
        self._service = service

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: dbus.Array, options: dict) -> None:
        raw = bytes(bytearray(int(b) for b in value))
        if not raw:
            return
        op = raw[0]
        if op == OP_WIFI_SCAN:
            self._service.request_wifi_scan()
            return
        if op == OP_SET_INSTALL_CODE:
            self._handle_set_install_code(raw[1:])
            return
        if op != OP_HEARTBEAT:
            print(f"ble_provision: command opcode 0x{op:02x} len={len(raw)}")

    def _handle_set_install_code(self, body: bytes) -> None:
        """Exchange the install code for a per-device JWT (ble-protocol.md § set_install_code)."""
        try:
            code = body.decode("utf-8").strip()
        except UnicodeDecodeError:
            self._service.set_status(ST_ERROR, ERR_VALIDATION)
            return
        if not (8 <= len(code) <= 64):
            self._service.set_status(ST_ERROR, ERR_VALIDATION)
            return
        try:
            from cloud_bootstrap import BootstrapError, redeem_install_code
        except ImportError as e:
            print(f"ble_provision: install_code redeem unavailable ({e})")
            self._service.set_status(ST_ERROR, ERR_INSTALL_CODE)
            return
        try:
            redeem_install_code(code)
        except BootstrapError as e:
            print(f"ble_provision: install_code rejected: {e}")
            self._service.set_status(ST_ERROR, ERR_INSTALL_CODE)
            return
        except Exception as e:
            print(f"ble_provision: install_code error: {type(e).__name__}: {e}")
            self._service.set_status(ST_ERROR, ERR_INSTALL_CODE)
            return
        self._service.set_status(ST_CLOUD_BOUND, 0)


class TelemetryCharacteristic(Characteristic):
    """Notify-only: min 8 bytes shift_mV f32 LE + seconds f32 LE."""

    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self, bus, index, u.CHAR_TELEMETRY, ["notify"], service
        )
        self._service = service
        self.notifying = False
        self._tick: int | None = None

    def _emit_frame(self) -> None:
        if not self.notifying:
            return
        t = time.monotonic() - self._service.start_mono
        payload = struct.pack("<ff", 0.0, float(t))
        self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": _dbus_bytes(payload)}, [])

    def _tick_cb(self) -> bool:
        if not self.notifying:
            self._tick = None
            return False
        self._emit_frame()
        return True

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self) -> None:
        if self.notifying:
            return
        self.notifying = True
        self._emit_frame()
        # Optional slow heartbeat so centrals see liveness without real ADC yet
        raw = (os.environ.get("ICCP_BLE_TELEMETRY_INTERVAL_S") or "").strip()
        try:
            sec = float(raw) if raw else 2.0
        except ValueError:
            sec = 2.0
        if sec > 0:
            self._tick = GLib.timeout_add(max(500, int(sec * 1000)), self._tick_cb)

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self) -> None:
        if self._tick is not None:
            GLib.source_remove(self._tick)
            self._tick = None
        self.notifying = False


class ProvisionService(Service):
    def __init__(
        self,
        bus: dbus.SystemBus,
        index: int,
        iface: str,
        on_wifi_ok: Callable[[str | None], None] | None,
    ) -> None:
        Service.__init__(self, bus, index, u.PROVISIONING_SERVICE, True)
        self.wlan_iface = iface
        self.on_wifi_ok = on_wifi_ok
        self.start_mono = time.monotonic()
        self._st_u8 = ST_IDLE
        self._code_u8 = 0
        self.last_error: str | None = None
        self.last_ip: str | None = None
        self.device_info = DeviceInfoCharacteristic(bus, 0, self)
        self.wifi_cred = WifiCredentialsCharacteristic(bus, 1, self)
        self.status = ContractStatusCharacteristic(bus, 2, self)
        self.command = CommandCharacteristic(bus, 3, self)
        self.telemetry = TelemetryCharacteristic(bus, 4, self)
        self.wifi_scan = WifiScanResultsCharacteristic(bus, 5, self)
        self.add_characteristic(self.device_info)
        self.add_characteristic(self.wifi_cred)
        self.add_characteristic(self.status)
        self.add_characteristic(self.command)
        self.add_characteristic(self.telemetry)
        self.add_characteristic(self.wifi_scan)

    def request_wifi_scan(self) -> None:
        def work() -> None:
            ssids = _scan_wifi_ssids_nmcli()
            raw = _wifi_scan_json_bytes(ssids)

            def push() -> bool:
                self.wifi_scan.set_payload(raw)
                return False

            GLib.idle_add(push)

        threading.Thread(target=work, daemon=True).start()

    def status_bytes(self) -> bytes:
        return bytes((self._st_u8 & 0xFF, self._code_u8 & 0xFF))

    def set_status(self, st: int, code: int = 0, *, notify: bool = True) -> None:
        self._st_u8 = st & 0xFF
        self._code_u8 = code & 0xFF
        if notify and self.status.notifying:
            self.status.push_u8()

    def on_wifi_json_written(self, raw: bytes) -> None:
        try:
            obj = json.loads(raw.decode("utf-8"))
            ssid = str(obj.get("ssid", "")).strip()
            psk = str(obj.get("psk", obj.get("password", ""))).strip()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            self.set_status(ST_ERROR, ERR_PARSE)
            return
        if not ssid or len(ssid) > 32 or len(psk) > 128:
            self.set_status(ST_ERROR, ERR_VALIDATION)
            return
        print("ble_provision: wifi_credentials write (ssid len only)", len(ssid))
        self.set_status(ST_WIFI_RECEIVED, 0)
        self._apply_wifi_thread(ssid, psk)

    def _apply_wifi_thread(self, ssid: str, password: str) -> None:
        def work() -> None:
            def push_connecting() -> bool:
                self.set_status(ST_CONNECTING, 0)
                return False

            GLib.idle_add(push_connecting)
            try:
                apply_credentials(
                    WifiCredentials(
                        ssid=ssid,
                        password=password,
                        interface=self.wlan_iface,
                    )
                )
                ip = wait_for_ipv4(self.wlan_iface)
                self.last_ip = ip

                def push_ok() -> bool:
                    self.set_status(ST_ONLINE, 0)
                    return False

                GLib.idle_add(push_ok)
                if self.on_wifi_ok:
                    ok_cb = self.on_wifi_ok

                    def call_cb(ip_arg: str | None = ip) -> bool:
                        ok_cb(ip_arg)
                        return False

                    GLib.idle_add(call_cb)
            except (WpaApplyError, OSError, RuntimeError) as e:
                self.last_error = str(e)

                def push_err() -> bool:
                    self.set_status(ST_ERROR, ERR_JOIN)
                    return False

                GLib.idle_add(push_err)

        threading.Thread(target=work, daemon=True).start()


class Advertisement(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, index: int, local_name: str) -> None:
        self.path = f"/com/coilshield/iccp/ad{index}"
        self.bus = bus
        self.ad_type = "peripheral"
        self._service_uuids = [u.PROVISIONING_SERVICE]
        self._local_name = local_name
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self) -> dict:
        return {
            LE_ADVERTISEMENT_IFACE: {
                "Type": self.ad_type,
                "ServiceUUIDs": dbus.Array(self._service_uuids, signature="s"),
                "LocalName": dbus.String(self._local_name),
            }
        }

    def get_path(self) -> dbus.ObjectPath:
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict:
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self) -> None:
        print(f"ble_provision: advertisement released {self.path}")


def find_adapter_path(bus: dbus.SystemBus) -> str | None:
    remote_om = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE
    )
    objects = remote_om.GetManagedObjects()
    for o, props in objects.items():
        if (
            GATT_MANAGER_IFACE in props
            and LE_ADVERTISING_MANAGER_IFACE in props
        ):
            return str(o)
    return None


def run_ble_provision_server(
    *,
    iface: str = "wlan0",
    local_name: str = "CoilShield-ICCP",
    window_s: float = 600.0,
    on_wifi_ok: Callable[[str | None], None] | None = None,
) -> None:
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    ensure_bluetooth_enabled(verbose=True)
    bus = dbus.SystemBus()
    adapter = wait_for_ble_adapter_path(lambda: find_adapter_path(bus))
    if not adapter:
        raise RuntimeError(
            "No Bluetooth adapter with GattManager1+LEAdvertisingManager1 "
            "(check rfkill, dtparam=krnbt=on / hci0, and bluetooth.service)"
        )

    mainloop = GLib.MainLoop()
    app_path = "/com/coilshield/iccp/gatt_app"
    app = Application(bus, app_path)
    prov = ProvisionService(bus, 0, iface, on_wifi_ok)
    app.add_service(prov)

    ad = Advertisement(bus, 0, local_name)

    adapter_props = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, adapter), DBUS_PROP_IFACE
    )
    adapter_props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))

    gatt_mgr = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, adapter), GATT_MANAGER_IFACE
    )
    ad_mgr = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, adapter), LE_ADVERTISING_MANAGER_IFACE
    )

    registered_ad = [False]
    registered_app = [False]

    def quit_clean() -> bool:
        try:
            if registered_ad[0]:
                ad_mgr.UnregisterAdvertisement(ad.get_path())
        except dbus.exceptions.DBusException:
            pass
        try:
            if registered_app[0]:
                gatt_mgr.UnregisterApplication(app.get_path())
        except dbus.exceptions.DBusException:
            pass
        mainloop.quit()
        return False

    def on_reg_app_error(err: dbus.exceptions.DBusException) -> None:
        print(f"ble_provision: RegisterApplication failed: {err}")
        GLib.idle_add(quit_clean)

    def on_reg_app_ok() -> None:
        registered_app[0] = True
        print("ble_provision: GATT application registered")

        def on_ad_ok() -> None:
            registered_ad[0] = True
            print("ble_provision: advertisement registered")

        def on_ad_err(err: dbus.exceptions.DBusException) -> None:
            print(f"ble_provision: RegisterAdvertisement failed: {err}")
            GLib.idle_add(quit_clean)

        ad_mgr.RegisterAdvertisement(
            ad.get_path(), {}, reply_handler=on_ad_ok, error_handler=on_ad_err
        )

    gatt_mgr.RegisterApplication(
        app.get_path(),
        {},
        reply_handler=on_reg_app_ok,
        error_handler=on_reg_app_error,
    )

    if window_s > 0:
        GLib.timeout_add(int(window_s * 1000), quit_clean)

    print(
        f"ble_provision: listening (adapter={adapter}, window_s={window_s}) — "
        "encrypt-write requires paired connection"
    )
    try:
        mainloop.run()
    finally:
        try:
            if registered_ad[0]:
                ad_mgr.UnregisterAdvertisement(ad.get_path())
        except dbus.exceptions.DBusException:
            pass
        try:
            if registered_app[0]:
                gatt_mgr.UnregisterApplication(app.get_path())
        except dbus.exceptions.DBusException:
            pass
