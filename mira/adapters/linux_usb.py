"""USB devices arriving and leaving, from Linux kernel uevents.

The kernel announces every device it adds or removes on a netlink socket
(`NETLINK_KOBJECT_UEVENT`, multicast group 1), readable without privileges.
The socket is watched by the Qt event loop (`QSocketNotifier`), so nothing
polls and no thread is started: a message is read only when one arrives.

Only whole USB devices are reported (``SUBSYSTEM=usb``,
``DEVTYPE=usb_device``), not their interfaces, and never hubs (root hubs
``usbN`` or device class 09). The name comes from the device's sysfs
``product``/``manufacturer`` attributes, read on arrival; it is remembered,
because the attributes are gone by the time the removal arrives. Devices
present at start are read once so their removal can be named, and announce
nothing themselves.

No libudev or pyudev: the kernel's own message format is a few
NUL-separated ``KEY=value`` strings.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSocketNotifier

from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind, SystemEventListener

NETLINK_KOBJECT_UEVENT = 15
KERNEL_GROUP = 1
HUB_CLASS = "09"


def open_uevent_socket() -> socket.socket:
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
    sock.bind((0, KERNEL_GROUP))
    sock.setblocking(False)
    return sock


def parse_uevent(message: bytes) -> dict[str, str] | None:
    """The ``KEY=value`` fields of one kernel uevent; None for anything else.

    udev's own re-broadcasts start with ``libudev`` and are a different
    format; they are not on the kernel group, but are refused all the same.
    """

    if not message or message.startswith(b"libudev"):
        return None
    parts = message.split(b"\0")
    if b"@" not in parts[0]:
        return None
    fields = {}
    for part in parts[1:]:
        key, separator, value = part.partition(b"=")
        if separator:
            fields[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return fields


def _read_attribute(device: Path, name: str) -> str:
    try:
        return (device / name).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def describe_device(device: Path) -> str | None:
    """A person's name for the USB device at ``device``; None for a hub."""

    if device.name.startswith("usb") or _read_attribute(device, "bDeviceClass") == HUB_CLASS:
        return None
    product = _read_attribute(device, "product")
    manufacturer = _read_attribute(device, "manufacturer")
    if product and manufacturer and not product.lower().startswith(manufacturer.lower()):
        return f"{manufacturer} {product}" if len(manufacturer) + len(product) < 40 else product
    return product or manufacturer or "USB device"


class LinuxUsbDeviceSource:
    """A `SystemEventSource` for USB devices; see the module."""

    name = "USB devices (kernel uevents)"

    def __init__(self, *, sysfs: Path = Path("/sys"), open_socket: Callable[[], socket.socket] = open_uevent_socket):
        self._sysfs = sysfs
        self._open_socket = open_socket
        self._socket: socket.socket | None = None
        self._notifier: QSocketNotifier | None = None
        self._listener: SystemEventListener | None = None
        # devpath -> name, for every non-hub device known to be present.
        self._names: dict[str, str] = {}

    def start(self, listener: SystemEventListener) -> bool:
        if self._socket is not None:
            return True
        try:
            self._socket = self._open_socket()
        except OSError:
            return False
        self._listener = listener
        self._names = self._present_devices()
        self._notifier = QSocketNotifier(self._socket.fileno(), QSocketNotifier.Type.Read)
        self._notifier.activated.connect(self.on_readable)
        return True

    def shutdown(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.activated.disconnect(self.on_readable)
            self._notifier.deleteLater()
            self._notifier = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._listener = None

    @property
    def watching(self) -> bool:
        return self._socket is not None

    def _present_devices(self) -> dict[str, str]:
        names = {}
        devices = self._sysfs / "bus" / "usb" / "devices"
        try:
            entries = list(devices.iterdir())
        except OSError:
            return names
        for entry in entries:
            try:
                real = entry.resolve()
                devpath = "/" + str(real.relative_to(self._sysfs.resolve()))
            except (OSError, ValueError):
                continue
            name = describe_device(real)
            if name is not None:
                names[devpath] = name
        return names

    def on_readable(self, *_) -> None:
        """Drain every queued message; called by the notifier."""
        while self._socket is not None:
            try:
                message = self._socket.recv(65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                # ENOBUFS: the kernel dropped messages; carry on with the next.
                continue
            event = self.event_for(parse_uevent(message))
            if event is not None and self._listener is not None:
                self._listener(event)

    def event_for(self, fields: dict[str, str] | None) -> DeviceEvent | None:
        if not fields or fields.get("SUBSYSTEM") != "usb" or fields.get("DEVTYPE") != "usb_device":
            return None
        devpath, action = fields.get("DEVPATH", ""), fields.get("ACTION")
        if action == "add":
            name = describe_device(self._sysfs / devpath.lstrip("/"))
            if name is None:
                return None
            self._names[devpath] = name
            return DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, name, devpath)
        if action == "remove":
            name = self._names.pop(devpath, None)
            if name is None:
                return None  # a hub, or never known
            return DeviceEvent(DeviceKind.USB, DeviceChange.DISCONNECTED, name, devpath)
        return None
