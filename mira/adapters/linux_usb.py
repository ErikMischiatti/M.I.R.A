"""USB devices arriving and leaving, from Linux kernel uevents.

The kernel announces every device it adds or removes on a netlink socket
(`NETLINK_KOBJECT_UEVENT`, multicast group 1), readable without privileges.
The socket is watched by the Qt event loop (`QSocketNotifier`), so nothing
polls and no thread is started: a message is read only when one arrives.

Only whole USB devices are reported (``SUBSYSTEM=usb``,
``DEVTYPE=usb_device``), not their interfaces, and never hubs (root hubs
``usbN`` or device class 09). One physical connection is one event, however
many interfaces a composite device has: the interfaces' own uevents are
ignored, and are only read from sysfs to classify the device.

**When a device counts as arrived.** The kernel sends ``add`` for a device
*before* it chooses a configuration and creates the interfaces, then
``bind`` once the generic USB driver has done so. A device is announced as
soon as it is configured — at ``add`` if sysfs already lists every interface
of its configuration (``bNumInterfaces``) by the time the message is read,
otherwise at its ``bind`` — so the classification sees all interfaces and a
device is never announced twice. A device removed before it was configured
announces nothing.

**What is read.** From the device's sysfs directory: ``product``,
``manufacturer``, ``idVendor``/``idProduct``, ``bDeviceClass`` and, per
interface, its class triple and description string. Never ``serial``: no
persistent hardware identifier reaches an event or a log line. Everything is
remembered on arrival, because the attributes are gone by the time the
removal arrives. Devices present at start are read once so their removal can
be named, and announce nothing themselves.

**Classification** (`classify_usb_device`) uses only the classes the device
declares, conservatively, first match wins:

    SMARTPHONE  an Android debug/fastboot interface (ff/42/01, ff/42/03), an
                interface named "MTP", or a product named "iPhone"
    STORAGE     mass storage (08)
    CAMERA      video (0e) or still image / PTP (06)
    AUDIO       audio (01)
    INPUT       human interface device (03)
    UNKNOWN     anything else

so a webcam with a microphone is a camera and a headset with volume buttons
is an audio device. Other platforms or richer sources (udev's hardware
database, ADB) can classify differently behind the same `DeviceEvent`.

No libudev or pyudev: the kernel's own message format is a few
NUL-separated ``KEY=value`` strings.
"""

from __future__ import annotations

import errno
import socket
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSocketNotifier

from mira.domain.devices import DeviceCategory
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind, SystemEventListener

NETLINK_KOBJECT_UEVENT = 15
KERNEL_GROUP = 1
HUB_CLASS = 0x09

# USB-IF class codes (https://www.usb.org/defined-class-codes).
AUDIO_CLASS = 0x01
HID_CLASS = 0x03
STILL_IMAGE_CLASS = 0x06
MASS_STORAGE_CLASS = 0x08
VIDEO_CLASS = 0x0E
VENDOR_CLASS = 0xFF
# Android's debug bridge and fastboot: vendor class, subclass 0x42.
ANDROID_SUBCLASS = 0x42
ANDROID_PROTOCOLS = frozenset({0x01, 0x03})

# Per interface class, first match wins (see the module).
_CLASS_CATEGORY = (
    (MASS_STORAGE_CLASS, DeviceCategory.STORAGE),
    (VIDEO_CLASS, DeviceCategory.CAMERA),
    (STILL_IMAGE_CLASS, DeviceCategory.CAMERA),
    (AUDIO_CLASS, DeviceCategory.AUDIO),
    (HID_CLASS, DeviceCategory.INPUT),
)
# Device-level classes that say "look at the interfaces" rather than what
# the device is: per interface, miscellaneous/composite, vendor specific.
_UNINFORMATIVE_DEVICE_CLASSES = frozenset({0x00, 0xEF, VENDOR_CLASS})


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


# --- Reading sysfs -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsbInterface:
    """One interface of a configured device: its class triple and description."""

    cls: int
    subclass: int
    protocol: int
    description: str = ""

    def __str__(self) -> str:
        return f"{self.cls:02x}/{self.subclass:02x}/{self.protocol:02x}"


