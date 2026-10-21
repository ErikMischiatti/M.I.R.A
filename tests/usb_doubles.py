"""A fake kernel for `LinuxUsbDeviceSource`: a sysfs tree and a socket pair.

No USB device is needed. `make_device` lays out what sysfs shows for one
device (and, once configured, its interfaces); `uevent` builds the kernel's
message for it; the source reads both exactly as it reads the real ones.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable
from pathlib import Path

from mira.adapters.linux_usb import LinuxUsbDeviceSource

# Interface class triples of real devices, with their description string.
ADB = "ff/42/01 ADB Interface"
MTP = "06/01/01 MTP"
PTP = "06/01/01"
MASS_STORAGE = "08/06/50"
VIDEO_CONTROL = "0e/01/00"
VIDEO_STREAMING = "0e/02/00"
AUDIO_CONTROL = "01/01/00"
AUDIO_STREAMING = "01/02/00"
HID = "03/01/01"
VENDOR = "ff/ff/00"


def uevent(action: str, devpath: str, **fields) -> bytes:
    fields = {"ACTION": action, "DEVPATH": devpath, "SUBSYSTEM": "usb", "DEVTYPE": "usb_device", **fields}
    return f"{action}@{devpath}".encode() + b"\0" + b"\0".join(f"{k}={v}".encode() for k, v in fields.items())


def make_device(
    sysfs: Path,
    devpath: str,
    *,
    product: str = "",
    manufacturer: str = "",
    device_class: str = "00",
    vendor: str = "",
    product_id: str = "",
    serial: str = "",
    interfaces: Iterable[str] = (),
    configured: bool = True,
    listed: bool = True,
) -> Path:
    """One device directory; with ``configured`` its interfaces are there too."""

    device = sysfs / devpath.lstrip("/")
    device.mkdir(parents=True)
    for name, value in (
        ("product", product), ("manufacturer", manufacturer), ("bDeviceClass", device_class),
        ("idVendor", vendor), ("idProduct", product_id), ("serial", serial),
    ):
        if value:
            (device / name).write_text(value + "\n")
    (device / "bNumInterfaces").write_text("\n")
    if configured:
        configure(device, interfaces)
    if listed:
        bus = sysfs / "bus" / "usb" / "devices"
        bus.mkdir(parents=True, exist_ok=True)
        for entry in (device, *device.glob(f"{device.name}:*")):
            (bus / entry.name).symlink_to(entry)
    return device


def configure(device: Path, interfaces: Iterable[str]) -> None:
    """What the kernel does between a device's ``add`` and its ``bind``.

    Each interface is ``"cc/ss/pp"``, optionally followed by its description.
    """

    interfaces = list(interfaces)
    for index, spec in enumerate(interfaces):
        triple, _, description = spec.partition(" ")
        path = device / f"{device.name}:1.{index}"
        path.mkdir()
        for name, value in zip(("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"), triple.split("/")):
            (path / name).write_text(value + "\n")
        if description:
            (path / "interface").write_text(description + "\n")
    # The kernel right-aligns it in two columns, as in " 2".
    (device / "bNumInterfaces").write_text(f"{len(interfaces):2d}\n")


class FakeKernel:
    """The kernel's end of the uevent socket, and the sysfs it describes."""

    def __init__(self, sysfs: Path):
        self.sysfs = sysfs
        (sysfs / "bus" / "usb" / "devices").mkdir(parents=True, exist_ok=True)
        self.end, self.ours = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.ours.setblocking(False)

    def source(self, **options) -> LinuxUsbDeviceSource:
        return LinuxUsbDeviceSource(sysfs=self.sysfs, open_socket=lambda: self.ours, **options)

    def send(self, *messages: bytes) -> None:
        for message in messages:
            self.end.send(message)

    def close(self) -> None:
        self.end.close()
        self.ours.close()
