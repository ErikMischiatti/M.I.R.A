"""USB devices arriving and leaving, from Linux kernel uevents.

The kernel announces every device it adds or removes on a netlink socket
(`NETLINK_KOBJECT_UEVENT`, multicast group 1), readable without privileges.
The socket is watched by the Qt event loop (`QSocketNotifier`), so nothing
polls and no thread is started: a message is read only when one arrives, and
at most `MAX_MESSAGES_PER_WAKE` per wake-up, so a burst (a dock with many
devices) never holds the UI thread; the notifier fires again for the rest.

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
announces nothing. Devices present at start are read once so their removal
can be named, and announce nothing themselves; one still being configured
at start is announced at its ``bind``, as it is arriving.

**What is read.** From the device's sysfs directory: ``product``,
``manufacturer``, ``idVendor``/``idProduct``, ``bDeviceClass`` and, per
interface, its class triple and description string. Never ``serial``: no
persistent hardware identifier reaches an event or a log line. Everything is
remembered on arrival, because the attributes are gone by the time the
removal arrives. An event's ``identity`` is the vendor id with the
manufacturer and product strings — the same for a phone in every USB mode,
unlike its product id — so the producer can tell a phone re-enumerating from
another device plugged into the same port.

**Classification** (`classify_usb_device`) uses only what the device
declares, first match wins, and prefers UNKNOWN to a guess:

    SMARTPHONE  Apple's vendor id with a product named "iPhone"; or a known
                phone maker's vendor id (`PHONE_VENDORS`) together with a
                phone function — MTP (an interface named "MTP"), PTP (still
                image class 06), Android debugging/fastboot (ff/42/01,
                ff/42/03)
    STORAGE     mass storage (08)
    CAMERA      video (0e); still image (06) only from a known camera
                maker (`CAMERA_VENDORS`)
    AUDIO       audio (01)
    INPUT       human interface device (03)
    UNKNOWN     anything else

so a phone in PTP mode is a smartphone, not a camera; MTP or ADB from any
other vendor (a media player, a watch, a development board) is unknown; and
a phone in charge-only mode that exposes no phone function is unknown, named
by its own strings. A webcam with a microphone is a camera and a headset
with volume buttons is an audio device.

**Failure and recovery.** A socket that cannot be opened at start makes the
source unavailable. Once running, the socket is supervised like the PipeWire
monitor: a persistent error closes it and it is reopened after a delay that
doubles from `RETRY_INITIAL_MS` to `RETRY_MAX_MS` (back to the initial delay
after `STABLE_MS` of stable running). Whenever messages may have been lost —
the kernel dropped some (``ENOBUFS``) or the socket was reopened — the known
devices are *resynchronised* with sysfs: devices that left are reported as
disconnected, devices that arrived as connected, and devices still there
say nothing. An exception while handling one message is logged and drops
that message only. `shutdown` closes the socket, cancels any retry and stops
delivery, even from inside a listener.

No libudev or pyudev: the kernel's own message format is a few
NUL-separated ``KEY=value`` strings.
"""

from __future__ import annotations

import errno
import socket
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSocketNotifier, QTimer

from mira.domain.devices import DeviceCategory
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind, SystemEventListener

NETLINK_KOBJECT_UEVENT = 15
KERNEL_GROUP = 1
HUB_CLASS = 0x09

# Asked for, not guaranteed: the kernel caps it at net.core.rmem_max.
RECEIVE_BUFFER_BYTES = 1 << 20
MAX_MESSAGES_PER_WAKE = 64
RETRY_INITIAL_MS = 1000
RETRY_MAX_MS = 30000
STABLE_MS = 60000

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
ADB_PROTOCOL = 0x01
# Apple's device multiplexer (usbmuxd talks to an iPhone through it).
APPLE_MUX = (VENDOR_CLASS, 0xFE, 0x02)