@dataclass(frozen=True, slots=True)
class UsbDeviceInfo:
    """What sysfs says about one device; names are empty when it says nothing."""

    product: str
    manufacturer: str
    vendor_id: str
    product_id: str
    device_class: int | None
    interfaces: tuple[UsbInterface, ...]


def _read_attribute(path: Path, name: str) -> str:
    try:
        return (path / name).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _hex(text: str) -> int | None:
    try:
        return int(text, 16)
    except ValueError:
        return None


def is_hub(device: Path) -> bool:
    return device.name.startswith("usb") or _hex(_read_attribute(device, "bDeviceClass")) == HUB_CLASS


def _interface_directories(device: Path) -> list[Path]:
    # Interfaces are named "<device>:<configuration>.<interface>", e.g. 3-2:1.0.
    try:
        return sorted(path for path in device.glob(f"{device.name}:*") if (path / "bInterfaceClass").exists())
    except OSError:
        return []


def is_configured(device: Path) -> bool:
    """Whether every interface of the active configuration is in sysfs.

    ``bNumInterfaces`` is empty until the kernel has chosen a configuration.
    """
    try:
        expected = int(_read_attribute(device, "bNumInterfaces"))
    except ValueError:
        return False
    return len(_interface_directories(device)) >= expected


def read_interfaces(device: Path) -> tuple[UsbInterface, ...]:
    interfaces = []
    for path in _interface_directories(device):
        cls, subclass, protocol = (
            _hex(_read_attribute(path, name)) for name in ("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol")
        )
        if cls is None:
            continue
        interfaces.append(UsbInterface(cls, subclass or 0, protocol or 0, _read_attribute(path, "interface")))
    return tuple(interfaces)


def read_device(device: Path) -> UsbDeviceInfo | None:
    """What sysfs says about the USB device at ``device``; None for a hub."""

    if is_hub(device):
        return None
    return UsbDeviceInfo(
        product=_read_attribute(device, "product"),
        manufacturer=_read_attribute(device, "manufacturer"),
        vendor_id=_read_attribute(device, "idVendor"),
        product_id=_read_attribute(device, "idProduct"),
        device_class=_hex(_read_attribute(device, "bDeviceClass")),
        interfaces=read_interfaces(device),
    )


def device_name(product: str, manufacturer: str) -> str:
    """A person's name for a device; empty when it names itself nothing."""
    if product and manufacturer and not product.lower().startswith(manufacturer.lower()):
        return f"{manufacturer} {product}" if len(manufacturer) + len(product) < 40 else product
    return product or manufacturer


def describe_device(device: Path) -> str | None:
    """A person's name for the USB device at ``device``; None for a hub."""
    info = read_device(device)
    return None if info is None else device_name(info.product, info.manufacturer)


def classify_usb_device(
    interfaces: Iterable[UsbInterface], *, device_class: int | None = None, product: str = ""
) -> DeviceCategory:
    """The category a device's own descriptors support; see the module."""

    interfaces = tuple(interfaces)
    for interface in interfaces:
        if interface.cls == VENDOR_CLASS and interface.subclass == ANDROID_SUBCLASS \
                and interface.protocol in ANDROID_PROTOCOLS:
            return DeviceCategory.SMARTPHONE
        if interface.description.strip().upper() == "MTP":
            return DeviceCategory.SMARTPHONE
    if "iphone" in product.lower():
        return DeviceCategory.SMARTPHONE
    classes = {interface.cls for interface in interfaces}
    if device_class is not None and device_class not in _UNINFORMATIVE_DEVICE_CLASSES:
        classes.add(device_class)
    for cls, category in _CLASS_CATEGORY:
        if cls in classes:
            return category
    return DeviceCategory.UNKNOWN


# --- The source --------------------------------------------------------------------


