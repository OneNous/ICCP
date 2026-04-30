"""
BlueZ GATT peripheral for Wi‑Fi provisioning (Linux + dbus-python + GLib).

Adapted from BlueZ ``example-gatt-server`` / ``example-advertisement`` patterns.
Does not log Wi‑Fi passwords.
"""

from __future__ import annotations

import json
import os
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


def _uptime_s(start: float) -> float:
    return round(time.monotonic() - start, 3)


def _status_payload(
    *,
    state: str,
    start: float,
    ip: str | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "iccp.ble.status.v1",
        "state": state,
        "uptime_s": _uptime_s(start),
        "version": _firmware_version(),
        "ip": ip or "",
        "last_error": last_error or "",
    }


def _dbus_bytes_utf8(obj: dict[str, Any]) -> dbus.Array:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return dbus.Array([dbus.Byte(b) for b in raw], signature="y")


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


class StatusCharacteristic(Characteristic):
    """Notify-only status JSON (``iccp.ble.status.v1``-shaped dict)."""

    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self, bus, index, u.CHAR_STATUS, ["notify"], service
        )
        self._service = service
        self.notifying = False
        self._heartbeat_source: int | None = None

    def _heartbeat_interval_ms(self) -> int:
        raw = (os.environ.get("ICCP_BLE_STATUS_HEARTBEAT_S") or "").strip()
        if not raw:
            return 0
        try:
            sec = float(raw)
        except ValueError:
            return 0
        if sec <= 0:
            return 0
        return max(1000, int(sec * 1000))

    def _heartbeat_tick(self) -> bool:
        if not self.notifying:
            self._heartbeat_source = None
            return False
        self._emit(
            _status_payload(
                state=self._service.state,
                start=self._service.start_mono,
                ip=self._service.last_ip,
                last_error=self._service.last_error,
            )
        )
        return True

    def _emit(self, payload: dict[str, Any]) -> None:
        if not self.notifying:
            return
        self.PropertiesChanged(
            GATT_CHRC_IFACE, {"Value": _dbus_bytes_utf8(payload)}, []
        )

    def push(self, payload: dict[str, Any]) -> None:
        GLib.idle_add(lambda: self._do_push(payload))

    def _do_push(self, payload: dict[str, Any]) -> bool:
        self._emit(payload)
        return False

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self) -> None:
        if self.notifying:
            return
        self.notifying = True
        self._emit(
            _status_payload(
                state=self._service.state,
                start=self._service.start_mono,
                ip=self._service.last_ip,
                last_error=self._service.last_error,
            )
        )
        ms = self._heartbeat_interval_ms()
        if ms > 0:
            self._heartbeat_source = GLib.timeout_add(ms, self._heartbeat_tick)

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self) -> None:
        if self._heartbeat_source is not None:
            GLib.source_remove(self._heartbeat_source)
            self._heartbeat_source = None
        self.notifying = False


class SsidCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self,
            bus,
            index,
            u.CHAR_WIFI_SSID,
            ["encrypt-write"],
            service,
        )
        self._service = service

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: dbus.Array, options: dict) -> None:
        raw = bytes(bytearray(int(b) for b in value))
        try:
            self._service.ssid = raw.decode("utf-8").strip()
        except UnicodeDecodeError as e:
            raise FailedException(str(e)) from e
        # Never log SSID body at info; length only.
        print(f"ble_provision: ssid write len={len(self._service.ssid)}")
        self._service.status.push(
            _status_payload(
                state="ssid_received",
                start=self._service.start_mono,
                last_error=None,
            )
        )


class PasswordCharacteristic(Characteristic):
    def __init__(self, bus: dbus.SystemBus, index: int, service: "ProvisionService"):
        Characteristic.__init__(
            self,
            bus,
            index,
            u.CHAR_WIFI_PASSWORD,
            ["encrypt-write"],
            service,
        )
        self._service = service

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value: dbus.Array, options: dict) -> None:
        raw = bytes(bytearray(int(b) for b in value))
        try:
            password = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise FailedException(str(e)) from e
        print("ble_provision: password write (len hidden)")
        self._service.on_password_written(password)


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
        self.ssid = ""
        self.state = "advertising"
        self.last_error: str | None = None
        self.last_ip: str | None = None
        self.status = StatusCharacteristic(bus, 0, self)
        self.add_characteristic(self.status)
        self.add_characteristic(SsidCharacteristic(bus, 1, self))
        self.add_characteristic(PasswordCharacteristic(bus, 2, self))

    def on_password_written(self, password: str) -> None:
        def work() -> None:
            self.state = "wifi_applying"

            def push_applying() -> bool:
                self.status.push(
                    _status_payload(
                        state="wifi_applying",
                        start=self.start_mono,
                        last_error=None,
                    )
                )
                return False

            GLib.idle_add(push_applying)
            try:
                apply_credentials(
                    WifiCredentials(
                        ssid=self.ssid,
                        password=password,
                        interface=self.wlan_iface,
                    )
                )
                ip = wait_for_ipv4(self.wlan_iface)
                self.last_ip = ip
                self.state = "wifi_ok"

                def push_ok() -> bool:
                    self.status.push(
                        _status_payload(
                            state="wifi_ok",
                            start=self.start_mono,
                            ip=ip,
                            last_error=None,
                        )
                    )
                    return False

                GLib.idle_add(push_ok)
                if self.on_wifi_ok:
                    ok_cb = self.on_wifi_ok

                    def call_cb(ip_arg: str | None = ip) -> bool:
                        ok_cb(ip_arg)
                        return False

                    GLib.idle_add(call_cb)
            except (WpaApplyError, OSError, RuntimeError) as e:
                self.state = "error"
                err_msg = str(e)
                self.last_error = err_msg

                def push_err() -> bool:
                    self.status.push(
                        _status_payload(
                            state="error",
                            start=self.start_mono,
                            last_error=err_msg,
                        )
                    )
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
    bus = dbus.SystemBus()
    adapter = find_adapter_path(bus)
    if not adapter:
        raise RuntimeError(
            "No Bluetooth adapter with GattManager1+LEAdvertisingManager1"
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
