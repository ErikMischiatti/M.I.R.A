"""Audio outputs and inputs appearing, leaving and becoming the default.

PipeWire (the audio server of current Ubuntu/GNOME) is observed through its
own command-line tool: ``pw-dump --monitor`` prints the whole object graph
as a JSON array once, then one JSON array of changed objects per change, for
as long as it runs. It is one long-lived child process (`QProcess`), read
when output arrives, so nothing polls. No Python binding and no PulseAudio
tools are needed; without ``pw-dump`` the source is unavailable.

`PipeWireTracker` is the pure part: it keeps the audio nodes
(``media.class`` ``Audio/Sink`` and ``Audio/Source``) and the effective
defaults (``default.audio.sink``/``default.audio.source`` in the ``default``
metadata) and turns each change into `DeviceEvent` values. The first array
is the baseline and announces nothing.

The monitor is supervised. If ``pw-dump`` exits, or fails to start, while
MIRA is not shutting down, it is restarted after a delay that doubles from
`RESTART_INITIAL_MS` up to `RESTART_MAX_MS`, and goes back to the initial
delay once a monitor has run for `STABLE_MS`; so a missing audio server is
retried quietly and never in a tight loop. There is only ever one child. The
first dump of a restarted monitor is *reconciled* against what was known,
by node name (PipeWire may renumber its objects): only devices that really
appeared, left or became the default while the monitor was down are
reported, never every device that is still there. `shutdown` marks the stop
as intentional before ending the child, so it is never restarted. Exits,
retries and recoveries are logged once each; device changes are not.

This is device *topology* only. It carries no samples and no level: a
microphone being present says nothing about what it hears.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QProcess, QTimer

from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind, SystemEventListener

_NODE = "PipeWire:Interface:Node"
_METADATA = "PipeWire:Interface:Metadata"
_KINDS = {"Audio/Sink": DeviceKind.AUDIO_OUTPUT, "Audio/Source": DeviceKind.AUDIO_INPUT}
_DEFAULT_KEYS = {"default.audio.sink": DeviceKind.AUDIO_OUTPUT, "default.audio.source": DeviceKind.AUDIO_INPUT}

# Beyond this much undecodable output something is wrong; start afresh.
MAX_BUFFER_CHARS = 8 * 1024 * 1024

RESTART_INITIAL_MS = 1000
RESTART_MAX_MS = 30000
STABLE_MS = 60000


class PipeWireTracker:
    """Audio nodes and defaults, fed with ``pw-dump`` object arrays."""

    def __init__(self):
        self._baseline = True
        self._reconcile = False
        # PipeWire object id -> (kind, node name, description)
        self._nodes: dict[int, tuple[DeviceKind, str, str]] = {}
        self._defaults: dict[DeviceKind, str] = {}

    def rebase(self) -> None:
        """The next dump comes from a new monitor: reconcile, do not replay."""
        self._reconcile = True

    def feed(self, objects: list) -> list[DeviceEvent]:
        if self._reconcile:
            return self._reconciled(objects)
        events: list[DeviceEvent] = []
        metadata = []
        for entry in objects:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == _METADATA:
                metadata.append(entry)
            else:
                events += self._node(entry)
        # Nodes first, so a default naming a node of this same batch finds it.
        for entry in metadata:
            events += self._metadata(entry)
        if self._baseline:
            self._baseline = False
            return []
        return events

    def _node(self, entry: dict) -> list[DeviceEvent]:
        object_id = entry.get("id")
        info = entry.get("info")
        if info is None:
            known = self._nodes.pop(object_id, None)
            if known is None:
                return []
            kind, key, name = known
            return [DeviceEvent(kind, DeviceChange.DISCONNECTED, name, key)]
        if entry.get("type") != _NODE or object_id in self._nodes:
            return []
        props = info.get("props") or {}
        kind = _KINDS.get(props.get("media.class"))
        key = props.get("node.name")
        if kind is None or not key:
            return []
        name = props.get("node.description") or props.get("node.nick") or key
        self._nodes[object_id] = (kind, key, name)
        return [DeviceEvent(kind, DeviceChange.CONNECTED, name, key)]

    def _metadata(self, entry: dict) -> list[DeviceEvent]:
        if (entry.get("props") or {}).get("metadata.name") != "default":
            return []
        events = []
        for item in entry.get("metadata") or []:
            kind = _DEFAULT_KEYS.get(item.get("key"))
            value = item.get("value")
            key = value.get("name") if isinstance(value, dict) else None
            if kind is None or not key or self._defaults.get(kind) == key:
                continue
            self._defaults[kind] = key
            events.append(DeviceEvent(kind, DeviceChange.DEFAULT_CHANGED, self._name_of(key), key))
        return events

    def _reconciled(self, objects: list) -> list[DeviceEvent]:
        """A full dump from a restarted monitor, against what was known."""
        self._reconcile = False
        before = {key: (kind, name) for kind, key, name in self._nodes.values()}
        defaults_before = dict(self._defaults)
        self._nodes, self._defaults, self._baseline = {}, {}, True
        self.feed(objects)  # rebuilt silently, as a baseline
        after = {key: (kind, name) for kind, key, name in self._nodes.values()}
        events = [DeviceEvent(kind, DeviceChange.DISCONNECTED, name, key)
                  for key, (kind, name) in before.items() if key not in after]
        events += [DeviceEvent(kind, DeviceChange.CONNECTED, name, key)
                   for key, (kind, name) in after.items() if key not in before]
        for kind, key in self._defaults.items():
            if defaults_before.get(kind) != key and key in after and key in before:
                events.append(DeviceEvent(kind, DeviceChange.DEFAULT_CHANGED, self._name_of(key), key))
        return events

    def _name_of(self, key: str) -> str:
        for _, node_key, name in self._nodes.values():
            if node_key == key:
                return name
        return key


class JsonArrayStream:
    """Splits concatenated JSON values arriving in arbitrary chunks."""

    def __init__(self):
        self._buffer = ""
        self._decoder = json.JSONDecoder()

    def feed(self, text: str) -> list:
        self._buffer += text
        values = []
        position = 0
        while True:
            while position < len(self._buffer) and self._buffer[position].isspace():
                position += 1
            if position >= len(self._buffer):
                break
            try:
                value, position = self._decoder.raw_decode(self._buffer, position)
            except json.JSONDecodeError:
                break  # incomplete: wait for more
            values.append(value)
        self._buffer = self._buffer[position:]
        if len(self._buffer) > MAX_BUFFER_CHARS:
            self._buffer = ""
        return values


class _QtTimer:
    def __init__(self, delay_ms: int, callback: Callable[[], None]):
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(callback)
        self._timer.start(delay_ms)

    def cancel(self) -> None:
        self._timer.stop()


class PipeWireAudioSource:
    """A `SystemEventSource` for audio devices; see the module.

    ``call_later(ms, callback)`` schedules a restart and returns a handle with
    ``cancel()`` (a Qt single-shot timer by default); ``clock`` measures how
    long a monitor ran, in seconds. Both are seams for tests.
    """

    name = "audio devices (PipeWire)"
    PROGRAM = "pw-dump"
    ARGUMENTS = ["--monitor", "--no-colors"]

    def __init__(self, *, which=shutil.which, call_later=_QtTimer, clock=time.monotonic):
        self._which = which
        self._call_later = call_later
        self._clock = clock
        self._program: str | None = None
        self._process: QProcess | None = None
        self._listener: SystemEventListener | None = None
        self._tracker = PipeWireTracker()
        self._stream = JsonArrayStream()
        self._running = False
        self._retry = None
        self._delay_ms = RESTART_INITIAL_MS
        self._started_at = 0.0
        self._recovering = False
        # Identifies the one scheduled restart, so a timer that fires late or
        # twice can never spawn a second child.
        self._generation = 0
        self.restarts = 0

    def start(self, listener: SystemEventListener) -> bool:
        if self._running:
            return True
        program = self._which(self.PROGRAM)
        if program is None:
            return False
        self._program, self._listener, self._running = program, listener, True
        self._tracker, self._stream = PipeWireTracker(), JsonArrayStream()
        self._delay_ms = RESTART_INITIAL_MS
        self._spawn()
        return True

    def shutdown(self) -> None:
        # Intentional: from here on an exit is expected and never restarted.
        self._running = False
        self._listener = None
        self._generation += 1
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None
        self._end_process()

    @property
    def process(self) -> QProcess | None:
        return self._process

    @property
    def restart_pending(self) -> bool:
        return self._retry is not None

    @property
    def next_delay_ms(self) -> int:
        return self._delay_ms

    def feed_text(self, text: str) -> None:
        """Handle a chunk of ``pw-dump`` output; the process's reader calls it."""
        for value in self._stream.feed(text):
            if not isinstance(value, list):
                continue
            if self._recovering:
                self._recovering = False
                print("[notices] pw-dump recovered; audio device notices resume")
            for event in self._tracker.feed(value):
                if self._listener is not None:
                    self._listener(event)

    # --- The child process ----------------------------------------------------------

    def _spawn(self) -> None:
        process = QProcess()
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.setStandardErrorFile(QProcess.nullDevice())
        process.readyReadStandardOutput.connect(self._on_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        self._started_at = self._clock()
        process.start(self._program, self.ARGUMENTS)

    def _end_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        for signal, slot in ((process.readyReadStandardOutput, self._on_output),
                             (process.finished, self._on_finished),
                             (process.errorOccurred, self._on_error)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if process.state() is not QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(1000):
                process.kill()
                process.waitForFinished(1000)
        process.deleteLater()

    def _on_output(self) -> None:
        if self._process is not None:
            self.feed_text(bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace"))

    def _on_error(self, error) -> None:
        # A child that never started emits no `finished`.
        if error is QProcess.ProcessError.FailedToStart:
            self._lost("could not start")

    def _on_finished(self, exit_code: int = 0, *_):
        self._lost(f"exited ({exit_code})")

    def _lost(self, how: str) -> None:
        if not self._running or self._retry is not None:
            return  # shutting down, or already rescheduled
        if self._process is not None:
            self._on_output()  # whatever it printed last still counts
        self._end_process()
        if (self._clock() - self._started_at) * 1000 >= STABLE_MS:
            self._delay_ms = RESTART_INITIAL_MS
        delay = self._delay_ms
        self._delay_ms = min(self._delay_ms * 2, RESTART_MAX_MS)
        print(f"[notices] pw-dump {how}; audio device monitoring paused, retrying in {delay / 1000:g} s",
              file=sys.stderr)
        self._generation += 1
        generation = self._generation
        self._retry = self._call_later(delay, lambda: self._restart(generation))

    def _restart(self, generation: int) -> None:
        if generation != self._generation or not self._running or self._process is not None:
            return
        self._retry = None
        self.restarts += 1
        self._recovering = True
        self._tracker.rebase()
        self._stream = JsonArrayStream()
        self._spawn()
