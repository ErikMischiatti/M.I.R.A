"""Turn application and system events into `Notice` values on the bus.

Producers decide *whether* something is worth a glance and say what it is
(`NoticeCategory`, `NoticeTone`, short text). They never decide how it is
shown: every notice is emitted as the bus event ``"notice"`` and each
embodiment presents it its own way (the desktop shell hands it to the notch's
presenter), or not at all.

    action_completed / action_failed ──▶ TaskNoticeProducer   ─┐
    SystemEventSource adapters ────────▶ SystemNoticeProducer ─┴─▶ "notice"

Noise is the producers' problem, not the adapters' and not the notch's:

- **Device changes settle** for `DEVICE_SETTLE_MS` per device before they are
  announced. A later change of the same device within that window replaces
  the pending one, and a connect and a disconnect that cancel out (a USB
  device re-enumerating, a flaky cable) announce nothing. A device that
  appears and then becomes the default announces only its arrival.
- **Repeats are dropped**: the same change of the same device announced
  again within `DEVICE_REPEAT_MS`, with no other change of it in between
  (two sources seeing one event, a driver re-announcing), is not announced
  again.
- **Connectivity is debounced**: the first state a source reports is the
  baseline, never a notice; afterwards a state must hold for
  `CONNECTIVITY_SETTLE_MS` and differ from the last announced one, so a
  network that drops for a second, or flaps, says nothing.

Timers run on the injected `Scheduler`, so the producers are deterministic
under `ManualScheduler` and need no GUI toolkit.
"""

from __future__ import annotations

from collections.abc import Sequence

from mira.actions.action_models import ActionResult
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import Scheduler, TimerHandle
from mira.domain.system_events import (
    Connectivity,
    ConnectivityEvent,
    DeviceChange,
    DeviceEvent,
    DeviceKind,
    SystemEvent,
    SystemEventSource,
    connectivity_notice,
    device_notice,
)
from mira.messaging.events import EventBus

NOTICE_EVENT = "notice"

DEVICE_SETTLE_MS = 800
DEVICE_REPEAT_MS = 3000
CONNECTIVITY_SETTLE_MS = 3000


# --- Tasks ---------------------------------------------------------------------------


def task_notice(succeeded: bool, payload: ActionResult | object) -> Notice:
    """The notice for an action lifecycle event.

    An action held back for confirmation is a warning, not an error: nothing
    went wrong yet.
    """
    if succeeded:
        return Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)
    data = getattr(payload, "data", None) or {}
    if data.get("requires_confirmation"):
        return Notice(NoticeCategory.TASK, NoticeTone.WARNING, "Needs confirmation")
    return Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed")


class TaskNoticeProducer:
    """Announces the result of every action MIRA ran."""

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        event_bus.subscribe("action_completed", self.on_action_completed)
        event_bus.subscribe("action_failed", self.on_action_failed)

    def on_action_completed(self, payload) -> None:
        self._bus.emit(NOTICE_EVENT, task_notice(True, payload))

    def on_action_failed(self, payload) -> None:
        self._bus.emit(NOTICE_EVENT, task_notice(False, payload))


# --- System ---------------------------------------------------------------------------

