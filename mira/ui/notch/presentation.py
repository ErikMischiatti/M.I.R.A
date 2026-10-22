"""Qt-free decision of what the notch presents, and the one authority over it.

The notch shows three things that change for different reasons:

- *presence* (`NotchPresence`): how much notch is visible;
- *activity* (`ActivityState`): what MIRA is doing, as committed by the
  application's activity authority;
- a transient *notice* (`mira.domain.notice.Notice`): something worth a
  glance right now.

`NotchPresenter` combines them into one immutable `NotchPresentation` and
hands every change to its listeners; `NotchWindow` only renders what it is
given. Nothing here knows Qt, the window, microphones, cameras or any other
concrete service: notices arrive already classified, and activity arrives as
the domain value the application committed.

Presence is resolved, never set from several places. The owner chooses a
*base* presence — hidden or shown — and the notch expands while at least one
`ExpansionReason` is active:

    HOVER     the pointer is over the notch (if `expand_on_hover`)
    PINNED    someone explicitly holds it open (tuning preview)
    NOTICE    a notice whose category expands automatically is showing
    CONTROLS  the user opened the notch's own controls (right click)
    LISTENING MIRA is listening to the user (the microphone is open for it)

    base HIDDEN               -> HIDDEN, whatever the reasons
    base COMPACT, no reasons  -> COMPACT
    base COMPACT, any reason  -> EXPANDED

so hover ending while a notice still shows keeps it expanded, a notice
expiring while the pointer is still there keeps it expanded, and the notch
only collapses when the last reason goes.

Notices have a lifetime. `present` shows a notice for
`NotchBehaviour.notice_duration_ms`, timed by the injected `Scheduler` port —
the application's own scheduler in production, a `ManualScheduler` in tests —
and nothing else ever collapses the notch. A notice whose category does not
expand automatically still becomes the content for its lifetime (visible if
the notch is open for another reason), but adds no expansion reason.

**Priority.** Every notice has a `NoticePriority` (`mira.domain.notice`):
PASSIVE (devices, connectivity) < FEEDBACK (task results) < INTERACTION (a
turn of conversation; no notice has it) < CRITICAL (errors, alerts). A turn
is MIRA listening, thinking about what it heard, or speaking
(`INTERACTION_ACTIVITIES`): the activity authority commits all three for one
exchange — LISTENING on input, THINKING once processing starts, SPEAKING or
an affect with the response — so a notice never slips in between them. The
*floor* is the highest of: the showing notice's priority; INTERACTION
during a turn; and, while something else takes the content
band — the integrated controls — a level above every priority, since a
notice drawn under them could not be seen.

- *Preemption.* A notice above the floor is shown at once and gets the
  full duration; so is one at the floor about the *same subject* as the
  showing notice (a device leaving replaces its own arrival). One at the
  floor about another subject waits its turn instead, so two devices
  connecting together are both seen, one after the other. A notice that is
  preempted by a higher one goes back to wait (without a second reaction)
  and returns after it, if still relevant: an error does not swallow the
  device notice it interrupted.
- *Deferral.* A notice below the floor waits instead of being lost — a USB
  device connecting while MIRA listens does not cover the waveform. At most
  `MAX_DEFERRED_NOTICES` wait; when full, the lowest-priority, oldest one is
  dropped.
- *Coalescing.* A newer notice about the same subject (the same device by
  `NoticeDevice.key`, else the same category) replaces the waiting one; a
  device that arrived and left while its arrival waited cancels out, since
  nothing changed for the user. The same notice as the one showing only
  restarts its time: no new animation, no reaction.
- *Expiration.* Three things are kept apart. *Validity*: a notice no
  longer true is gone at once — superseded by a newer one about its
  subject, or, for an arrival, cancelled by the same device leaving — and
  nothing waits longer than `MAX_NOTICE_AGE_MS` in all, whatever holds it
  back. *Eligible waiting*: the time a notice waits although nothing but
  the queue holds it back (no turn, no band it cannot pass); it may last
  as long as every notice ahead of it needs to be shown, plus
  `ELIGIBLE_GRACE_MS`, and it is not counted during a conversation or
  under a band owner — a long turn does not use it up. *Display*: once
  shown, `notice_duration_ms`.
- *Yielding.* When MIRA starts listening or speaking, a showing notice
  below INTERACTION steps aside: it goes back to wait, without a second
  reaction, and the conversation has the band from its first moment. Any
  notice steps aside the same way when the controls open, and comes back
  when they close.
- *Restoration.* When the floor drops — the showing notice expires or is
  dismissed, listening or speaking ends — the waiting notice with the
  highest priority (the oldest among equals) is shown, in the same
  resolution step, so the notch stays expanded from one to the next instead
  of collapsing between them. One at a time: the next waits for that one to
  expire.

**Reactions.** Listeners of `subscribe_reactions` hear about a notice when it
starts showing — at once, or when restored — never while it waits, never
for a refinement or a repeat. The eyes' cue is played from there
(`startup.bind_presenter`), so MIRA does not react to what it does not
show, and does not greet a device twice.

**Refinement.** `refine` brings later information about a device (from the
device information service): it updates the notice about that device in
place, whether it is showing (keeping its remaining time, unless the update
is worth announcing) or waiting; it is shown on its own only if it is worth
announcing, and then as any other notice, without a reaction. Information
about a device whose latest notice is its departure is dropped.

Activity alone never expands the notch. It only chooses the content shown
when the notch is already expanded and no notice is showing.

Controls are not a notice either: right click opens the notch's integrated
controls (`control_actions`: the offered actions except opening the main
window, which is the double click), drawn inside the notch instead of a
native menu. They close when one is used, on another right click, or
`CONTROLS_LINGER_MS` after the pointer leaves (re-entering in time keeps
them), timed on the injected `Scheduler`.

Running tasks are persistent state, not a notice: while any runs, the content
of an open notch is "Working" (unless a notice is showing), and nothing
expands it — a long task must never hold the notch open. Content precedence:

    controls  >  notice  >  listening  >  task running  >  activity

Listening is the one activity that expands the notch, through its own
LISTENING reason, because the microphone being open must never be
invisible. While listening the content is the *real* waveform of the
captured audio (`shows_waveform`); the window reads it from its waveform
source on every frame. Without real samples there is no waveform: the
content is the listening symbol instead, never a synthetic animation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from mira.domain.devices import DeviceCategory
from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice, NoticeCategory, NoticePriority, NoticeTone, notice_priority
from mira.domain.scheduler import Scheduler, TimerHandle
from mira.ui.notch.presence import NotchPresence


class ExpansionReason(StrEnum):
    """Why the notch is expanded; it stays expanded while any one holds."""

    HOVER = "hover"
    PINNED = "pinned"
    NOTICE = "notice"
    CONTROLS = "controls"
    LISTENING = "listening"


class NotchSymbol(StrEnum):
    """The notch's small vector symbol vocabulary (see `symbol_painter`)."""

    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    TASK = "task"
    DEVICE = "device"
    NETWORK = "network"
    PHONE = "phone"
    STORAGE = "storage"
    CAMERA = "camera"
    HEADPHONES = "headphones"
    KEYBOARD = "keyboard"
    USB = "usb"


