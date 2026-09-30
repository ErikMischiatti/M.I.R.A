"""Deterministic camera doubles shared by the camera tests."""

from __future__ import annotations

import threading
import time

from mira.domain.camera import CameraFrame, CameraInfo, PixelFormat


def make_frame(
    sequence: int = 0,
    *,
    width: int = 4,
    height: int = 2,
    timestamp_s: float | None = None,
) -> CameraFrame:
    return CameraFrame(
        sequence=sequence,
        timestamp_s=float(sequence) if timestamp_s is None else timestamp_s,
        width=width,
        height=height,
        pixel_format=PixelFormat.BGR8,
        pixels=bytes([sequence % 256]) * (width * height * 3),
    )


class FakeCameraSource:
    """A scripted `CameraSource` that records how it was used.

    ``reads`` is consumed in order: a `CameraFrame` is delivered, None is a
    failed read, and an exception instance is raised. After the script, the
    source behaves like a live device (``then="frames"``: a new frame every
    millisecond), a dead one (``then="fail"``: every read fails), or a stalled
    one (``then="block"``: the read waits for ``unblock``, then delivers a frame).

    Setting ``hold`` to an unset `threading.Event` makes each read wait for it,
    which keeps a read in flight for as long as a test needs.
    """

    def __init__(
        self,
        reads=(),
        *,
        open_error: BaseException | None = None,
        info: CameraInfo | None = None,
        then: str = "frames",
    ):
        self.reads = list(reads)
        self.open_error = open_error
        self.info = info or CameraInfo(device="fake0", width=4, height=2, fps=30.0)
        self.then = then
        self.hold: threading.Event | None = None
        self.calls: list[str] = []
        self.threads: set[str] = set()
        self.read_started = threading.Event()
        self.blocked = threading.Event()
        self.unblock = threading.Event()
        self.closed = threading.Event()
        self.is_open = False
        self._next_sequence = 0

    def open(self):
        self._record("open")
        if self.open_error is not None:
            raise self.open_error
        self.is_open = True
        return self.info

    def read(self):
        self._record("read")
        self.read_started.set()
        if self.hold is not None:
            self.hold.wait(5.0)
        if self.reads:
            item = self.reads.pop(0)
            if isinstance(item, BaseException):
                raise item
            if item is not None:
                self._next_sequence = item.sequence + 1
            return item
        if self.then == "block":
            self.blocked.set()
            self.unblock.wait(5.0)
        elif self.then == "fail":
            time.sleep(0.001)
            return None
        else:
            time.sleep(0.001)
        frame = make_frame(self._next_sequence, timestamp_s=time.monotonic())
        self._next_sequence += 1
        return frame

    def close(self):
        self._record("close")
        self.is_open = False
        self.closed.set()

    def count(self, call: str) -> int:
        return self.calls.count(call)

    def _record(self, call: str) -> None:
        self.calls.append(call)
        self.threads.add(threading.current_thread().name)