class SystemNoticeProducer:
    """Owns the system event sources and announces their meaningful changes.

    `start` starts every source and reports which ones are available;
    `shutdown` stops them all, cancels pending timers and ignores anything
    that still arrives. Both are idempotent, and nothing happens until
    `start`.
    """

    def __init__(self, event_bus: EventBus, scheduler: Scheduler, sources: Sequence[SystemEventSource] = ()):
        self._bus = event_bus
        self._scheduler = scheduler
        self._sources = list(sources)
        self._running = False
        self._started: list[SystemEventSource] = []
        # (kind, key) -> the change waiting to settle, and its timer.
        self._pending: dict[tuple[DeviceKind, str], tuple[DeviceEvent, TimerHandle]] = {}
        # (kind, change, key) -> when it was last announced, in scheduler ms.
        # Announced recently: (kind, change, key); each entry expires on its own timer.
        self._announced: set[tuple[DeviceKind, DeviceChange, str]] = set()
        self._repeat_timers: list[TimerHandle] = []
        self._connectivity: Connectivity | None = None
        self._announced_connectivity: Connectivity | None = None
        self._connectivity_timer: TimerHandle | None = None

    @property
    def sources(self) -> list[SystemEventSource]:
        return list(self._sources)

    @property
    def active_sources(self) -> list[SystemEventSource]:
        """The sources that started: their facility is available here."""
        return list(self._started)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for source in self._sources:
            if source.start(self.handle):
                self._started.append(source)
                print(f"[notices] watching {source.name}")
            else:
                print(f"[notices] {source.name} unavailable; no notices from it")

    def shutdown(self) -> None:
        self._running = False
        for source in self._sources:
            source.shutdown()
        self._started = []
        for _, timer in self._pending.values():
            timer.cancel()
        self._pending.clear()
        for timer in self._repeat_timers:
            timer.cancel()
        self._repeat_timers = []
        self._announced.clear()
        if self._connectivity_timer is not None:
            self._connectivity_timer.cancel()
            self._connectivity_timer = None

    def handle(self, event: SystemEvent) -> None:
        """One event from a source; anything after shutdown is ignored."""
        if not self._running:
            return
        if isinstance(event, DeviceEvent):
            self._device(event)
        elif isinstance(event, ConnectivityEvent):
            self._connectivity_changed(event.state)

    # --- Devices ----------------------------------------------------------------

    def _device(self, event: DeviceEvent) -> None:
        slot = (event.kind, event.key)
        pending = self._pending.pop(slot, None)
        if pending is not None:
            previous, timer = pending
            timer.cancel()
            if {previous.change, event.change} == {DeviceChange.CONNECTED, DeviceChange.DISCONNECTED}:
                return  # a bounce: nothing changed for the user
            if previous.change is DeviceChange.CONNECTED and event.change is DeviceChange.DEFAULT_CHANGED:
                event = previous  # its arrival says enough
        timer = self._scheduler.call_later(DEVICE_SETTLE_MS, lambda: self._settled(slot))
        self._pending[slot] = (event, timer)

    def _settled(self, slot: tuple[DeviceKind, str]) -> None:
        pending = self._pending.pop(slot, None)
        if pending is None or not self._running:
            return
        event = pending[0]
        identity = (event.kind, event.change, event.key)
        if identity in self._announced:
            return  # repeated within DEVICE_REPEAT_MS
        # Only an unchanged repeat is noise: a device that left and came back
        # is news again.
        self._announced = {entry for entry in self._announced if (entry[0], entry[2]) != (event.kind, event.key)}
        self._announced.add(identity)
        self._repeat_timers = [timer for timer in self._repeat_timers if timer.is_pending()]
        self._repeat_timers.append(
            self._scheduler.call_later(DEVICE_REPEAT_MS, lambda: self._announced.discard(identity))
        )
        self._bus.emit(NOTICE_EVENT, device_notice(event))

    # --- Connectivity ----------------------------------------------------------------

    def _connectivity_changed(self, state: Connectivity) -> None:
        if self._connectivity is None:
            # The baseline: what the system was like when we started looking.
            self._connectivity = self._announced_connectivity = state
            return
        self._connectivity = state
        if self._connectivity_timer is not None:
            self._connectivity_timer.cancel()
            self._connectivity_timer = None
        if state is self._announced_connectivity:
            return  # back where it was before anyone noticed
        self._connectivity_timer = self._scheduler.call_later(CONNECTIVITY_SETTLE_MS, self._connectivity_settled)

    def _connectivity_settled(self) -> None:
        self._connectivity_timer = None
        if not self._running or self._connectivity is self._announced_connectivity:
            return
        self._announced_connectivity = self._connectivity
        self._bus.emit(NOTICE_EVENT, connectivity_notice(self._connectivity))
