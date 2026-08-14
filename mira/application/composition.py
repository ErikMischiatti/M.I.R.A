"""The one place that constructs the MIRA application graph.

Before this module, `MainWindow.__init__` built the graph: it created the event
bus, the state manager, the Qt scheduler, the brain, the interaction manager and
the embodied-behaviour layer, and only then began building widgets. A UI class
therefore decided which scheduler the turn lifecycle ran on, and the graph could
not be assembled at all without a `QMainWindow` — so nothing could inspect it,
and no test could substitute a collaborator without reaching inside the window.

`build_application` moves that decision here and changes nothing else. The
construction order below is the order `MainWindow` used, preserved deliberately
rather than incidentally, because two parts of it are observable:

  - `EventBus.emit` fans out in subscription order
    (`mira/messaging/events.py:12-14`). `InteractionManager` and
    `EmbodiedBehavior` both subscribe to `response_ready` and to the three input
    events, and they subscribe from their constructors, so the order these two
    are built in *is* the order their handlers run in.
  - `StateManager`, the bus and the scheduler are shared by aliasing, not
    rebuilt per component. A second bus or a second scheduler would leave every
    component constructing successfully while events reached nobody and timers
    sat on a scheduler nothing drives.

`scheduler` is the single injection seam, and it exists for two reasons rather
than for symmetry: it is the adapter choice, and it is what lets the graph be
built with no Qt event loop at all. Passing `ManualScheduler` from
`mira.domain.scheduler` yields the real graph, fully wired, driven by an
explicit clock — which is how `tests/test_application_composition.py` exercises
a whole turn without a `QApplication`.

Deliberately plain: a frozen dataclass and one function. No container, no
registry, no service locator, no reflection. Adding a component means adding a
line here and a field below, which is the point — the graph stays readable as
source.
"""

from __future__ import annotations

from dataclasses import dataclass

from mira.adapters.qt_scheduler import QtScheduler
from mira.core.activity_authority import ActivityAuthority
from mira.core.brain import Brain
from mira.core.embodied_behavior import EmbodiedBehavior
from mira.core.interaction_manager import InteractionManager
from mira.core.state_manager import StateManager
from mira.domain.scheduler import Scheduler
from mira.messaging.events import EventBus


@dataclass(frozen=True)
class Application:
    """The constructed graph, held by the object that composed it.

    Frozen because composition happens once: rebinding a field after the fact
    would leave the components wired to the previous value and nothing else
    would notice. It is a record of what was built, not a place to look things
    up — components already hold their own collaborators, injected explicitly.

    The UI needs only `event_bus` and `brain`; the remaining fields are here so
    the graph can be inspected and driven in tests, and so ownership is stated
    in one readable place rather than inferred from constructor arguments.

    `StateManager` is deliberately *not* a field. Exposing it handed any holder
    of this record a live state manager without importing the type, which is
    the one route `scripts/check_state_authority.py` cannot see: its Rule B
    keys on the import, and an aliased receiver defeats Rule A. Reach the state
    through `activity` instead.
    """

    event_bus: EventBus
    activity: ActivityAuthority
    scheduler: Scheduler
    brain: Brain
    interaction_manager: InteractionManager
    embodied_behavior: EmbodiedBehavior


def build_application(*, scheduler: Scheduler | None = None) -> Application:
    """Construct and wire the application graph.

    `scheduler` defaults to the production `QtScheduler`, which binds its home
    thread at construction (`mira/adapters/qt_scheduler.py:143`). That makes the
    *caller's* thread the serialized context, so production must call this from
    the Qt main thread — `mira.main` does, after creating the `QApplication`,
    which is where `MainWindow` used to build it.

    No side effects: nothing is started, no state transition is triggered, and
    no timer is armed. The returned graph is idle until the UI feeds it.
    """
    event_bus = EventBus()
    state_manager = StateManager(event_bus)
    # One authority over the shared face state, shared by every component
    # that used to write it. Two authorities would each commit against the
    # same StateManager and neither would be authoritative.
    activity = ActivityAuthority(state_manager)
    scheduler = QtScheduler() if scheduler is None else scheduler

    brain = Brain(event_bus, activity, scheduler=scheduler)

    # These next two lines are the order-sensitive pair. Both subscribe from
    # their own constructors, and both handle `response_ready` and the three
    # input events, so the order they are built in is the order their handlers
    # run in. `Brain` above subscribes to nothing — it only emits — so its
    # position is free. Interaction first, embodiment second, as before the move.
    interaction_manager = InteractionManager(event_bus, activity)
    embodied_behavior = EmbodiedBehavior(event_bus, activity, scheduler=scheduler)

    return Application(
        event_bus=event_bus,
        activity=activity,
        scheduler=scheduler,
        brain=brain,
        interaction_manager=interaction_manager,
        embodied_behavior=embodied_behavior,
    )