class LinuxUsbDeviceSource:
    """A `SystemEventSource` for USB devices; see the module.

    With ``debug`` every arrival and removal is logged with its
    classification (``[devices] …``), never with a serial number.
    """

    name = "USB devices (kernel uevents)"

    def __init__(
        self,
        *,
        sysfs: Path = Path("/sys"),
        open_socket: Callable[[], socket.socket] = open_uevent_socket,
        debug: bool = False,
    ):
        self._sysfs = sysfs
        self._open_socket = open_socket
        self._debug = debug
        self._socket: socket.socket | None = None
        self._notifier: QSocketNotifier | None = None
        self._listener: SystemEventListener | None = None
        # devpath -> its arrival, for every non-hub device known to be present.
        self._present: dict[str, DeviceEvent] = {}
        # Added but not configured yet: announced at their bind.
        self._configuring: set[str] = set()

    def start(self, listener: SystemEventListener) -> bool:
        if self._socket is not None:
            return True
        try:
            self._socket = self._open_socket()
        except OSError:
            return False
        self._listener = listener
        self._present = self._present_devices()
        self._configuring.clear()
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
        self._configuring.clear()

    @property
    def watching(self) -> bool:
        return self._socket is not None

    def _present_devices(self) -> dict[str, DeviceEvent]:
        present = {}
        devices = self._sysfs / "bus" / "usb" / "devices"
        try:
            entries = list(devices.iterdir())
        except OSError:
            return present
        for entry in entries:
            if ":" in entry.name:
                continue  # an interface, not a device
            try:
                real = entry.resolve()
                devpath = "/" + str(real.relative_to(self._sysfs.resolve()))
            except (OSError, ValueError):
                continue
            event = self._arrival_from_sysfs(devpath)
            if event is not None:
                present[devpath] = event
        return present

    def on_readable(self, *_) -> None:
        """Drain every queued message; called by the notifier."""
        while self._socket is not None:
            try:
                message = self._socket.recv(65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as error:
                if error.errno == errno.ENOBUFS:
                    continue  # the kernel dropped messages; carry on with the next
                # Anything else will not go away by reading again: stop
                # watching rather than spin on a readable but broken socket.
                print(f"[notices] USB uevent socket failed ({error}); USB notices stop", file=sys.stderr)
                self.shutdown()
                return
            event = self.event_for(parse_uevent(message))
            if event is not None and self._listener is not None:
                self._listener(event)

    def event_for(self, fields: dict[str, str] | None) -> DeviceEvent | None:
        if not fields or fields.get("SUBSYSTEM") != "usb" or fields.get("DEVTYPE") != "usb_device":
            return None
        devpath, action = fields.get("DEVPATH", ""), fields.get("ACTION")
        if action == "add":
            # A device still known at this path left without us hearing it
            # (the kernel dropped the message): this is a new arrival.
            self._present.pop(devpath, None)
            device = self._device_path(devpath)
            if is_hub(device):
                return None
            self._configuring.add(devpath)
            if not is_configured(device):
                self._log(f"added {self._port(devpath)}; waiting for its configuration")
                return None
            return self._arrived(devpath)
        if action == "bind":
            if devpath not in self._configuring:
                return None  # announced already, a hub, or never added
            return self._arrived(devpath)
        if action == "remove":
            self._configuring.discard(devpath)
            arrival = self._present.pop(devpath, None)
            if arrival is None:
                return None  # a hub, never configured, or never known
            self._log(f"disconnected {self._port(devpath)}: {arrival.category} {arrival.name!r}")
            return replace(arrival, change=DeviceChange.DISCONNECTED)
        return None

    def _arrived(self, devpath: str) -> DeviceEvent | None:
        self._configuring.discard(devpath)
        event = self._arrival_from_sysfs(devpath, log=True)
        if event is not None:
            self._present[devpath] = event
        return event

    def _arrival_from_sysfs(self, devpath: str, *, log: bool = False) -> DeviceEvent | None:
        info = read_device(self._device_path(devpath))
        if info is None:
            return None
        category = classify_usb_device(info.interfaces, device_class=info.device_class, product=info.product)
        name = device_name(info.product, info.manufacturer)
        if log:
            interfaces = " ".join(str(interface) for interface in info.interfaces) or "none"
            self._log(
                f"connected {self._port(devpath)}: {category} {name!r} "
                f"({info.vendor_id or '?'}:{info.product_id or '?'}, interfaces {interfaces})"
            )
        return DeviceEvent(
            DeviceKind.USB, DeviceChange.CONNECTED, name, devpath,
            category=category, manufacturer=info.manufacturer or None,
        )

    def _device_path(self, devpath: str) -> Path:
        return self._sysfs / devpath.lstrip("/")

    @staticmethod
    def _port(devpath: str) -> str:
        return devpath.rsplit("/", 1)[-1]

    def _log(self, message: str) -> None:
        if self._debug:
            print(f"[devices] {message}", file=sys.stderr)
