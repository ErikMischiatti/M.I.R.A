"""Platform adapters report semantic system events, against fakes only.

No USB device, audio server or NetworkManager is needed: a temporary sysfs
tree and a socket pair stand in for the kernel, a script for ``pw-dump``, a
fake bus for D-Bus. The real facilities are exercised in the manual smoke
test.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

from mira.adapters.linux_usb import LinuxUsbDeviceSource, describe_device, parse_uevent
from mira.adapters.networkmanager import NetworkManagerConnectivitySource, connectivity_from_nm
from mira.adapters.pipewire_audio import JsonArrayStream, PipeWireAudioSource, PipeWireTracker
from mira.application.composition import build_application, desktop_system_event_sources
from mira.domain.scheduler import ManualScheduler
from mira.domain.system_events import Connectivity, ConnectivityEvent, DeviceChange, DeviceEvent, DeviceKind


@pytest.fixture(scope="module", autouse=True)
def application():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def spin(milliseconds: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


# --- USB: kernel uevents ----------------------------------------------------------


def uevent(action: str, devpath: str, **fields) -> bytes:
    fields = {"ACTION": action, "DEVPATH": devpath, "SUBSYSTEM": "usb", "DEVTYPE": "usb_device", **fields}
    return f"{action}@{devpath}".encode() + b"\0" + b"\0".join(f"{k}={v}".encode() for k, v in fields.items())


def make_device(sysfs, devpath, *, product="", manufacturer="", device_class="00", listed=True):
    device = sysfs / devpath.lstrip("/")
    device.mkdir(parents=True)
    for name, value in (("product", product), ("manufacturer", manufacturer), ("bDeviceClass", device_class)):
        if value:
            (device / name).write_text(value + "\n")
    if listed:
        bus = sysfs / "bus" / "usb" / "devices"
        bus.mkdir(parents=True, exist_ok=True)
        (bus / device.name).symlink_to(device)
    return device


@pytest.fixture
def usb(tmp_path):
    sysfs = tmp_path / "sys"
    (sysfs / "bus" / "usb" / "devices").mkdir(parents=True)
    kernel, ours = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    ours.setblocking(False)
    source = LinuxUsbDeviceSource(sysfs=sysfs, open_socket=lambda: ours)
    source.sysfs, source.kernel, source.events = sysfs, kernel, []
    yield source
    source.shutdown()
    kernel.close()


def test_a_uevent_is_split_into_its_fields():
    fields = parse_uevent(uevent("add", "/devices/pci0/usb1/1-2", PRODUCT="46d/c52b/1211"))

    assert fields["ACTION"] == "add" and fields["DEVPATH"] == "/devices/pci0/usb1/1-2"
    assert fields["PRODUCT"] == "46d/c52b/1211"
    assert parse_uevent(b"libudev\0\xfe\xed") is None
    assert parse_uevent(b"") is None and parse_uevent(b"garbage") is None


def test_devices_are_named_from_sysfs_and_hubs_are_not_devices(tmp_path):
    assert describe_device(make_device(tmp_path, "/a/1-1", product="USB Receiver", manufacturer="Logitech")) \
        == "Logitech USB Receiver"
    assert describe_device(make_device(tmp_path, "/a/1-2", product="Logitech Webcam C920", manufacturer="Logitech")) \
        == "Logitech Webcam C920"
    assert describe_device(make_device(tmp_path, "/a/1-3")) == "USB device"
    assert describe_device(make_device(tmp_path, "/a/1-4", product="Hub", device_class="09")) is None
    assert describe_device(make_device(tmp_path, "/a/usb2", product="xHCI Host Controller")) is None


def test_arrival_and_removal_become_device_events(usb):
    devpath = "/devices/pci0/usb1/1-2"
    assert usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="Keyboard", listed=False)

    usb.kernel.send(uevent("add", devpath))
    usb.on_readable()
    usb.kernel.send(uevent("remove", devpath))
    usb.on_readable()

    assert usb.events == [
        DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "Keyboard", devpath),
        DeviceEvent(DeviceKind.USB, DeviceChange.DISCONNECTED, "Keyboard", devpath),
    ]


def test_a_device_present_at_start_is_named_on_removal_but_not_announced(usb):
    devpath = "/devices/pci0/usb1/1-4"
    make_device(usb.sysfs, devpath, product="Headset")
    usb.start(usb.events.append)
    assert usb.events == []

    usb.kernel.send(uevent("remove", devpath))
    usb.on_readable()

    assert usb.events == [DeviceEvent(DeviceKind.USB, DeviceChange.DISCONNECTED, "Headset", devpath)]


def test_interfaces_hubs_other_subsystems_and_other_actions_are_ignored(usb):
    usb.start(usb.events.append)
    make_device(usb.sysfs, "/d/usb1/1-5", product="Hub", device_class="09", listed=False)
    for message in (
        uevent("add", "/d/usb1/1-5"),                                   # a hub
        uevent("add", "/d/usb1/1-6:1.0", DEVTYPE="usb_interface"),      # an interface
        uevent("add", "/d/block/sda", SUBSYSTEM="block", DEVTYPE="disk"),
        uevent("bind", "/d/usb1/1-6"),
        uevent("remove", "/d/usb1/1-9"),                                # never known
    ):
        usb.kernel.send(message)
    usb.on_readable()

    assert usb.events == []


def test_the_notifier_delivers_kernel_messages_without_polling(usb):
    devpath = "/devices/pci0/usb1/1-7"
    usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="Mouse", listed=False)

    usb.kernel.send(uevent("add", devpath))
    spin(50)

    assert [event.name for event in usb.events] == ["Mouse"]


def test_shutdown_closes_the_socket_and_stops_delivery(usb):
    usb.start(usb.events.append)
    ours = usb._socket

    usb.shutdown()
    usb.shutdown()

    assert ours.fileno() == -1
    assert not usb.watching


def test_a_socket_that_cannot_open_means_unavailable(tmp_path):
    def refuse():
        raise PermissionError("netlink")

    assert LinuxUsbDeviceSource(sysfs=tmp_path, open_socket=refuse).start(lambda event: None) is False


# --- Audio: PipeWire ------------------------------------------------------------------


def node(object_id, media_class, name, description=None):
    return {"id": object_id, "type": "PipeWire:Interface:Node",
            "info": {"props": {"media.class": media_class, "node.name": name, "node.description": description}}}


def defaults(**values):
    return {"id": 40, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
            "metadata": [{"key": key.replace("_", "."), "value": {"name": name}} for key, name in values.items()]}


SPEAKER = node(66, "Audio/Sink", "alsa_output.speaker", "Speaker")
MIC = node(128, "Audio/Source", "alsa_input.mic", "Microphones")


def test_the_first_dump_is_the_baseline():
    tracker = PipeWireTracker()

    assert tracker.feed([SPEAKER, MIC, defaults(default_audio_sink="alsa_output.speaker")]) == []


def test_new_and_removed_audio_nodes_become_events():
    tracker = PipeWireTracker()
    tracker.feed([SPEAKER])

    added = tracker.feed([node(91, "Audio/Sink", "bluez_output.1", "Headphones")])
    updated = tracker.feed([node(91, "Audio/Sink", "bluez_output.1", "Headphones")])
    removed = tracker.feed([{"id": 91, "info": None}])

    assert added == [DeviceEvent(DeviceKind.AUDIO_OUTPUT, DeviceChange.CONNECTED, "Headphones", "bluez_output.1")]
    assert updated == []  # property updates of a known node are not news
    assert removed == [DeviceEvent(DeviceKind.AUDIO_OUTPUT, DeviceChange.DISCONNECTED, "Headphones", "bluez_output.1")]


def test_a_new_default_input_is_an_audio_input_change():
    tracker = PipeWireTracker()
    tracker.feed([SPEAKER, MIC, defaults(default_audio_source="alsa_input.mic")])

    usb_mic = node(140, "Audio/Source", "alsa_input.usb", "USB Mic")
    events = tracker.feed([usb_mic, defaults(default_audio_source="alsa_input.usb")])

    assert events == [
        DeviceEvent(DeviceKind.AUDIO_INPUT, DeviceChange.CONNECTED, "USB Mic", "alsa_input.usb"),
        DeviceEvent(DeviceKind.AUDIO_INPUT, DeviceChange.DEFAULT_CHANGED, "USB Mic", "alsa_input.usb"),
    ]
    assert tracker.feed([defaults(default_audio_source="alsa_input.usb")]) == []


def test_non_audio_objects_and_removals_of_unknown_ids_are_ignored():
    tracker = PipeWireTracker()
    tracker.feed([])

    assert tracker.feed([
        node(145, "Video/Source", "v4l2_input", "Webcam"),
        {"id": 80, "type": "PipeWire:Interface:Port", "info": {"props": {}}},
        {"id": 999, "info": None},
        "junk",
    ]) == []


def test_concatenated_json_arrives_in_any_chunks():
    stream = JsonArrayStream()
    text = json.dumps([SPEAKER], indent=2) + "\n" + json.dumps([{"id": 66, "info": None}], indent=2)

    values = []
    for start in range(0, len(text), 7):
        values += stream.feed(text[start:start + 7])

    assert values == [[SPEAKER], [{"id": 66, "info": None}]]


def test_without_pw_dump_audio_is_unavailable():
    assert PipeWireAudioSource(which=lambda name: None).start(lambda event: None) is False


@pytest.fixture
def fake_pw_dump(tmp_path):
    """A stand-in for ``pw-dump --monitor``: a baseline, one change, then waits."""
    script = tmp_path / "pw-dump"
    baseline = json.dumps([SPEAKER])
    change = json.dumps([node(91, "Audio/Sink", "bluez_output.1", "Headphones")])
    script.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        f"print({baseline!r}, flush=True)\ntime.sleep(0.2)\nprint({change!r}, flush=True)\ntime.sleep(60)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_the_monitor_process_feeds_events_and_shutdown_ends_it(fake_pw_dump):
    events = []
    source = PipeWireAudioSource(which=lambda name: str(fake_pw_dump))

    assert source.start(events.append)
    deadline = time.monotonic() + 10
    while not events and time.monotonic() < deadline:
        spin(50)
    process = source.process
    pid = process.processId()
    source.shutdown()
    source.shutdown()

    assert events == [DeviceEvent(DeviceKind.AUDIO_OUTPUT, DeviceChange.CONNECTED, "Headphones", "bluez_output.1")]
    assert source.process is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # no stray child left behind
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- Connectivity: NetworkManager ---------------------------------------------------------


@pytest.mark.parametrize(("value", "state"), [
    (4, Connectivity.ONLINE), (3, Connectivity.LIMITED), (2, Connectivity.LIMITED),
    (1, Connectivity.OFFLINE), (0, None), ("x", None), (None, None),
])
def test_networkmanager_states_map_to_connectivity(value, state):
    assert connectivity_from_nm(value) is state


class DisconnectedBus:
    def isConnected(self):
        return False


def test_no_system_bus_means_unavailable():
    assert NetworkManagerConnectivitySource(lambda: DisconnectedBus()).start(lambda event: None) is False


def test_reports_reach_the_listener_only_while_started():
    source = NetworkManagerConnectivitySource(lambda: DisconnectedBus())
    events = []
    source.report(4)
    source._listener = events.append  # as `start` leaves it
    source.report(1)
    source.report(0)

    assert events == [ConnectivityEvent(Connectivity.OFFLINE)]


# --- Composition ------------------------------------------------------------------------


def test_the_default_application_watches_nothing():
    application = build_application(scheduler=ManualScheduler())

    assert application.system_notices.sources == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktop adapters")
def test_a_linux_desktop_gets_usb_audio_and_connectivity_watchers():
    kinds = {type(source).__name__ for source in desktop_system_event_sources()}

    assert kinds == {"LinuxUsbDeviceSource", "PipeWireAudioSource", "NetworkManagerConnectivitySource"}


def test_the_application_starts_and_stops_its_sources():
    class Source:
        name = "fake"
        started = stopped = 0

        def start(self, listener):
            Source.started += 1
            return True

        def shutdown(self):
            Source.stopped += 1

    application = build_application(scheduler=ManualScheduler(), system_event_sources=[Source()])
    assert Source.started == 0

    application.start()
    application.shutdown()

    assert (Source.started, Source.stopped) == (1, 1)
