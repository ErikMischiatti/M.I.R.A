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

    HOVER   the pointer is over the notch (if `expand_on_hover`)
    PINNED  someone explicitly holds it open (tuning preview)
    NOTICE  a notice whose category expands automatically is showing

    base HIDDEN               -> HIDDEN, whatever the reasons
    base COMPACT, no reasons  -> COMPACT
    base COMPACT, any reason  -> EXPANDED

so hover ending while a notice still shows keeps it expanded, a notice
expiring while the pointer is still there keeps it expanded, and the notch
only collapses when the last reason goes.

Notices have a lifetime. `present` shows a notice for
`NotchBehaviour.notice_duration_ms`, timed by the injected `Scheduler` port —
the application's own scheduler in production, a `ManualScheduler` in tests —
so there is exactly one timer and nothing else ever collapses the notch.
Replacement policy: the latest notice replaces the one showing and gets the
full duration; there is no queue. A notice whose category does not expand
automatically still becomes the content for its lifetime (visible if the
notch is open for another reason), but adds no expansion reason.

Activity alone never expands the notch. It only chooses the content shown
when the notch is already expanded and no notice is showing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import Scheduler, TimerHandle
from mira.ui.notch.presence import NotchPresence


class ExpansionReason(StrEnum):
    """Why the notch is expanded; it stays expanded while any one holds."""

    HOVER = "hover"
    PINNED = "pinned"
    NOTICE = "notice"


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


class NotchAction(StrEnum):
    """User intents the notch can raise; its owner decides what they do."""

    OPEN_MAIN_WINDOW = "open main window"
    OPEN_SETTINGS = "open settings"
    QUIT = "quit"


@dataclass(frozen=True, slots=True)
class NotchContent:
    """What the expanded notch shows under the eyes: a symbol, a short label."""

    symbol: NotchSymbol
    label: str | None = None


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

# Used when a notice brings no text of its own. A neutral device or task
# notice without text is left to its symbol.
_TONE_LABEL = {
    NoticeTone.SUCCESS: "Done",
    NoticeTone.WARNING: "Warning",
    NoticeTone.ERROR: "Error",
}


def notice_content(notice: Notice) -> NotchContent:
    symbol = _TONE_SYMBOL.get(notice.tone) or _CATEGORY_SYMBOL[notice.category]
    text = (notice.text or "").strip() or None
    return NotchContent(symbol, text if text is not None else _TONE_LABEL.get(notice.tone))


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

    @property
    def content(self) -> NotchContent | None:
        """A showing notice wins over the activity."""
        if self.notice is not None:
            return notice_content(self.notice)
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
        self._notice: Notice | None = None
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

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    # --- Inputs ------------------------------------------------------------

    def show(self) -> None:
        self._base = NotchPresence.COMPACT
        self._publish()

    def hide(self) -> None:
        # A hidden notch cannot be under the pointer; never carry a stale
        # hover into the next show.
        self._base = NotchPresence.HIDDEN
        self._hovered = False
        self._publish()

    def set_activity(self, activity: ActivityState) -> None:
        self._activity = activity
        self._publish()

    def set_hovered(self, hovered: bool) -> None:
        self._hovered = bool(hovered)
        self._publish()

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = bool(pinned)
        self._publish()

    def set_behaviour(self, behaviour: NotchBehaviour) -> None:
        """New preferences apply at once; a new duration from the next notice."""
        self._behaviour = behaviour
        self._publish()

    def present(self, notice: Notice) -> None:
        """Show ``notice`` for the configured duration, replacing any other."""
        if self._stopped:
            return
        self._cancel_expiry()
        self._notice = notice
        self._generation += 1
        generation = self._generation
        self._expiry = self._scheduler.call_later(
            self._behaviour.notice_duration_ms, lambda: self._expire(generation)
        )
        self._publish()

    def dismiss_notice(self) -> None:
        self._cancel_expiry()
        self._notice = None
        self._publish()

    def shutdown(self) -> None:
        self._cancel_expiry()
        self._stopped = True

    # --- Resolution ----------------------------------------------------------

    def _reasons(self) -> frozenset[ExpansionReason]:
        reasons = set()
        if self._hovered and self._behaviour.expand_on_hover:
            reasons.add(ExpansionReason.HOVER)
        if self._pinned:
            reasons.add(ExpansionReason.PINNED)
        if self._notice is not None and self._behaviour.expands_for(self._notice.category):
            reasons.add(ExpansionReason.NOTICE)
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
        self._publish()

    def _cancel_expiry(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        self._generation += 1