class NotchAction(StrEnum):
    """User intents the notch can raise; its owner decides what they do."""

    OPEN_MAIN_WINDOW = "open main window"
    OPEN_SETTINGS = "open settings"
    QUIT = "quit"


@dataclass(frozen=True, slots=True)
class NotchContent:
    """What the expanded notch shows under the eyes: a symbol, a short label.

    ``detail`` is secondary information about what the label names
    ("84% · Charging" after "Pixel 8"), drawn after it, less prominently;
    the label is shortened before the detail is.
    """

    symbol: NotchSymbol
    label: str | None = None
    detail: str | None = None


_ACTIVITY_CONTENT = {
    ActivityState.LISTENING: NotchContent(NotchSymbol.LISTENING, "Listening"),
    ActivityState.THINKING: NotchContent(NotchSymbol.THINKING, "Thinking"),
    ActivityState.SPEAKING: NotchContent(NotchSymbol.SPEAKING, "Speaking"),
}

_TONE_SYMBOL = {
    NoticeTone.SUCCESS: NotchSymbol.SUCCESS,
    NoticeTone.WARNING: NotchSymbol.WARNING,
    NoticeTone.ERROR: NotchSymbol.ERROR,
}

# Neutral notices show what they are about.
_CATEGORY_SYMBOL = {
    NoticeCategory.ALERT: NotchSymbol.WARNING,
    NoticeCategory.DEVICE: NotchSymbol.DEVICE,
    NoticeCategory.TASK: NotchSymbol.TASK,
    NoticeCategory.CONNECTIVITY: NotchSymbol.NETWORK,
}

