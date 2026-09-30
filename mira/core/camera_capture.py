"""Own a camera source and the thread that reads it.

A camera is a continuous producer with its own clock, so it gets one dedicated
capture thread rather than the `Scheduler` port: `Scheduler.submit` is for
short units of turn work whose results are delivered back on the serialized
context, and frames must never be marshalled onto the UI thread.

The capture thread is the only thread that touches the source. It opens the
device, reads until asked to stop or until the device keeps failing, and
releases the device on the way out, whatever happened. It never touches Qt,
the event bus or application state.

Frames form their own data plane, separate from the event bus. `EventBus` is a
synchronous fan-out for small semantic events on the serialized context;
pushing 30 raw images a second through it would run every subscriber on the
capture thread, or queue images on the UI thread. Instead the capture keeps
only the newest frame, and consumers pull: `latest_frame` never blocks, and
`wait_for_frame` lets a consumer thread sleep until something newer arrives.
A slow consumer therefore sees fewer frames rather than a growing backlog, and
no consumer code ever runs on the capture thread.
"""

from __future__ import annotations

import threading

from mira.domain.camera import CameraError, CameraFrame, CameraSource


class CameraCapture:
    """Start and stop capture from one `CameraSource`; None means disabled.

    `start` returns immediately; opening happens on the capture thread, so a
    slow or missing device never blocks the caller. `stop` waits up to
    ``join_timeout_s`` for an in-flight read to return; a read that outlasts it
    still releases the device when it returns, and no second reader starts in
    the meantime. `shutdown` stops and refuses any later start.
    """

    MAX_CONSECUTIVE_READ_FAILURES = 5
    THREAD_NAME = "mira-camera"

    def __init__(self, source: CameraSource | None, *, join_timeout_s: float = 2.0):
        self._source = source
        self._join_timeout_s = join_timeout_s
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._shut_down = False
        self._frames = threading.Condition()
        self._latest: CameraFrame | None = None

    @property
    def enabled(self) -> bool:
        return self._source is not None

    @property
    def running(self) -> bool:
        """True while the capture thread is alive, including while stopping."""

        thread = self._thread
        return thread is not None and thread.is_alive()

    def latest_frame(self) -> CameraFrame | None:
        """The newest frame of the current capture, or None; never blocks."""

        with self._frames:
            return self._latest

    def wait_for_frame(
        self, *, newer_than: int | None = None, timeout_s: float
    ) -> CameraFrame | None:
        """Wait for a frame whose sequence exceeds ``newer_than``.

        Returns the newest such frame, or None on timeout or once capture stops.
        Sequences restart when a capture restarts, so a consumer that outlives
        a stop should start again from ``newer_than=None``.
        """

        stop_requested = self._stop_requested

        def ready() -> bool:
            if stop_requested.is_set():
                return True
            latest = self._latest
            return latest is not None and (newer_than is None or latest.sequence > newer_than)

        with self._frames:
            self._frames.wait_for(ready, timeout=timeout_s)
            if stop_requested.is_set():
                return None
            latest = self._latest
            if latest is None or (newer_than is not None and latest.sequence <= newer_than):
                return None
            return latest

    def start(self) -> None:
        with self._lock:
            if self._source is None or self._shut_down or self.running:
                return
            self._stop_requested = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(self._source, self._stop_requested),
                name=self.THREAD_NAME,
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> bool:
        """Stop capturing; True once the device has been released."""

        with self._lock:
            thread = self._thread
            self._stop_requested.set()
        self._clear_frames()
        if thread is None:
            return True
        thread.join(self._join_timeout_s)
        if thread.is_alive():
            print(f"[Camera] capture still finishing a read after {self._join_timeout_s}s")
            return False
        with self._lock:
            if self._thread is thread:
                self._thread = None
        return True

    def shutdown(self) -> bool:
        """Stop for good; idempotent."""

        with self._lock:
            self._shut_down = True
        return self.stop()

    def _run(self, source: CameraSource, stop_requested: threading.Event) -> None:
        try:
            try:
                info = source.open()
            except CameraError as error:
                print(f"[Camera] unavailable: {error}")
                return
            except Exception as error:  # noqa: BLE001 - a broken source must not escape the thread
                print(f"[Camera] failed to open: {error!r}")
                return
            print(f"[Camera] active: {info.device} {info.width}x{info.height}")

            failures = 0
            while not stop_requested.is_set():
                try:
                    frame = source.read()
                except Exception:  # noqa: BLE001 - counted like any failed read
                    frame = None
                if frame is None:
                    failures += 1
                    if failures >= self.MAX_CONSECUTIVE_READ_FAILURES:
                        print(f"[Camera] failed: {failures} consecutive reads failed")
                        return
                    continue
                failures = 0
                self._publish(frame, stop_requested)
        finally:
            self._clear_frames()
            try:
                source.close()
            except Exception as error:  # noqa: BLE001 - release is best effort
                print(f"[Camera] error while releasing: {error!r}")

    def _publish(self, frame: CameraFrame, stop_requested: threading.Event) -> None:
        with self._frames:
            # A read that returns after stop was requested is not published:
            # stop clears the frame under this same lock after setting the flag.
            if stop_requested.is_set():
                return
            self._latest = frame
            self._frames.notify_all()

    def _clear_frames(self) -> None:
        with self._frames:
            self._latest = None
            self._frames.notify_all()