# USB-IF vendor ids (as sysfs writes them) of makers whose devices with a
# phone function are phones or tablets. Deliberately short: contract
# manufacturers and chipset vendors (Qualcomm, MediaTek), whose ids appear on
# all kinds of boards, are left out. Other phones are UNKNOWN, by name.
PHONE_VENDORS = frozenset({
    "18d1",  # Google
    "04e8",  # Samsung
    "2717",  # Xiaomi
    "2a70",  # OnePlus
    "22b8",  # Motorola
    "12d1",  # Huawei
    "22d9",  # OPPO (and realme)
    "0fce",  # Sony Mobile
    "1004",  # LG
    "0bb4",  # HTC
    "19d2",  # ZTE
    "1bbb",  # TCL / Alcatel
})
APPLE_VENDOR = "05ac"
# Makers of cameras that speak PTP over USB.
CAMERA_VENDORS = frozenset({
    "04a9",  # Canon
    "04b0",  # Nikon
    "04cb",  # Fujifilm
    "07b4",  # Olympus / OM System
    "04da",  # Panasonic
    "054c",  # Sony
    "2672",  # GoPro
    "1a98",  # Leica
    "25fb",  # Pentax (Ricoh)
    "05ca",  # Ricoh
})

# Per interface class, first match wins (see the module). Still image is
# decided separately: alone it does not say "camera".
_CLASS_CATEGORY = (
    (MASS_STORAGE_CLASS, DeviceCategory.STORAGE),
    (VIDEO_CLASS, DeviceCategory.CAMERA),
    (AUDIO_CLASS, DeviceCategory.AUDIO),
    (HID_CLASS, DeviceCategory.INPUT),
)
# Device-level classes that say "look at the interfaces" rather than what
# the device is: per interface, miscellaneous/composite, vendor specific.
_UNINFORMATIVE_DEVICE_CLASSES = frozenset({0x00, 0xEF, VENDOR_CLASS})


def open_uevent_socket() -> socket.socket:
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER_BYTES)
        sock.bind((0, KERNEL_GROUP))
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
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
        vendor_id=_read_attribute(device, "idVendor").lower(),
        product_id=_read_attribute(device, "idProduct").lower(),
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


def device_identity(info: UsbDeviceInfo) -> str | None:
    """What stays the same when this device re-enumerates; never the serial."""
    return f"usb:{info.vendor_id}:{info.manufacturer}:{info.product}" if info.vendor_id else None


def _is_phone_function(interface: UsbInterface) -> bool:
    return (
        interface.description.strip().upper() == "MTP"
        or interface.cls == STILL_IMAGE_CLASS
        or (interface.cls == VENDOR_CLASS and interface.subclass == ANDROID_SUBCLASS
            and interface.protocol in ANDROID_PROTOCOLS)
    )


def device_services(interfaces: Iterable[UsbInterface]) -> frozenset[str]:
    """What the device offers to talk to, as `DeviceEvent.services` names it."""
    services = set()
    for interface in interfaces:
        mtp = interface.description.strip().upper() == "MTP"
        if mtp:
            services.add("mtp")
        elif interface.cls == STILL_IMAGE_CLASS:
            services.add("ptp")
        if (interface.cls, interface.subclass, interface.protocol) == (VENDOR_CLASS, ANDROID_SUBCLASS, ADB_PROTOCOL):
            services.add("adb")
        if (interface.cls, interface.subclass, interface.protocol) == APPLE_MUX:
            services.add("usbmux")
    return frozenset(services)


def classify_usb_device(
    interfaces: Iterable[UsbInterface],
    *,
    device_class: int | None = None,
    product: str = "",
    vendor_id: str = "",
) -> DeviceCategory:
    """The category a device's own descriptors support; see the module."""

    interfaces = tuple(interfaces)
    vendor = vendor_id.strip().lower()
    if vendor == APPLE_VENDOR and product.strip().lower().startswith("iphone"):
        return DeviceCategory.SMARTPHONE
    if vendor in PHONE_VENDORS and any(_is_phone_function(interface) for interface in interfaces):
        return DeviceCategory.SMARTPHONE
    classes = {interface.cls for interface in interfaces}
    if device_class is not None and device_class not in _UNINFORMATIVE_DEVICE_CLASSES:
        classes.add(device_class)
    for cls, category in _CLASS_CATEGORY:
        if cls in classes:
            return category
    if STILL_IMAGE_CLASS in classes and vendor in CAMERA_VENDORS:
        return DeviceCategory.CAMERA
    return DeviceCategory.UNKNOWN