# A device notice that says what kind of device it is about shows that kind.
# Only the USB source classifies today, so an unknown kind is a USB plug.
_DEVICE_SYMBOL = {
    DeviceCategory.SMARTPHONE: NotchSymbol.PHONE,
    DeviceCategory.STORAGE: NotchSymbol.STORAGE,
    DeviceCategory.CAMERA: NotchSymbol.CAMERA,
    DeviceCategory.AUDIO: NotchSymbol.HEADPHONES,
    DeviceCategory.INPUT: NotchSymbol.KEYBOARD,
    DeviceCategory.UNKNOWN: NotchSymbol.USB,
}

# Used when a notice brings no text of its own. A neutral device or task
# notice without text is left to its symbol.
_TONE_LABEL = {
    NoticeTone.SUCCESS: "Done",
    NoticeTone.WARNING: "Warning",
    NoticeTone.ERROR: "Error",
}


def notice_content(notice: Notice) -> NotchContent:
    symbol = _TONE_SYMBOL.get(notice.tone)
    if symbol is None and notice.device is not None:
        symbol = _DEVICE_SYMBOL[notice.device.category]
    if symbol is None:
        symbol = _CATEGORY_SYMBOL[notice.category]
    text = (notice.text or "").strip() or None
    detail = (notice.detail or "").strip() or None
    return NotchContent(symbol, text if text is not None else _TONE_LABEL.get(notice.tone), detail)


TASK_RUNNING_CONTENT = NotchContent(NotchSymbol.TASK, "Working")
LISTENING_CONTENT = NotchContent(NotchSymbol.LISTENING, "Listening")

# The integrated controls, in display order. Opening the main window is not
# among them: the double click is that gesture.
CONTROL_ORDER = (NotchAction.OPEN_SETTINGS, NotchAction.QUIT)

CONTROLS_LINGER_MS = 1500


def control_actions(actions: frozenset[NotchAction]) -> tuple[NotchAction, ...]:
    return tuple(action for action in CONTROL_ORDER if action in actions)


def activity_content(activity: ActivityState) -> NotchContent | None:
    """Idle shows nothing beyond the eyes."""
    return _ACTIVITY_CONTENT.get(activity)


@dataclass(frozen=True, slots=True)
class NotchPresentation:
    """Everything the notch should present at one moment."""

    presence: NotchPresence = NotchPresence.HIDDEN
    activity: ActivityState = ActivityState.IDLE
    notice: Notice | None = None
    reasons: frozenset[ExpansionReason] = frozenset()
    actions: frozenset[NotchAction] = frozenset()
    tasks_running: bool = False
    controls: tuple[NotchAction, ...] = ()
    listening: bool = False
    capturing: bool = False

    @property
    def shows_waveform(self) -> bool:
        """Whether the content band shows the captured audio right now."""
        return self.listening and self.capturing and not self.controls and self.notice is None

    @property
    def content(self) -> NotchContent | None:
        """A showing notice, else listening, else running work, else the activity.

        Not drawn while `controls` are open (they take the content band), and
        replaced by the waveform when `shows_waveform` and samples exist.
        """
        if self.notice is not None:
            return notice_content(self.notice)
        if self.listening:
            return LISTENING_CONTENT
        if self.tasks_running:
            return TASK_RUNNING_CONTENT
        return activity_content(self.activity)


MIN_NOTICE_DURATION_MS = 1000
MAX_NOTICE_DURATION_MS = 30000
DEFAULT_NOTICE_DURATION_MS = 4000

