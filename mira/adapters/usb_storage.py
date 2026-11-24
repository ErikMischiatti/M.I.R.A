"""A USB storage device's capacity, from sysfs alone.

When the kernel has probed a USB mass-storage device, its disk appears in
sysfs below the USB device itself:

    /sys/devices/…/usb3/3-2/3-2:1.0/host6/target6:0:0/6:0:0:0/block/sdb/size

``size`` is the medium's size in 512-byte sectors, whatever its own block
size. That one number is all this provider reads: nothing is mounted,
opened or listed, so no file, file name or label on the device is ever
seen. The disk is found by following the USB device's own sysfs tree, so it
is always the disk of *this* device.

The disk appears a moment after the USB device is configured, so a query
that finds none yet asks to be retried. A card reader with no card reports
size 0, which is no capacity, not "0 B"; a reader with several slots and
cards (several disks) reports nothing rather than picking one.
"""

from __future__ import annotations

from pathlib import Path

from mira.domain.device_info import DeviceInfo, InfoResult
from mira.domain.devices import DeviceCategory
from mira.domain.system_events import DeviceEvent, DeviceKind

SECTOR_BYTES = 512

NOT_YET = "the storage device has no disk yet"
NO_MEDIUM = "the storage device reports no medium"
SEVERAL = "the storage device has several disks"


class UsbStorageInfoProvider:
    """A `DeviceInfoProvider` for USB mass storage; see the module."""

    name = "usb storage"

    def __init__(self, *, sysfs: Path = Path("/sys")):
        self._sysfs = sysfs

    def cancel(self) -> None:
        """Nothing to stop: a few small sysfs reads end at once."""

    def supports(self, device: DeviceEvent) -> bool:
        return device.kind is DeviceKind.USB and device.category is DeviceCategory.STORAGE

    def query(self, device: DeviceEvent) -> InfoResult:
        root = self._sysfs / device.key.lstrip("/")
        try:
            sizes = [self._sectors(path) for path in root.glob("*/host*/target*/*/block/*/size")]
        except OSError:
            sizes = []
        if not sizes:
            return InfoResult(problem=NOT_YET, retry=True)
        media = [size for size in sizes if size]
        if not media:
            return InfoResult(problem=NO_MEDIUM)
        if len(media) > 1:
            return InfoResult(problem=SEVERAL)
        return InfoResult(DeviceInfo(capacity_bytes=media[0] * SECTOR_BYTES))

    @staticmethod
    def _sectors(path: Path) -> int:
        try:
            return max(int(path.read_text().strip()), 0)
        except (OSError, ValueError):
            return 0
