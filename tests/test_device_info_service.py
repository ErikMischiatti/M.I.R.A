"""The device information service: which provider, which answer, when to ask again.

Qt-free and deterministic: a `ManualScheduler` holds submitted queries until
the test runs them, so a query can be overtaken by a disconnect, a
reconnect or a shutdown exactly where the test says.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mira.core.device_info import DEVICE_INFO_EVENT, RETRY_DELAYS_MS, DeviceInfoService
from mira.core.notice_producers import DEVICE_CHANGED_EVENT
from mira.domain.device_info import ChargingState, DeviceInfo, DeviceInfoUpdate, InfoResult
from mira.domain.devices import DeviceCategory
from mira.domain.scheduler import ManualScheduler
from mira.domain.system_events import DeviceChange, DeviceEvent, DeviceKind
from mira.messaging.events import EventBus

PIXEL = DeviceEvent(
    DeviceKind.USB, DeviceChange.CONNECTED, "Google Pixel 8", "/d/usb3/3-2",
    category=DeviceCategory.SMARTPHONE, identity="usb:18d1:Google:Pixel 8", services=frozenset({"mtp", "adb"}),
)
CHARGING = DeviceInfo("Google", "Pixel 8", 84, ChargingState.CHARGING)


def gone(device: DeviceEvent) -> DeviceEvent:
    return replace(device, change=DeviceChange.DISCONNECTED)


class FakeProvider:
    """Answers from a script; records what it was asked."""

    def __init__(self, name="fake", service="adb", answers=None):
        self.name = name
        self.service = service
        self.answers = list(answers or [InfoResult(CHARGING)])
        self.asked: list[DeviceEvent] = []

    def supports(self, device):
        return self.service in device.services

    def query(self, device):
        self.asked.append(device)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


@pytest.fixture
def make():
    built = []

    def make(*providers, refresh_s=0, debug=False):
        bus, scheduler = EventBus(), ManualScheduler()
        service = DeviceInfoService(bus, scheduler, providers, refresh_interval_s=refresh_s, debug=debug)
        updates: list[DeviceInfoUpdate] = []
        bus.subscribe(DEVICE_INFO_EVENT, updates.append)
        service.start()
        service.bus, service.scheduler, service.updates = bus, scheduler, updates
        service.changed = lambda event: bus.emit(DEVICE_CHANGED_EVENT, event)
        built.append(service)
        return service

    yield make
    for service in built:
        service.shutdown()


# --- Provider selection ---------------------------------------------------------------


def test_the_first_provider_that_supports_the_device_is_asked():
    upower, adb = FakeProvider("upower", "usbmux"), FakeProvider("adb", "adb")
    service = DeviceInfoService(EventBus(), ManualScheduler(), [upower, adb])
    service.start()

    service.on_device_changed(PIXEL)
    service._scheduler.run_all()

    assert adb.asked == [PIXEL] and upower.asked == []


def test_a_device_no_provider_supports_is_not_tracked_or_asked(make):
    provider = FakeProvider(service="adb")
    service = make(provider)
    keyboard = DeviceEvent(DeviceKind.USB, DeviceChange.CONNECTED, "Keyboard", "/d/usb3/3-1",
                           category=DeviceCategory.INPUT)

    service.changed(keyboard)

    assert service.scheduler.pending_work() == 0 and service.tracked() == {} and provider.asked == []


def test_a_provider_that_fails_to_decide_is_skipped(make):
    class Broken(FakeProvider):
        def supports(self, device):
            raise RuntimeError("broken")

    fallback = FakeProvider("adb")
    service = make(Broken("broken"), fallback)

    service.changed(PIXEL)
    service.scheduler.run_all()

    assert fallback.asked == [PIXEL]


# --- Answers --------------------------------------------------------------------------


def test_information_arrives_after_the_query_and_is_announced_once(make):
    service = make(FakeProvider())

    service.changed(PIXEL)
    assert service.updates == []  # nothing waits for the query
    service.scheduler.run_all()

    assert service.updates == [DeviceInfoUpdate(PIXEL, CHARGING, announce=True)]
    assert service.tracked() == {PIXEL.key: CHARGING}


def test_a_query_that_raises_is_a_problem_not_a_crash(make, capsys):
    class Raising(FakeProvider):
        def query(self, device):
            raise ValueError("bad output")

    service = make(Raising(), debug=True)

    service.changed(PIXEL)
    service.scheduler.run_all()

    assert service.updates == []
    assert "no information on 'Google Pixel 8': ValueError: bad output" in capsys.readouterr().err


def test_an_empty_answer_publishes_nothing(make):
    service = make(FakeProvider(answers=[InfoResult(DeviceInfo())]))

    service.changed(PIXEL)
    service.scheduler.run_all()

    assert service.updates == []


# --- Stale answers --------------------------------------------------------------------


def test_an_answer_for_a_device_that_left_meanwhile_is_discarded(make):
    service = make(FakeProvider())
    service.changed(PIXEL)

    service.changed(gone(PIXEL))
    service.scheduler.run_all()

    assert service.updates == [] and service.tracked() == {}


def test_an_answer_for_an_earlier_connection_is_discarded(make):
    old, new = InfoResult(DeviceInfo(battery_percent=10)), InfoResult(CHARGING)
    service = make(FakeProvider(answers=[old, new]))
    service.changed(PIXEL)
    service.changed(gone(PIXEL))
    service.changed(PIXEL)  # back before the first query answered

    service.scheduler.run_all()

    assert service.updates == [DeviceInfoUpdate(PIXEL, CHARGING, announce=True)]


def test_after_shutdown_nothing_is_asked_or_published(make):
    service = make(FakeProvider())
    service.changed(PIXEL)

    service.shutdown()
    service.scheduler.run_all()
    service.changed(PIXEL)

    assert service.updates == [] and service.scheduler.pending_work() == 0
    assert service.scheduler.pending_timers() == 0


def test_a_device_that_reenumerates_as_itself_announces_nothing_new(make):
    provider = FakeProvider()
    service = make(provider)
    service.changed(PIXEL)
    service.scheduler.run_all()

    # A silent mode switch is published as the device arriving again only.
    service.changed(replace(PIXEL, services=frozenset({"adb"})))
    service.scheduler.run_all()

    assert len(provider.asked) == 2 and len(service.updates) == 1


def test_a_device_that_really_left_and_came_back_is_announced_again(make):
    service = make(FakeProvider())
    service.changed(PIXEL)
    service.scheduler.run_all()

    service.changed(gone(PIXEL))  # the user was told it left
    service.changed(PIXEL)
    service.scheduler.run_all()

    assert [update.announce for update in service.updates] == [True, True]


# --- Asking again ---------------------------------------------------------------------


def test_a_retryable_problem_is_asked_again_with_growing_delays_then_given_up(make):
    provider = FakeProvider(answers=[InfoResult(problem="unauthorized", retry=True)])
    service = make(provider)
    service.changed(PIXEL)
    service.scheduler.run_all()

    for delay in RETRY_DELAYS_MS:
        service.scheduler.advance(delay - 1)
        assert service.scheduler.pending_work() == 0
        service.scheduler.advance(1)
        service.scheduler.run_all()
    service.scheduler.advance(10 * max(RETRY_DELAYS_MS))

    assert len(provider.asked) == 1 + len(RETRY_DELAYS_MS)
    assert service.scheduler.pending_timers() == 0 and service.updates == []


def test_the_phone_authorized_while_waiting_is_announced(make):
    provider = FakeProvider(answers=[InfoResult(problem="unauthorized", retry=True), InfoResult(CHARGING)])
    service = make(provider)
    service.changed(PIXEL)
    service.scheduler.run_all()

    service.scheduler.advance(RETRY_DELAYS_MS[0])
    service.scheduler.run_all()

    assert service.updates == [DeviceInfoUpdate(PIXEL, CHARGING, announce=True)]


def test_a_final_problem_is_not_asked_again(make):
    provider = FakeProvider(answers=[InfoResult(problem="adb is not installed")])
    service = make(provider)

    service.changed(PIXEL)
    service.scheduler.run_all()
    service.scheduler.advance(10 * max(RETRY_DELAYS_MS))

    assert len(provider.asked) == 1 and service.scheduler.pending_timers() == 0


def test_a_problem_is_logged_once_per_connection_and_only_with_debug(make, capsys):
    unauthorized = InfoResult(problem="unauthorized", retry=True)
    quiet = make(FakeProvider(answers=[unauthorized]))
    quiet.changed(PIXEL)
    quiet.scheduler.run_all()
    assert capsys.readouterr().err == ""

    loud = make(FakeProvider(answers=[unauthorized]), debug=True)
    loud.changed(PIXEL)
    loud.scheduler.run_all()
    loud.scheduler.advance(RETRY_DELAYS_MS[0])
    loud.scheduler.run_all()
    assert capsys.readouterr().err.count("unauthorized") == 1


def test_without_a_refresh_interval_a_connection_is_asked_once(make):
    provider = FakeProvider()
    service = make(provider)

    service.changed(PIXEL)
    service.scheduler.run_all()
    service.scheduler.advance(3_600_000)

    assert len(provider.asked) == 1 and service.scheduler.pending_timers() == 0


def test_a_refresh_publishes_only_changes_and_announces_only_a_charging_change(make):
    answers = [
        InfoResult(CHARGING),
        InfoResult(CHARGING),                                                   # unchanged
        InfoResult(replace(CHARGING, battery_percent=85)),                       # percentage moved
        InfoResult(replace(CHARGING, battery_percent=100, charging=ChargingState.FULL)),
    ]
    provider = FakeProvider(answers=answers)
    service = make(provider, refresh_s=60)
    service.changed(PIXEL)
    service.scheduler.run_all()

    for _ in range(3):
        assert service.scheduler.pending_work() == 0  # one query at a time, after the answer
        service.scheduler.advance(60_000)
        service.scheduler.run_all()

    assert [(update.info.battery_percent, update.announce) for update in service.updates] == [
        (84, True), (85, False), (100, True),
    ]


def test_a_failed_refresh_keeps_what_was_read_and_the_rhythm(make):
    provider = FakeProvider(answers=[InfoResult(CHARGING), InfoResult(problem="adb failed"), InfoResult(CHARGING)])
    service = make(provider, refresh_s=60)
    service.changed(PIXEL)
    service.scheduler.run_all()

    service.scheduler.advance(60_000)
    service.scheduler.run_all()
    service.scheduler.advance(60_000)
    service.scheduler.run_all()

    assert len(provider.asked) == 3 and service.tracked() == {PIXEL.key: CHARGING}
    assert len(service.updates) == 1


def test_disconnecting_cancels_the_refresh(make):
    provider = FakeProvider()
    service = make(provider, refresh_s=60)
    service.changed(PIXEL)
    service.scheduler.run_all()

    service.changed(gone(PIXEL))
    service.scheduler.advance(600_000)

    assert len(provider.asked) == 1 and service.scheduler.pending_timers() == 0


def test_repeated_connection_cycles_leave_nothing_behind(make):
    provider = FakeProvider(answers=[InfoResult(problem="unauthorized", retry=True)])
    service = make(provider, refresh_s=60)

    for _ in range(50):
        service.changed(PIXEL)
        service.scheduler.run_all()
        service.changed(gone(PIXEL))

    assert service.tracked() == {} and service.scheduler.pending_timers() == 0
    assert service.scheduler.pending_work() == 0


def test_events_before_start_are_ignored():
    bus, scheduler = EventBus(), ManualScheduler()
    DeviceInfoService(bus, scheduler, [FakeProvider()])

    bus.emit(DEVICE_CHANGED_EVENT, PIXEL)

    assert scheduler.pending_work() == 0