# Conservative: problems, device and connectivity changes are worth
# interrupting a glance for (the producers already suppress their noise);
# task results already appear in the main window.
DEFAULT_AUTOMATIC_EXPANSION = frozenset({NoticeCategory.ALERT, NoticeCategory.DEVICE, NoticeCategory.CONNECTIVITY})


@dataclass(frozen=True, slots=True)
class NotchBehaviour:
    """The persisted interaction preferences; the defaults are production."""

    expand_on_hover: bool = True
    automatic_expansion: frozenset[NoticeCategory] = DEFAULT_AUTOMATIC_EXPANSION
    notice_duration_ms: int = DEFAULT_NOTICE_DURATION_MS

    def __post_init__(self):
        duration = self.notice_duration_ms
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValueError(f"notice duration must be whole milliseconds, got {duration!r}")
        if not MIN_NOTICE_DURATION_MS <= duration <= MAX_NOTICE_DURATION_MS:
            raise ValueError(
                f"notice duration must be within {MIN_NOTICE_DURATION_MS}..{MAX_NOTICE_DURATION_MS} ms, "
                f"got {duration}"
            )
        object.__setattr__(self, "automatic_expansion", frozenset(self.automatic_expansion))

    def expands_for(self, category: NoticeCategory) -> bool:
        return category in self.automatic_expansion


Listener = Callable[[NotchPresentation], None]
ReactionListener = Callable[[Notice], None]

MAX_DEFERRED_NOTICES = 3
# A waiting notice is dropped this long after it arrived, whatever held it
# back: a bound on retention (a long turn of conversation lasts well under it).
MAX_NOTICE_AGE_MS = 60_000
# Beyond the turns of the notices ahead of it, how long a waiting notice may
# stay waiting while nothing but the queue holds it back.
ELIGIBLE_GRACE_MS = 2_000
# Eligible waiting is counted in steps of this, on the scheduler.
ELIGIBILITY_TICK_MS = 500

# The activities of one turn of conversation; see the module.
INTERACTION_ACTIVITIES = frozenset({ActivityState.LISTENING, ActivityState.THINKING, ActivityState.SPEAKING})

# Above every notice priority: the content band belongs to something the
# user opened, and a notice drawn under it would not be seen.
_BAND_TAKEN = NoticePriority.CRITICAL + 1


class NoticeOutcome(StrEnum):
    """What became of a notice handed to the presenter."""

    SHOWN = "shown"
    DEFERRED = "deferred"
    REFRESHED = "refreshed"
    """The same notice was already showing; its time restarted."""
    UPDATED = "updated"
    """A refinement changed the notice about its device (showing or waiting)."""
    UNCHANGED = "unchanged"
    DROPPED = "dropped"


@dataclass(eq=False)
class _Deferred:
    notice: Notice
    priority: NoticePriority
    react: bool
    order: int
    eligible_left_ms: int
    timer: TimerHandle | None = None
    """Drops it at `MAX_NOTICE_AGE_MS`."""


def _subject(notice: Notice) -> tuple:
    """What a notice is about, for coalescing: its device, else its category."""
    if notice.device is not None and notice.device.key is not None:
        return ("device", notice.device.key)
    return ("category", notice.category)


def _cancel_out(waiting: Notice, newer: Notice) -> bool:
    """An arrival still waiting, followed by the same device leaving."""
    return (
        waiting.device is not None and newer.device is not None
        and waiting.device.connected and not newer.device.connected
    )


