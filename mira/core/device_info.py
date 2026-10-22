"""Learn more about connected devices, off the UI thread, and say when it changes.

    "device_changed" (SystemNoticeProducer) ──▶ DeviceInfoService ──▶ "device_info"
                                                     │  ▲             (DeviceInfoUpdate)
                                     Scheduler.submit│  │on_complete
                                                     ▼  │
                                            DeviceInfoProvider.query  (mira.adapters:
                                                                       ADB, UPower, sysfs)

The service follows the devices the user was told about (``"device_changed"``
from the system producer, not raw source events), so information never
arrives for a device whose arrival is still settling or was a bounce. For
each connected device it picks the first provider that supports it and asks
it once, through `Scheduler.submit`: the query runs on a worker thread, and
its answer is handled back on the serialized (UI) thread. Nothing here waits.

**Which answer counts.** Every connection of a device gets a new
*generation*; an answer for an older generation — the device left, or came
back, while the query ran — is discarded, as is everything after `shutdown`.
One query per device is in flight at a time.

**What is published.** Only information that was read, and only when it
differs from what was last published for that device, as a
`DeviceInfoUpdate`. Its ``announce`` is True for the first information of a
connection and for a change of charging state, which are worth bringing
forward; a battery percentage that merely moved only refines a notice that
is already showing. A device that re-enumerates as itself (a phone switching
USB mode) keeps what was known, so asking again announces nothing new.

**When it asks again.** An answer that says asking again may help (the phone
has not authorized this computer yet) is retried after `RETRY_DELAYS_MS`,
then given up until the device reconnects. With a refresh interval, a device
that reported a battery is asked again at that interval — one bounded query,
never a stream. Without one (the default), each connection is asked once.

Problems are logged once per connection and only with ``debug``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass

from mira.core.notice_producers import DEVICE_CHANGED_EVENT
from mira.domain.device_info import DeviceInfo, DeviceInfoProvider, DeviceInfoUpdate, InfoResult
from mira.domain.scheduler import Scheduler, TimerHandle
from mira.domain.system_events import DeviceChange, DeviceEvent
from mira.messaging.events import EventBus

DEVICE_INFO_EVENT = "device_info"

# While a phone waits for the user to accept a prompt: about a minute in all.
RETRY_DELAYS_MS = (2000, 4000, 8000, 16000, 30000)


@dataclass
class _Tracked:
    device: DeviceEvent
    provider: DeviceInfoProvider
    generation: int
    info: DeviceInfo | None = None
    attempts: int = 0
    in_flight: bool = False
    timer: TimerHandle | None = None
    problem: str | None = None


def _safe_query(provider: DeviceInfoProvider, device: DeviceEvent) -> InfoResult:
    # `Scheduler.submit` work must not raise; a provider that does anyway is
    # a problem to report, not a crash.
    try:
        return provider.query(device)
    except Exception as error:  # noqa: BLE001
        return InfoResult(problem=f"{type(error).__name__}: {error}")


class DeviceInfoService:
    """See the module. Idle until `start`; `shutdown` is idempotent."""

    def __init__(
        self,
        event_bus: EventBus,
        scheduler: Scheduler,
        providers: Sequence[DeviceInfoProvider] = (),
        *,
        refresh_interval_s: int = 0,
        debug: bool = False,
    ):
        self._bus = event_bus
        self._scheduler = scheduler
        self._providers = list(providers)
        self._refresh_ms = max(int(refresh_interval_s), 0) * 1000
        self._debug = debug
        self._running = False
        self._generation = 0
        self._devices: dict[str, _Tracked] = {}
        event_bus.subscribe(DEVICE_CHANGED_EVENT, self.on_device_changed)

    @property
    def providers(self) -> list[DeviceInfoProvider]:
        return list(self._providers)

    def tracked(self) -> dict[str, DeviceInfo | None]:
        """Connected devices a provider supports, and what is known of each."""
        return {key: tracked.info for key, tracked in self._devices.items()}

    def start(self) -> None:
        self._running = True

    def shutdown(self) -> None:
        """Stop asking: queries in progress end promptly, answers are dropped."""
        self._running = False
        for provider in self._providers:
            try:
                provider.cancel()
            except Exception as error:  # noqa: BLE001 - one provider must not keep the others running
                self._log(f"{provider.name} failed to cancel: {error}")
        for key in list(self._devices):
            self._forget(key)

    # --- Devices -------------------------------------------------------------------

    def on_device_changed(self, device: DeviceEvent) -> None:
        if not self._running:
            return
        if device.change is DeviceChange.DISCONNECTED:
            self._forget(device.key)
            return
        if device.change is not DeviceChange.CONNECTED:
            return
        previous = self._devices.get(device.key)
        known = previous.info if previous is not None and previous.device.identity == device.identity else None
        self._forget(device.key)
        provider = self._provider_for(device)
        if provider is None:
            return
        self._generation += 1
        tracked = _Tracked(device, provider, self._generation, info=known)
        self._devices[device.key] = tracked
        self._query(tracked)

    def _provider_for(self, device: DeviceEvent) -> DeviceInfoProvider | None:
        for provider in self._providers:
            try:
                if provider.supports(device):
                    return provider
            except Exception as error:  # noqa: BLE001 - a broken provider is skipped
                self._log(f"{provider.name} failed to decide on {device.name!r}: {error}")
        return None

    def _forget(self, key: str) -> None:
        tracked = self._devices.pop(key, None)
        if tracked is not None and tracked.timer is not None:
            tracked.timer.cancel()
        # A query still in flight answers for a generation nobody tracks.

    # --- Queries -------------------------------------------------------------------

    def _query(self, tracked: _Tracked) -> None:
        if tracked.in_flight or not self._running:
            return
        tracked.in_flight = True
        tracked.timer = None
        key, generation = tracked.device.key, tracked.generation
        provider, device = tracked.provider, tracked.device
        self._scheduler.submit(
            lambda: _safe_query(provider, device),
            lambda result: self._answered(key, generation, result),
        )

    def _answered(self, key: str, generation: int, result: InfoResult) -> None:
        tracked = self._devices.get(key)
        if not self._running or tracked is None or tracked.generation != generation:
            return  # stale: the device left or came back meanwhile
        tracked.in_flight = False
        info = result.info
        if info is None or info.is_empty:
            self._unanswered(tracked, result)
            return
        tracked.attempts, tracked.problem = 0, None
        previous, tracked.info = tracked.info, info
        if info != previous:
            announce = previous is None or info.charging != previous.charging
            self._log(f"{tracked.provider.name}: {tracked.device.name!r} {info}")
            self._bus.emit(DEVICE_INFO_EVENT, DeviceInfoUpdate(tracked.device, info, announce))
        if self._refresh_ms and info.battery_percent is not None:
            tracked.timer = self._scheduler.call_later(self._refresh_ms, lambda: self._again(key, generation))

    def _unanswered(self, tracked: _Tracked, result: InfoResult) -> None:
        if result.problem != tracked.problem:
            tracked.problem = result.problem
            self._log(f"{tracked.provider.name}: no information on {tracked.device.name!r}: {result.problem}")
        if result.retry and tracked.attempts < len(RETRY_DELAYS_MS):
            delay = RETRY_DELAYS_MS[tracked.attempts]
            tracked.attempts += 1
            key, generation = tracked.device.key, tracked.generation
            tracked.timer = self._scheduler.call_later(delay, lambda: self._again(key, generation))
        elif tracked.info is not None and self._refresh_ms:
            # A refresh failed once; what was read stays, and so does the rhythm.
            key, generation = tracked.device.key, tracked.generation
            tracked.timer = self._scheduler.call_later(self._refresh_ms, lambda: self._again(key, generation))

    def _again(self, key: str, generation: int) -> None:
        tracked = self._devices.get(key)
        if tracked is not None and tracked.generation == generation:
            self._query(tracked)

    def _log(self, message: str) -> None:
        if self._debug:
            print(f"[devices] {message}", file=sys.stderr)
