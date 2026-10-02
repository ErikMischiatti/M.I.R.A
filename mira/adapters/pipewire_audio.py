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

This is device *topology* only. It carries no samples and no level: a
microphone being present says nothing about what it hears.
"""

from __future__ import annotations

import json
import shutil
import sys

from PySide6.QtCore import QProcess

from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind, SystemEventListener

_NODE = "PipeWire:Interface:Node"
_METADATA = "PipeWire:Interface:Metadata"
_KINDS = {"Audio/Sink": DeviceKind.AUDIO_OUTPUT, "Audio/Source": DeviceKind.AUDIO_INPUT}
_DEFAULT_KEYS = {"default.audio.sink": DeviceKind.AUDIO_OUTPUT, "default.audio.source": DeviceKind.AUDIO_INPUT}

# Beyond this much undecodable output something is wrong; start afresh.
MAX_BUFFER_CHARS = 8 * 1024 * 1024


class PipeWireTracker:
    """Audio nodes and defaults, fed with ``pw-dump`` object arrays."""

    def __init__(self):
        self._baseline = True
        # PipeWire object id -> (kind, node name, description)
        self._nodes: dict[int, tuple[DeviceKind, str, str]] = {}
        self._defaults: dict[DeviceKind, str] = {}

    def feed(self, objects: list) -> list[DeviceEvent]:
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


class PipeWireAudioSource:
    """A `SystemEventSource` for audio devices; see the module."""

    name = "audio devices (PipeWire)"
    PROGRAM = "pw-dump"
    ARGUMENTS = ["--monitor", "--no-colors"]

    def __init__(self, *, which=shutil.which):
        self._which = which
        self._process: QProcess | None = None
        self._listener: SystemEventListener | None = None
        self._tracker = PipeWireTracker()
        self._stream = JsonArrayStream()

    def start(self, listener: SystemEventListener) -> bool:
        if self._process is not None:
            return True
        program = self._which(self.PROGRAM)
        if program is None:
            return False
        self._listener = listener
        self._tracker, self._stream = PipeWireTracker(), JsonArrayStream()
        self._process = QProcess()
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.setStandardErrorFile(QProcess.nullDevice())
        self._process.readyReadStandardOutput.connect(self._on_output)
        self._process.finished.connect(self._on_finished)
        self._process.start(program, self.ARGUMENTS)
        return True

    def shutdown(self) -> None:
        process, self._process = self._process, None
        self._listener = None
        if process is None:
            return
        process.readyReadStandardOutput.disconnect(self._on_output)
        process.finished.disconnect(self._on_finished)
        if process.state() is not QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(1000):
                process.kill()
                process.waitForFinished(1000)
        process.deleteLater()

    @property
    def process(self) -> QProcess | None:
        return self._process

    def feed_text(self, text: str) -> None:
        """Handle a chunk of ``pw-dump`` output; the process's reader calls it."""
        for value in self._stream.feed(text):
            if isinstance(value, list):
                for event in self._tracker.feed(value):
                    if self._listener is not None:
                        self._listener(event)

    def _on_output(self) -> None:
        if self._process is not None:
            self.feed_text(bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace"))

    def _on_finished(self, *_):
        # The audio server restarted or the tool died: report, do not respawn.
        print("[notices] pw-dump ended; audio device notices stop", file=sys.stderr)
