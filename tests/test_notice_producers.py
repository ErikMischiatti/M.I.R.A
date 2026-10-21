"""Producers turn application and system events into notices on the bus.

Qt-free: a `ManualScheduler` drives every timer, fakes stand in for the
platform sources, so no test needs a USB device, an audio server or a
network manager.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from mira.actions.action_models import ActionResult
from mira.core.notice_producers import (
    CONNECTIVITY_SETTLE_MS,
    DEVICE_REPEAT_MS,
    DEVICE_SETTLE_MS,
    NOTICE_EVENT,
    SystemNoticeProducer,
    TaskNoticeProducer,
    connectivity_notice,
    device_notice,
    task_notice,
)
from mira.domain.devices import DeviceCategory
from mira.domain.notice import Notice, NoticeCategory, NoticeDevice, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.domain.system_events import (
    Connectivity,
    ConnectivityEvent,
    DeviceChange,
    DeviceEvent,
    DeviceKind,
)
from mira.messaging.events import EventBus


class FakeSource:
    def __init__(self, name="fake", available=True):
        self.name = name
        self.available = available
        self.listener = None
        self.started = 0
        self.stopped = 0

    def start(self, listener):
        self.started += 1
        if self.available:
            self.listener = listener
        return self.available

    def shutdown(self):
        self.stopped += 1
        self.listener = None


@pytest.fixture
def system():
    bus, scheduler = EventBus(), ManualScheduler()
    source = FakeSource()
    producer = SystemNoticeProducer(bus, scheduler, [source])
    notices: list[Notice] = []
    bus.subscribe(NOTICE_EVENT, notices.append)
    producer.start()
    producer.notices, producer.scheduler, producer.source = notices, scheduler, source
    yield producer
    producer.shutdown()


def plug(change=DeviceChange.CONNECTED, kind=DeviceKind.USB, name="Keyboard", key="1-2", category=None):
    return DeviceEvent(kind, change, name, key, category=category)


# --- Tasks -----------------------------------------------------------------------------


def test_action_results_become_task_notices_on_the_bus():
    bus = EventBus()
    TaskNoticeProducer(bus)
    notices = []
    bus.subscribe(NOTICE_EVENT, notices.append)

    bus.emit("action_completed", ActionResult(True, "open_browser", "ok"))
    bus.emit("action_failed", ActionResult(False, "open_browser", "no"))
    bus.emit("action_failed", ActionResult(False, "delete", "confirm", data={"requires_confirmation": True}))

    assert notices == [
        Notice(NoticeCategory.TASK, NoticeTone.SUCCESS),
        Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed"),
        Notice(NoticeCategory.TASK, NoticeTone.WARNING, "Needs confirmation"),
    ]


def test_a_payload_without_data_is_a_plain_failure():
    assert task_notice(False, object()).tone is NoticeTone.ERROR


# --- Device conversion -----------------------------------------------------------------


@pytest.mark.parametrize(("event", "text"), [
    (plug(), "Keyboard connected"),
    (plug(DeviceChange.DISCONNECTED), "Keyboard disconnected"),
    (plug(DeviceChange.DEFAULT_CHANGED, DeviceKind.AUDIO_INPUT, "USB Mic"), "Microphone: USB Mic"),
    (plug(DeviceChange.DEFAULT_CHANGED, DeviceKind.AUDIO_OUTPUT, "Headphones"), "Output: Headphones"),
    (plug(name="  "), "USB device connected"),
])
def test_device_events_become_device_notices(event, text):
    assert device_notice(event) == Notice(NoticeCategory.DEVICE, NoticeTone.INFO, text)


@pytest.mark.parametrize(("event", "text", "device"), [
    (plug(name="Pixel 8", category=DeviceCategory.SMARTPHONE), "Pixel 8 connected",
     NoticeDevice(DeviceCategory.SMARTPHONE, connected=True)),
    (plug(DeviceChange.DISCONNECTED, name="Pixel 8", category=DeviceCategory.SMARTPHONE), "Pixel 8 disconnected",
     NoticeDevice(DeviceCategory.SMARTPHONE, connected=False)),
    # A device that names itself nothing is called what it is, never guessed.
    (plug(name="", category=DeviceCategory.STORAGE), "USB storage connected",
     NoticeDevice(DeviceCategory.STORAGE, connected=True)),
    (plug(name="", category=DeviceCategory.UNKNOWN), "USB device connected",
     NoticeDevice(DeviceCategory.UNKNOWN, connected=True)),
])
def test_a_classified_device_notice_says_what_kind_of_device_it_is(event, text, device):
    assert device_notice(event) == Notice(NoticeCategory.DEVICE, NoticeTone.INFO, text, device)


def test_a_settled_classified_arrival_and_removal_keep_their_device():
    bus, scheduler = EventBus(), ManualScheduler()
    source = FakeSource()
    producer = SystemNoticeProducer(bus, scheduler, [source])
    notices: list[Notice] = []
    bus.subscribe(NOTICE_EVENT, notices.append)
    producer.start()
    phone = plug(name="Pixel 8", category=DeviceCategory.SMARTPHONE)

    source.listener(phone)
    scheduler.advance(DEVICE_SETTLE_MS)
    source.listener(plug(DeviceChange.DISCONNECTED, name="Pixel 8", category=DeviceCategory.SMARTPHONE))
    scheduler.advance(DEVICE_SETTLE_MS)
    producer.shutdown()

    assert [(notice.text, notice.device) for notice in notices] == [
        ("Pixel 8 connected", NoticeDevice(DeviceCategory.SMARTPHONE, connected=True)),
        ("Pixel 8 disconnected", NoticeDevice(DeviceCategory.SMARTPHONE, connected=False)),
    ]


def test_a_device_change_is_announced_once_it_settles(system):
    system.handle(plug())
    assert system.notices == []

    system.scheduler.advance(DEVICE_SETTLE_MS)

    assert system.notices == [device_notice(plug())]


def test_a_bounce_announces_nothing(system):
    system.handle(plug())
    system.handle(plug(DeviceChange.DISCONNECTED))
    system.scheduler.advance(DEVICE_SETTLE_MS * 2)

    assert system.notices == []


def test_disconnect_and_reconnect_are_both_announced_once_settled(system):
    system.handle(plug())
    system.scheduler.advance(DEVICE_SETTLE_MS)
    system.handle(plug(DeviceChange.DISCONNECTED))
    system.scheduler.advance(DEVICE_SETTLE_MS)
    system.handle(plug())
    system.scheduler.advance(DEVICE_SETTLE_MS)

    assert [n.text for n in system.notices] == ["Keyboard connected", "Keyboard disconnected", "Keyboard connected"]


def test_an_unchanged_repeat_is_dropped_until_the_repeat_window_passes(system):
    for _ in range(2):
        system.handle(plug())
        system.scheduler.advance(DEVICE_SETTLE_MS)
    assert len(system.notices) == 1

    system.scheduler.advance(DEVICE_REPEAT_MS)
    system.handle(plug())
    system.scheduler.advance(DEVICE_SETTLE_MS)
    assert len(system.notices) == 2


def test_a_new_device_that_becomes_the_default_is_announced_as_connected(system):
    output = dict(kind=DeviceKind.AUDIO_OUTPUT, name="Headphones", key="bluez_output.1")
    system.handle(plug(**output))
    system.handle(plug(DeviceChange.DEFAULT_CHANGED, **output))
    system.scheduler.advance(DEVICE_SETTLE_MS)

    assert [n.text for n in system.notices] == ["Headphones connected"]


def test_different_devices_settle_independently(system):
    system.handle(plug(key="1-2", name="Keyboard"))
    system.handle(plug(key="1-3", name="Mouse"))
    system.scheduler.advance(DEVICE_SETTLE_MS)

    assert sorted(n.text for n in system.notices) == ["Keyboard connected", "Mouse connected"]


# --- Connectivity --------------------------------------------------------------------


def test_the_first_connectivity_state_is_a_baseline_not_a_notice(system):
    system.handle(ConnectivityEvent(Connectivity.ONLINE))
    system.scheduler.advance(CONNECTIVITY_SETTLE_MS * 2)

    assert system.notices == []


def test_loss_and_restoration_are_announced_after_they_hold(system):
    system.handle(ConnectivityEvent(Connectivity.ONLINE))
    system.handle(ConnectivityEvent(Connectivity.OFFLINE))
    system.scheduler.advance(CONNECTIVITY_SETTLE_MS - 1)
    assert system.notices == []
    system.scheduler.advance(1)
    assert system.notices == [connectivity_notice(Connectivity.OFFLINE)]

    system.handle(ConnectivityEvent(Connectivity.ONLINE))
    system.scheduler.advance(CONNECTIVITY_SETTLE_MS)
    assert system.notices[-1] == Notice(NoticeCategory.CONNECTIVITY, NoticeTone.SUCCESS, "Back online")


def test_a_short_drop_or_flapping_says_nothing(system):
    system.handle(ConnectivityEvent(Connectivity.ONLINE))
    for state in (Connectivity.OFFLINE, Connectivity.LIMITED, Connectivity.OFFLINE, Connectivity.ONLINE):
        system.handle(ConnectivityEvent(state))
        system.scheduler.advance(CONNECTIVITY_SETTLE_MS // 3)
    system.scheduler.advance(CONNECTIVITY_SETTLE_MS * 2)

    assert system.notices == []


def test_limited_connectivity_is_a_warning():
    assert connectivity_notice(Connectivity.LIMITED).tone is NoticeTone.WARNING
    assert connectivity_notice(Connectivity.OFFLINE).category is NoticeCategory.CONNECTIVITY


# --- Lifecycle ------------------------------------------------------------------------


def test_nothing_starts_before_start_and_unavailable_sources_are_reported(capsys):
    bus, scheduler = EventBus(), ManualScheduler()
    here, missing = FakeSource("here"), FakeSource("missing", available=False)
    producer = SystemNoticeProducer(bus, scheduler, [here, missing])
    assert here.started == missing.started == 0

    producer.start()
    producer.start()

    assert here.started == missing.started == 1
    assert producer.active_sources == [here]
    out = capsys.readouterr().out
    assert "watching here" in out and "missing unavailable" in out
    producer.shutdown()


def test_shutdown_stops_sources_cancels_timers_and_ignores_late_events(system):
    system.handle(plug())
    system.handle(ConnectivityEvent(Connectivity.ONLINE))
    system.handle(ConnectivityEvent(Connectivity.OFFLINE))
    assert system.scheduler.pending_timers() == 2

    system.shutdown()
    system.shutdown()
    system.handle(plug(key="9"))

    assert system.source.stopped == 2
    assert system.scheduler.pending_timers() == 0
    system.scheduler.advance(10_000)
    assert system.notices == []


def test_announcement_timers_do_not_accumulate(system):
    for index in range(20):
        system.handle(plug(key=str(index)))
        system.scheduler.advance(DEVICE_SETTLE_MS)
        system.scheduler.advance(DEVICE_REPEAT_MS)

    assert system.scheduler.pending_timers() == 0
    assert len(system._repeat_timers) <= 1


def test_producers_know_no_platform_or_toolkit():
    source = (pathlib.Path(__file__).resolve().parents[1] / "mira/core/notice_producers.py").read_text()
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith(("PySide6", "mira.adapters", "mira.ui", "socket", "subprocess", "ctypes"))
                   for name in imported)
