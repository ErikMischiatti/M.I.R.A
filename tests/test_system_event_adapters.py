"""Platform adapters report semantic system events, against fakes only.

No USB device, audio server or NetworkManager is needed: a temporary sysfs
tree and a socket pair stand in for the kernel, a script for ``pw-dump``, a
fake bus for D-Bus. The real facilities are exercised in the manual smoke
test.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

from mira.adapters.linux_usb import (
    LinuxUsbDeviceSource,
    UsbInterface,
    classify_usb_device,
    describe_device,
    is_configured,
    parse_uevent,
    read_interfaces,
)
from mira.adapters.networkmanager import NetworkManagerConnectivitySource, connectivity_from_nm
from mira.adapters.pipewire_audio import JsonArrayStream, PipeWireAudioSource, PipeWireTracker
from mira.application.composition import build_application, desktop_system_event_sources
from mira.domain.devices import DeviceCategory
from mira.domain.scheduler import ManualScheduler
from mira.domain.system_events import Connectivity, ConnectivityEvent, DeviceChange, DeviceEvent, DeviceKind
from usb_doubles import (
    ADB,
    AUDIO_CONTROL,
    AUDIO_STREAMING,
    HID,
    MASS_STORAGE,
    MTP,
    PTP,
    VENDOR,
    VIDEO_CONTROL,
    VIDEO_STREAMING,
    FakeKernel,
    configure,
    make_device,
    uevent,
)


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


@pytest.fixture
def kernel(tmp_path):
    kernel = FakeKernel(tmp_path / "sys")
    yield kernel
    kernel.close()


@pytest.fixture
def usb(kernel):
    source = kernel.source()
    source.sysfs, source.kernel, source.events = kernel.sysfs, kernel, []
    yield source
    source.shutdown()


def deliver(usb, *messages):
    usb.kernel.send(*messages)
    usb.on_readable()


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
    assert describe_device(make_device(tmp_path, "/a/1-3")) == ""  # it names itself nothing
    assert describe_device(make_device(tmp_path, "/a/1-4", product="Hub", device_class="09")) is None
    assert describe_device(make_device(tmp_path, "/a/usb2", product="xHCI Host Controller")) is None


def test_arrival_and_removal_become_device_events(usb):
    devpath = "/devices/pci0/usb1/1-2"
    assert usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="Keyboard", manufacturer="Cherry", interfaces=[HID], listed=False)

    deliver(usb, uevent("add", devpath))
    deliver(usb, uevent("remove", devpath))

    arrival = DeviceEvent(
        DeviceKind.USB, DeviceChange.CONNECTED, "Cherry Keyboard", devpath,
        category=DeviceCategory.INPUT, manufacturer="Cherry",
    )
    assert usb.events == [arrival, replace(arrival, change=DeviceChange.DISCONNECTED)]


def test_a_device_present_at_start_is_named_on_removal_but_not_announced(usb):
    devpath = "/devices/pci0/usb1/1-4"
    make_device(usb.sysfs, devpath, product="Headset", interfaces=[AUDIO_CONTROL, AUDIO_STREAMING, HID])
    usb.start(usb.events.append)
    assert usb.events == []

    deliver(usb, uevent("remove", devpath))

    assert usb.events == [DeviceEvent(
        DeviceKind.USB, DeviceChange.DISCONNECTED, "Headset", devpath, category=DeviceCategory.AUDIO,
    )]


def test_interfaces_hubs_other_subsystems_and_other_actions_are_ignored(usb):
    usb.start(usb.events.append)
    make_device(usb.sysfs, "/d/usb1/1-5", product="Hub", device_class="09", listed=False)
    deliver(
        usb,
        uevent("add", "/d/usb1/1-5"),                                   # a hub
        uevent("bind", "/d/usb1/1-5"),
        uevent("add", "/d/usb1/1-6:1.0", DEVTYPE="usb_interface"),      # an interface
        uevent("add", "/d/block/sda", SUBSYSTEM="block", DEVTYPE="disk"),
        uevent("bind", "/d/usb1/1-6"),                                  # never added
        uevent("change", "/d/usb1/1-5"),
        uevent("remove", "/d/usb1/1-9"),                                # never known
    )

    assert usb.events == []


def test_the_notifier_delivers_kernel_messages_without_polling(usb):
    devpath = "/devices/pci0/usb1/1-7"
    usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="Mouse", interfaces=[HID], listed=False)

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


# --- USB: one physical connection, one event --------------------------------------


def test_a_device_not_yet_configured_is_announced_at_its_bind_with_every_interface(usb):
    # What the kernel really sends: the device's add before its configuration,
    # then one add per interface, then the device's bind.
    devpath = "/devices/pci0/usb1/1-3"
    usb.start(usb.events.append)
    device = make_device(usb.sysfs, devpath, product="Pixel 8", manufacturer="Google", configured=False, listed=False)

    deliver(usb, uevent("add", devpath))
    assert usb.events == []

    configure(device, [MTP, ADB])
    deliver(
        usb,
        uevent("add", f"{devpath}/1-3:1.0", DEVTYPE="usb_interface", INTERFACE="6/1/1"),
        uevent("add", f"{devpath}/1-3:1.1", DEVTYPE="usb_interface", INTERFACE="255/66/1"),
        uevent("bind", f"{devpath}/1-3:1.0", DEVTYPE="usb_interface"),
        uevent("bind", devpath),
    )

    assert usb.events == [DeviceEvent(
        DeviceKind.USB, DeviceChange.CONNECTED, "Google Pixel 8", devpath,
        category=DeviceCategory.SMARTPHONE, manufacturer="Google",
    )]


def test_a_composite_device_configured_by_the_time_it_is_read_is_announced_once(usb):
    devpath = "/devices/pci0/usb1/1-3"
    usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="C920", interfaces=[VIDEO_CONTROL, VIDEO_STREAMING,
                                                               AUDIO_CONTROL, AUDIO_STREAMING], listed=False)

    deliver(
        usb,
        uevent("add", devpath),
        *(uevent("add", f"{devpath}/1-3:1.{n}", DEVTYPE="usb_interface") for n in range(4)),
        uevent("bind", devpath),
        uevent("add", devpath + "/1-3:1.0/video4linux/video0", SUBSYSTEM="video4linux", DEVTYPE=""),
    )

    assert [(event.change, event.category) for event in usb.events] == [
        (DeviceChange.CONNECTED, DeviceCategory.CAMERA),
    ]


def test_a_device_removed_before_it_was_configured_announces_nothing(usb):
    devpath = "/devices/pci0/usb1/1-3"
    usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="Flaky", configured=False, listed=False)

    deliver(usb, uevent("add", devpath), uevent("remove", devpath), uevent("bind", devpath))

    assert usb.events == []


def test_rapid_reconnection_reports_each_arrival_and_removal_in_order(usb):
    devpath = "/devices/pci0/usb1/1-3"
    usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="Stick", interfaces=[MASS_STORAGE], listed=False)

    for _ in range(3):
        deliver(usb, uevent("add", devpath), uevent("bind", devpath), uevent("remove", devpath))

    assert [event.change for event in usb.events] == [DeviceChange.CONNECTED, DeviceChange.DISCONNECTED] * 3
    assert {event.category for event in usb.events} == {DeviceCategory.STORAGE}


def test_an_add_for_a_device_still_known_there_is_a_new_arrival(usb):
    # The removal was lost (the kernel dropped it): the new device is news.
    devpath = "/devices/pci0/usb1/1-3"
    make_device(usb.sysfs, devpath, product="Old", interfaces=[HID])
    usb.start(usb.events.append)

    deliver(usb, uevent("add", devpath))

    assert [(event.change, event.name) for event in usb.events] == [(DeviceChange.CONNECTED, "Old")]


def test_a_device_with_no_strings_is_unnamed_and_unknown_not_guessed(usb):
    devpath = "/devices/pci0/usb1/1-3"
    usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, interfaces=[VENDOR], listed=False)

    deliver(usb, uevent("add", devpath))

    assert usb.events == [DeviceEvent(
        DeviceKind.USB, DeviceChange.CONNECTED, "", devpath, category=DeviceCategory.UNKNOWN,
    )]


def test_interfaces_listed_on_the_bus_are_not_devices_present_at_start(usb):
    make_device(usb.sysfs, "/d/usb1/1-2", product="Keyboard", interfaces=[HID, HID])

    usb.start(usb.events.append)

    assert list(usb._present) == ["/d/usb1/1-2"]


def test_a_serial_number_never_reaches_an_event_or_the_debug_log(kernel, capsys):
    devpath = "/devices/pci0/usb1/1-3"
    source = kernel.source(debug=True)
    events = []
    source.start(events.append)
    make_device(kernel.sysfs, devpath, product="Pixel 8", manufacturer="Google", vendor="18d1",
                product_id="4ee7", serial="SERIAL0123456789", interfaces=[MTP], listed=False)

    kernel.send(uevent("add", devpath), uevent("remove", devpath))
    source.on_readable()
    source.shutdown()

    log = capsys.readouterr().err
    assert "SERIAL0123456789" not in log and "SERIAL0123456789" not in repr(events)
    assert "[devices] connected 1-3: smartphone 'Google Pixel 8' (18d1:4ee7, interfaces 06/01/01)" in log
    assert "[devices] disconnected 1-3" in log


def test_without_debug_ordinary_device_events_are_not_logged(usb, capsys):
    devpath = "/devices/pci0/usb1/1-3"
    usb.start(usb.events.append)
    make_device(usb.sysfs, devpath, product="Mouse", interfaces=[HID], listed=False)

    deliver(usb, uevent("add", devpath), uevent("remove", devpath))

    assert len(usb.events) == 2 and "[devices]" not in capsys.readouterr().err


# --- USB: classification ------------------------------------------------------------


def interfaces(*specs: str) -> list[UsbInterface]:
    result = []
    for spec in specs:
        triple, _, description = spec.partition(" ")
        cls, subclass, protocol = (int(part, 16) for part in triple.split("/"))
        result.append(UsbInterface(cls, subclass, protocol, description))
    return result


@pytest.mark.parametrize(("specs", "category"), [
    ((MTP, ADB), DeviceCategory.SMARTPHONE),                    # Android, file transfer + debugging
    ((MTP,), DeviceCategory.SMARTPHONE),                        # Android, file transfer
    ((VENDOR, "ff/42/01"), DeviceCategory.SMARTPHONE),          # Android, debugging only
    (("ff/42/03",), DeviceCategory.SMARTPHONE),                 # fastboot
    ((MASS_STORAGE,), DeviceCategory.STORAGE),
    ((VIDEO_CONTROL, VIDEO_STREAMING, AUDIO_CONTROL, AUDIO_STREAMING), DeviceCategory.CAMERA),
    ((PTP,), DeviceCategory.CAMERA),                            # a still camera (or a phone in PTP mode)
    ((AUDIO_CONTROL, AUDIO_STREAMING, HID), DeviceCategory.AUDIO),
    ((HID, HID), DeviceCategory.INPUT),
    ((VENDOR,), DeviceCategory.UNKNOWN),
    (("e0/01/01", "e0/01/01"), DeviceCategory.UNKNOWN),         # a Bluetooth adapter
    ((), DeviceCategory.UNKNOWN),
])
def test_devices_are_classified_from_their_own_interfaces(specs, category):
    assert classify_usb_device(interfaces(*specs)) is category


def test_a_device_level_class_counts_and_an_iphone_is_a_smartphone():
    assert classify_usb_device([], device_class=0x08) is DeviceCategory.STORAGE
    assert classify_usb_device([], device_class=0xEF) is DeviceCategory.UNKNOWN
    assert classify_usb_device(interfaces(PTP, VENDOR), product="iPhone") is DeviceCategory.SMARTPHONE


def test_a_device_with_unreadable_interfaces_is_classified_from_what_is_left(tmp_path):
    device = make_device(tmp_path, "/a/1-1", interfaces=[HID, MASS_STORAGE])
    (device / "1-1:1.1" / "bInterfaceClass").write_text("not hex\n")

    assert read_interfaces(device) == (UsbInterface(0x03, 0x01, 0x01),)
    assert is_configured(device)


def test_configuration_is_complete_only_with_every_interface(tmp_path):
    device = make_device(tmp_path, "/a/1-1", configured=False)
    assert not is_configured(device)

    (device / "bNumInterfaces").write_text(" 2\n")
    interface = device / "1-1:1.0"
    interface.mkdir()
    (interface / "bInterfaceClass").write_text("08\n")
    assert not is_configured(device)


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


class BrokenSocket:
    """recv fails the same way forever, as a socket in a bad state would."""

    def __init__(self, error):
        self.error, self.reads, self.closed = error, 0, False

    def recv(self, size):
        self.reads += 1
        if self.reads > 100:
            raise AssertionError("kept reading a broken socket")
        raise self.error

    def fileno(self):
        return -1

    def close(self):
        self.closed = True


def test_a_persistent_socket_error_stops_watching_instead_of_spinning(tmp_path, capsys):
    import errno

    broken = BrokenSocket(OSError(errno.EBADF, "Bad file descriptor"))
    source = LinuxUsbDeviceSource(sysfs=tmp_path, open_socket=lambda: broken)
    source._socket, source._listener = broken, lambda event: None  # as `start` leaves it

    source.on_readable()

    assert broken.reads == 1 and broken.closed and not source.watching
    assert "USB notices stop" in capsys.readouterr().err


def test_dropped_kernel_messages_are_skipped_not_fatal(tmp_path):
    import errno

    class Overflowing(BrokenSocket):
        def recv(self, size):
            self.reads += 1
            if self.reads == 1:
                raise OSError(errno.ENOBUFS, "No buffer space available")
            raise BlockingIOError

    sock = Overflowing(None)
    source = LinuxUsbDeviceSource(sysfs=tmp_path, open_socket=lambda: sock)
    source._socket, source._listener = sock, lambda event: None

    source.on_readable()

    assert sock.reads == 2 and source.watching
