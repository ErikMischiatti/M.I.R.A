"""Own a camera source and the thread that reads it.

A camera is a continuous producer with its own clock, so it gets one dedicated
capture thread rather than the `Scheduler` port: `Scheduler.submit` is for
short units of turn work whose results are delivered back on the serialized
context, and frames must never be marshalled onto the UI thread.

The capture thread is the only thread that touches the source. It opens the
device, reads until asked to stop or until the device keeps failing, and
releases the device on the way out, whatever happened. It never touches Qt,
the event bus or application state.
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

    @property
    def enabled(self) -> bool:
        return self._source is not None

    @property
    def running(self) -> bool:
        """True while the capture thread is alive, including while stopping."""

        thread = self._thread
        return thread is not None and thread.is_alive()

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
                self._deliver(frame)
        finally:
            try:
                source.close()
            except Exception as error:  # noqa: BLE001 - release is best effort
                print(f"[Camera] error while releasing: {error!r}")

    def _deliver(self, frame: CameraFrame) -> None:
        """Hand a captured frame to consumers; frames are dropped for now."""