# --- The source --------------------------------------------------------------------


class _QtTimer:
    """A cancellable single-shot timer on the Qt loop (the default retry timer)."""

    def __init__(self, delay_ms: int, callback: Callable[[], None]):
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(callback)
        self._timer.start(delay_ms)

    def cancel(self) -> None:
        self._timer.stop()


class LinuxUsbDeviceSource:
    """A `SystemEventSource` for USB devices; see the module.

    With ``debug`` every arrival and removal is logged with its
    classification (``[devices] …``), never with a serial number.
    ``call_later(ms, callback)`` schedules a reopen and returns a handle with
    ``cancel()``; ``clock`` measures how long a socket ran, in seconds. Both
    are seams for tests, as is ``open_socket``.
    """

    name = "USB devices (kernel uevents)"

    def __init__(
        self,
        *,
        sysfs: Path = Path("/sys"),
        open_socket: Callable[[], socket.socket] = open_uevent_socket,
        debug: bool = False,
        call_later=_QtTimer,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._sysfs = sysfs
        self._open_socket = open_socket
        self._debug = debug
        self._call_later = call_later
        self._clock = clock
        self._socket: socket.socket | None = None
        self._notifier: QSocketNotifier | None = None
        self._listener: SystemEventListener | None = None
        self._running = False
        # devpath -> its arrival, for every non-hub device known to be present.
        self._present: dict[str, DeviceEvent] = {}
        # Added but not configured yet: announced at their bind.
        self._configuring: set[str] = set()
        self._retry = None
        self._delay_ms = RETRY_INITIAL_MS
        self._opened_at = 0.0
        # Identifies the one scheduled reopen, so a timer that fires late or
        # twice can never open a second socket.
        self._generation = 0
        self.restarts = 0

    def start(self, listener: SystemEventListener) -> bool:
        if self._running:
            return True
        try:
            sock = self._open_socket()
        except OSError:
            return False
        self._running, self._listener = True, listener
        self._delay_ms = RETRY_INITIAL_MS
        # Open first, then look: a device arriving in between is in both, and
        # its `add` simply finds it already known.
        self._attach(sock)
        self._present, self._configuring = self._scan()
        for devpath, arrival in self._present.items():
            self._log(f"present at start {self._port(devpath)}: {arrival.category} {arrival.name!r}")
        for devpath in self._configuring:
            self._log(f"configuring at start {self._port(devpath)}; announced at its bind")
        return True

    def shutdown(self) -> None:
        self._running = False
        self._listener = None
        self._generation += 1
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None
        self._detach()
        self._configuring.clear()

    @property
    def watching(self) -> bool:
        return self._socket is not None

    # --- The socket ----------------------------------------------------------------

    def _attach(self, sock: socket.socket) -> None:
        self._socket = sock
        self._opened_at = self._clock()
        self._notifier = QSocketNotifier(sock.fileno(), QSocketNotifier.Type.Read)
        self._notifier.activated.connect(self.on_readable)

    def _detach(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.activated.disconnect(self.on_readable)
            self._notifier.deleteLater()
            self._notifier = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def on_readable(self, *_) -> None:
        """Read what is queued, up to `MAX_MESSAGES_PER_WAKE`; called by the notifier."""
        for _ in range(MAX_MESSAGES_PER_WAKE):
            if self._socket is None:
                return  # shut down or lost, possibly by a listener
            try:
                message = self._socket.recv(65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as error:
                if error.errno == errno.ENOBUFS:
                    # The kernel dropped messages: what they said is in sysfs.
                    print("[notices] USB uevents were dropped; resynchronising with sysfs", file=sys.stderr)
                    self.resync()
                    continue
                # Anything else will not go away by reading again: reopen
                # later rather than spin on a readable but broken socket.
                self._lost(error)
                return
            try:
                event = self.event_for(parse_uevent(message))
            except Exception as error:  # noqa: BLE001 - one bad message must not stop watching
                print(f"[notices] USB uevent ignored ({type(error).__name__}: {error})", file=sys.stderr)
                continue
            self._deliver(event)

    def _deliver(self, event: DeviceEvent | None) -> None:
        if event is None or self._listener is None:
            return
        try:
            self._listener(event)
        except Exception as error:  # noqa: BLE001 - a failing consumer must not stop watching
            print(f"[notices] USB event not delivered ({type(error).__name__}: {error})", file=sys.stderr)

    def _lost(self, error: OSError) -> None:
        self._detach()
        if not self._running or self._retry is not None:
            return
        if (self._clock() - self._opened_at) * 1000 >= STABLE_MS:
            self._delay_ms = RETRY_INITIAL_MS
        delay = self._delay_ms
        self._delay_ms = min(self._delay_ms * 2, RETRY_MAX_MS)
        print(f"[notices] USB uevent socket failed ({error}); USB monitoring paused, retrying in {delay / 1000:g} s",
              file=sys.stderr)
        self._generation += 1
        generation = self._generation
        self._retry = self._call_later(delay, lambda: self._reopen(generation))

    def _reopen(self, generation: int) -> None:
        if generation != self._generation or not self._running or self._socket is not None:
            return
        self._retry = None
        try:
            sock = self._open_socket()
        except OSError as error:
            self._opened_at = self._clock()  # it did not run: keep backing off
            self._lost(error)
            return
        self.restarts += 1
        self._attach(sock)
        print("[notices] USB monitoring resumed", file=sys.stderr)
        self.resync()

    # --- Devices ---------------------------------------------------------------------

    def _scan(self) -> tuple[dict[str, DeviceEvent], set[str]]:
        """The devices sysfs lists now: configured ones, and ones still configuring."""
        present: dict[str, DeviceEvent] = {}
        configuring: set[str] = set()
        devices = self._sysfs / "bus" / "usb" / "devices"
        try:
            entries = list(devices.iterdir())
        except OSError:
            return present, configuring
        for entry in entries:
            if ":" in entry.name:
                continue  # an interface, not a device
            try:
                real = entry.resolve()
                devpath = "/" + str(real.relative_to(self._sysfs.resolve()))
            except (OSError, ValueError):
                continue
            if is_hub(real):
                continue
            if not is_configured(real):
                configuring.add(devpath)
                continue
            event = self._arrival_from_sysfs(devpath)
            if event is not None:
                present[devpath] = event
        return present, configuring

    def resync(self) -> None:
        """Report what changed while messages may have been lost; see the module."""
        present, configuring = self._scan()
        events = []
        for devpath, known in self._present.items():
            now = present.get(devpath)
            if now is None or now.identity != known.identity:
                events.append(replace(known, change=DeviceChange.DISCONNECTED))
        for devpath, now in present.items():
            known = self._present.get(devpath)
            if known is None or known.identity != now.identity:
                events.append(now)
        self._present, self._configuring = present, configuring
        self._log(f"resynchronised: {len(events)} change(s), {len(configuring)} configuring")
        for event in events:
            self._deliver(event)

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
        category = classify_usb_device(
            info.interfaces, device_class=info.device_class, product=info.product, vendor_id=info.vendor_id,
        )
        name = device_name(info.product, info.manufacturer)
        if log:
            interfaces = " ".join(str(interface) for interface in info.interfaces) or "none"
            self._log(
                f"connected {self._port(devpath)}: {category} {name!r} "
                f"({info.vendor_id or '?'}:{info.product_id or '?'}, interfaces {interfaces})"
            )
        return DeviceEvent(
            DeviceKind.USB, DeviceChange.CONNECTED, name, devpath,
            category=category, manufacturer=info.manufacturer or None, identity=device_identity(info),
            services=device_services(info.interfaces),
        )

    def _device_path(self, devpath: str) -> Path:
        return self._sysfs / devpath.lstrip("/")

    @staticmethod
    def _port(devpath: str) -> str:
        return devpath.rsplit("/", 1)[-1]

    def _log(self, message: str) -> None:
        if self._debug:
            print(f"[devices] {message}", file=sys.stderr)