class NotchPresenter:
    """The one authority over what the notch presents.

    Inputs are plain method calls from the owner (`DesktopShell`, or a
    standalone tool) and from the bound window (hover). Every input
    re-resolves the presentation; listeners hear about it only when it
    actually changed. `actions` are the user intents this embodiment's owner
    can honour, and are presented so the window offers nothing else.

    `shutdown` cancels the notice timer and stops notifying, so nothing runs
    once the owner has torn down; it is idempotent.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        behaviour: NotchBehaviour | None = None,
        *,
        actions: frozenset[NotchAction] = frozenset(),
    ):
        self._scheduler = scheduler
        self._behaviour = NotchBehaviour() if behaviour is None else behaviour
        self._actions = frozenset(actions)
        self._base = NotchPresence.HIDDEN
        self._activity = ActivityState.IDLE
        self._hovered = False
        self._pinned = False
        self._tasks_running = False
        self._controls_open = False
        self._controls_timer: TimerHandle | None = None
        self._listening = False
        self._capturing = False
        self._notice: Notice | None = None
        self._notice_priority = NoticePriority.PASSIVE
        self._deferred: list[_Deferred] = []
        self._deferred_order = 0
        self._eligibility_tick: TimerHandle | None = None
        self._reactions: list[ReactionListener] = []
        self._expiry: TimerHandle | None = None
        # Identifies the notice the pending expiry belongs to, so a callback
        # that outlives its cancellation can never expire a newer notice.
        self._generation = 0
        self._listeners: list[Listener] = []
        self._stopped = False
        self._presentation = self._resolve()

    # --- Reading ---------------------------------------------------------

    @property
    def presentation(self) -> NotchPresentation:
        return self._presentation

    @property
    def behaviour(self) -> NotchBehaviour:
        return self._behaviour

    @property
    def hovered(self) -> bool:
        return self._hovered

    @property
    def pinned(self) -> bool:
        return self._pinned

    @property
    def deferred(self) -> tuple[Notice, ...]:
        """The notices waiting to be shown, oldest first."""
        return tuple(entry.notice for entry in sorted(self._deferred, key=lambda entry: entry.order))

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def subscribe_reactions(self, listener: ReactionListener) -> None:
        """Hear about each notice when it starts showing; see the module."""
        self._reactions.append(listener)

    # --- Inputs ------------------------------------------------------------

    def show(self) -> None:
        self._base = NotchPresence.COMPACT
        self._publish()

    def hide(self) -> None:
        # A hidden notch cannot be under the pointer; never carry a stale
        # hover or open controls into the next show.
        self._base = NotchPresence.HIDDEN
        self._hovered = False
        self._controls_open = False
        self._cancel_controls_timer()
        self._restore()
        self._publish()

    def set_activity(self, activity: ActivityState) -> None:
        self._activity = activity
        self._yield_to_context()
        self._restore()
        self._publish()

    def set_hovered(self, hovered: bool) -> None:
        self._hovered = bool(hovered)
        if self._controls_open:
            if self._hovered:
                self._cancel_controls_timer()
            elif self._controls_timer is None and not self._stopped:
                self._controls_timer = self._scheduler.call_later(CONTROLS_LINGER_MS, self._controls_lingered)
        self._publish()

    # --- Controls ---------------------------------------------------------

    @property
    def controls_open(self) -> bool:
        return self._controls_open

    def open_controls(self) -> None:
        """Show the integrated controls, if this notch offers any."""
        if self._stopped or self._base is NotchPresence.HIDDEN or not control_actions(self._actions):
            return
        self._controls_open = True
        self._cancel_controls_timer()
        self._yield_to_context()
        self._publish()

    def close_controls(self) -> None:
        self._controls_open = False
        self._cancel_controls_timer()
        self._restore()
        self._publish()

    def toggle_controls(self) -> None:
        """The right-click gesture."""
        if self._controls_open:
            self.close_controls()
        else:
            self.open_controls()

    def _controls_lingered(self) -> None:
        self._controls_timer = None
        if not self._hovered:
            self.close_controls()

    def _cancel_controls_timer(self) -> None:
        if self._controls_timer is not None:
            self._controls_timer.cancel()
            self._controls_timer = None

    def set_listening(self, listening: bool, capturing: bool = False) -> None:
        """Whether MIRA listens, and whether real audio is arriving for it."""
        self._listening = bool(listening)
        self._capturing = bool(listening and capturing)
        self._yield_to_context()
        self._restore()
        self._publish()

    def set_tasks_running(self, running: bool) -> None:
        """Whether MIRA is executing anything; content only, never presence."""
        self._tasks_running = bool(running)
        self._publish()

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = bool(pinned)
        self._publish()

    def set_behaviour(self, behaviour: NotchBehaviour) -> None:
        """New preferences apply at once; a new duration from the next notice."""
        self._behaviour = behaviour
        self._publish()

    def present(self, notice: Notice, *, react: bool = True) -> NoticeOutcome:
        """Show ``notice`` now, or let it wait, by priority; see the module.

        ``react`` False shows it without telling the reaction listeners (it
        refines something MIRA already reacted to).
        """
        if self._stopped:
            return NoticeOutcome.DROPPED
        if notice == self._notice:
            self._start_expiry()
            return NoticeOutcome.REFRESHED
        waiting = self._take_deferred(_subject(notice))
        if waiting is not None and _cancel_out(waiting.notice, notice):
            return NoticeOutcome.DROPPED
        if waiting is not None:
            react = react or waiting.react  # its arrival has not been reacted to yet
        priority = notice_priority(notice)
        floor = self._floor()
        showing = self._notice
        same_subject = showing is not None and _subject(showing) == _subject(notice)
        if priority > floor or (priority == floor and (showing is None or same_subject)):
            if showing is not None and not same_subject and priority > self._notice_priority:
                self._defer(showing, self._notice_priority, react=False)  # interrupted: back later
            self._show(notice, priority, react)
            return NoticeOutcome.SHOWN
        self._defer(notice, priority, react)
        return NoticeOutcome.DEFERRED

    def refine(self, notice: Notice, *, announce: bool) -> NoticeOutcome:
        """Later information about the device ``notice`` is about; see the module."""
        if self._stopped or notice.device is None:
            return NoticeOutcome.DROPPED
        key = notice.device.key
        showing = self._notice
        if showing is not None and showing.device is not None and showing.device.key == key:
            if not showing.device.connected:
                return NoticeOutcome.DROPPED  # it left: nothing more to say about it
            if notice == showing:
                return NoticeOutcome.UNCHANGED
            self._notice = notice
            if announce:
                self._start_expiry()
            self._publish()
            return NoticeOutcome.UPDATED
        for entry in self._deferred:
            if entry.notice.device is not None and entry.notice.device.key == key:
                if not entry.notice.device.connected:
                    return NoticeOutcome.DROPPED
                entry.notice = notice  # keeps its place, its time and its reaction
                return NoticeOutcome.UPDATED
        if announce:
            return self.present(notice, react=False)
        return NoticeOutcome.DROPPED

    def dismiss_notice(self) -> None:
        self._cancel_expiry()
        self._notice = None
        self._restore()
        self._publish()

    def shutdown(self) -> None:
        self._cancel_expiry()
        self._cancel_controls_timer()
        for entry in self._deferred:
            if entry.timer is not None:
                entry.timer.cancel()
        self._deferred.clear()
        if self._eligibility_tick is not None:
            self._eligibility_tick.cancel()
            self._eligibility_tick = None
        self._stopped = True

    # --- Arbitration ---------------------------------------------------------

    def _interacting(self) -> bool:
        return self._listening or self._activity in INTERACTION_ACTIVITIES

    def _band_taken(self) -> bool:
        """Whether something the user opened has the content band."""
        return self._controls_open

    def _context_floor(self) -> int:
        """What the situation alone demands of a notice, apart from the one showing."""
        if self._band_taken():
            return _BAND_TAKEN
        if self._interacting():
            return NoticePriority.INTERACTION
        return 0

    def _floor(self) -> int:
        """The priority a notice needs to be shown now; see the module."""
        floor = self._context_floor()
        if self._notice is not None:
            floor = max(floor, self._notice_priority)
        return floor

    def _show(self, notice: Notice, priority: NoticePriority, react: bool) -> None:
        self._notice, self._notice_priority = notice, priority
        self._start_expiry()
        self._publish()
        if react and not self._stopped:
            for listener in list(self._reactions):
                listener(notice)

    def _start_expiry(self) -> None:
        self._cancel_expiry()
        generation = self._generation
        self._expiry = self._scheduler.call_later(
            self._behaviour.notice_duration_ms, lambda: self._expire(generation)
        )

    def _defer(self, notice: Notice, priority: NoticePriority, react: bool) -> None:
        self._deferred_order += 1
        # Enough for every notice ahead to have its turn at the configured pace.
        budget = MAX_DEFERRED_NOTICES * self._behaviour.notice_duration_ms + ELIGIBLE_GRACE_MS
        entry = _Deferred(notice, priority, react, self._deferred_order, budget)
        entry.timer = self._scheduler.call_later(MAX_NOTICE_AGE_MS, lambda: self._drop_deferred(entry))
        self._deferred.append(entry)
        if len(self._deferred) > MAX_DEFERRED_NOTICES:
            self._drop_deferred(min(self._deferred, key=lambda waiting: (waiting.priority, waiting.order)))
        self._arm_eligibility_tick()

    def _eligible(self, entry: _Deferred) -> bool:
        """Nothing outside the queue holds it back: no turn, no band it cannot pass."""
        return entry.priority >= self._context_floor()

    def _arm_eligibility_tick(self) -> None:
        if self._stopped or self._eligibility_tick is not None:
            return
        if any(self._eligible(entry) for entry in self._deferred):
            self._eligibility_tick = self._scheduler.call_later(ELIGIBILITY_TICK_MS, self._count_eligible)

    def _count_eligible(self) -> None:
        """One step of eligible waiting for every notice nothing else holds back."""
        self._eligibility_tick = None
        if self._stopped:
            return
        for entry in list(self._deferred):
            if self._eligible(entry):
                entry.eligible_left_ms -= ELIGIBILITY_TICK_MS
                if entry.eligible_left_ms <= 0:
                    self._drop_deferred(entry)
        self._arm_eligibility_tick()

    def _take_deferred(self, subject: tuple) -> _Deferred | None:
        for entry in self._deferred:
            if _subject(entry.notice) == subject:
                self._drop_deferred(entry)
                return entry
        return None

    def _drop_deferred(self, entry: _Deferred) -> None:
        if entry in self._deferred:
            self._deferred.remove(entry)
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None

    def _yield_to_context(self) -> None:
        """A conversation starting, or the controls opening, takes the band from a lesser notice."""
        if self._notice is None or self._notice_priority >= self._context_floor():
            return
        notice, priority = self._notice, self._notice_priority
        self._cancel_expiry()
        self._notice = None
        self._defer(notice, priority, react=False)

    def _restore(self) -> None:
        """Show the best waiting notice if the floor now lets it; see the module."""
        if self._stopped or not self._deferred:
            return
        best = max(self._deferred, key=lambda entry: (entry.priority, -entry.order))
        if best.priority >= self._floor():
            self._drop_deferred(best)
            self._show(best.notice, best.priority, best.react)
        self._arm_eligibility_tick()

    # --- Resolution ----------------------------------------------------------

    def _reasons(self) -> frozenset[ExpansionReason]:
        reasons = set()
        if self._hovered and self._behaviour.expand_on_hover:
            reasons.add(ExpansionReason.HOVER)
        if self._pinned:
            reasons.add(ExpansionReason.PINNED)
        if self._notice is not None and self._behaviour.expands_for(self._notice.category):
            reasons.add(ExpansionReason.NOTICE)
        if self._controls_open:
            reasons.add(ExpansionReason.CONTROLS)
        if self._listening:
            reasons.add(ExpansionReason.LISTENING)
        return frozenset(reasons)

    def _resolve(self) -> NotchPresentation:
        reasons = self._reasons()
        if self._base is NotchPresence.HIDDEN:
            presence = NotchPresence.HIDDEN
        else:
            presence = NotchPresence.EXPANDED if reasons else NotchPresence.COMPACT
        return NotchPresentation(
            presence=presence,
            activity=self._activity,
            notice=self._notice,
            reasons=reasons,
            actions=self._actions,
            tasks_running=self._tasks_running,
            controls=control_actions(self._actions) if self._controls_open else (),
            listening=self._listening,
            capturing=self._capturing,
        )

    def _publish(self) -> None:
        presentation = self._resolve()
        if presentation == self._presentation:
            return
        self._presentation = presentation
        if self._stopped:
            return
        for listener in list(self._listeners):
            listener(presentation)

    def _expire(self, generation: int) -> None:
        if generation != self._generation or self._stopped:
            return
        self._expiry = None
        self._notice = None
        self._restore()
        self._publish()

    def _cancel_expiry(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        self._generation += 1
