"""What the application graph is, and how it is wired together.

Characterization first, refactor second. Every assertion here was written
against the pre-refactor code, where `MainWindow.__init__` constructed the whole
graph itself, and was green before `mira/application/` existed. The refactor then
had to keep them green. Only `BUILD_GRAPH` below changed — it names the new
entry point — so a diff of this file shows exactly which observable facts moved
(none) and which construction call did.

Two mechanisms, because the two questions differ. What the *production* graph is
wired to has to be observed with the production adapter, so it is built in a
subprocess with the offscreen Qt platform — the idiom `test_qt_scheduler_adapter.py`
already uses, since `QtScheduler` binds its home thread at construction and
`MainWindow` is a `QMainWindow`. How the graph *behaves* needs neither: injecting
`ManualScheduler` at the composition seam yields the same graph with no
`QApplication` at all, in-process and deterministic.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mira.application.composition import Application, build_application
from mira.domain.scheduler import ManualScheduler
from mira.domain.state import FaceState

REPO_ROOT = Path(__file__).resolve().parents[1]

# The one line the refactor changed. Before it read:
#     from mira.ui.main_window import MainWindow
#     window = MainWindow()
#     graph, bus = window, window.event_bus
BUILD_GRAPH = """
from mira.application.composition import build_application
from mira.ui.main_window import MainWindow

application = build_application()
window = MainWindow(application)
graph, bus = application, application.event_bus
"""

# Reports the wiring as data. Kept separate from the assertions so the same
# observations can be read by a human when one of them fails.
PROBE = """
import json, sys
from PySide6.QtWidgets import QApplication

app = QApplication([])

BUILD_GRAPH

def describe(callback):
    owner = getattr(callback, "__self__", None)
    name = getattr(callback, "__name__", repr(callback))
    if owner is None:
        return "<callable>"
    return f"{type(owner).__name__}.{name}"

print("---JSON---")
print(json.dumps({
    "subscribers": {
        event: [describe(c) for c in callbacks]
        for event, callbacks in sorted(bus._subscribers.items())
    },
    "types": {
        "event_bus": type(graph.event_bus).__name__,
        "activity": type(graph.activity).__name__,
        "scheduler": type(graph.scheduler).__name__,
        "brain": type(graph.brain).__name__,
        "interaction_manager": type(graph.interaction_manager).__name__,
        "embodied_behavior": type(graph.embodied_behavior).__name__,
    },
    "shared_instances": {
        "brain.event_bus": graph.brain.event_bus is graph.event_bus,
        "brain.activity": graph.brain.activity is graph.activity,
        "brain.scheduler": graph.brain.scheduler is graph.scheduler,
        "embodied.event_bus": graph.embodied_behavior.event_bus is graph.event_bus,
        "embodied.activity": graph.embodied_behavior.activity is graph.activity,
        "embodied.scheduler": graph.embodied_behavior.scheduler is graph.scheduler,
        "interaction.event_bus": graph.interaction_manager.event_bus is graph.event_bus,
        "interaction.activity": graph.interaction_manager.activity is graph.activity,
        "activity.state_manager.event_bus": graph.activity.state_manager.event_bus is graph.event_bus,
    },
    "brain_owns": {
        "memory": type(graph.brain.memory).__name__,
        "intent_engine": type(graph.brain.intent_engine).__name__,
        "response_builder": type(graph.brain.response_builder).__name__,
        "action_registry": type(graph.brain.action_registry).__name__,
        "action_executor": type(graph.brain.action_executor).__name__,
    },
    "initial_state": graph.activity.current().name,
    "pending_timers": graph.scheduler.pending_timers(),
    "pending_completions": graph.scheduler.pending_completions(),
    "window_reaches_brain": window.brain is graph.brain,
}))
"""


def _build_and_report() -> dict:
    source = PROBE.replace("BUILD_GRAPH", BUILD_GRAPH)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("MIRA_INTENT_ENGINE", None)  # the default engine is what is characterized
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, f"building the graph failed:\n{result.stderr[-3000:]}"
    _, _, payload = result.stdout.partition("---JSON---")
    return json.loads(payload)


@pytest.fixture(scope="module")
def graph() -> dict:
    """One subprocess for the whole module: constructing it is the slow part."""
    return _build_and_report()


@pytest.fixture
def manual_scheduler() -> ManualScheduler:
    """The deterministic `Scheduler` from the domain port, used as the double."""
    return ManualScheduler()


@pytest.fixture
def qt_free_application(manual_scheduler: ManualScheduler) -> Application:
    """The real graph, built with the port's reference implementation.

    Function-scoped: these tests drive state and subscribe to the bus, and a
    shared graph would leak both into the next test.
    """
    return build_application(scheduler=manual_scheduler)


# --- what gets built ----------------------------------------------------


def test_the_graph_is_built_from_the_production_implementations(graph):
    """Composition picks the concrete adapters; nothing else chooses for it."""
    assert graph["types"] == {
        "event_bus": "EventBus",
        "activity": "ActivityAuthority",
        "scheduler": "QtScheduler",
        "brain": "Brain",
        "interaction_manager": "InteractionManager",
        "embodied_behavior": "EmbodiedBehavior",
    }


def test_brain_still_owns_the_collaborators_it_constructs_itself(graph):
    """`Brain.__init__` builds these five (mira/core/brain.py:65-74).

    Pinned because the composition root deliberately did *not* take them over:
    injecting them would change `Brain`'s constructor, and this tranche moves
    wiring without redesigning ownership inside a component.
    """
    assert graph["brain_owns"] == {
        "memory": "SessionMemory",
        "intent_engine": "RuleIntentEngine",
        "response_builder": "ResponseBuilder",
        "action_registry": "ActionRegistry",
        "action_executor": "ActionExecutor",
    }


# --- how it is wired ----------------------------------------------------


def test_every_collaborator_shares_one_bus_one_state_manager_one_scheduler(graph):
    """Aliasing is the load-bearing part of composition.

    Two buses, or two schedulers, would leave the application running with
    events that reach nobody and timers on a scheduler nothing drives — and
    every component would still construct successfully.
    """
    assert graph["shared_instances"] == {name: True for name in graph["shared_instances"]}


def test_subscribers_are_registered_exactly_once_in_a_fixed_order(graph):
    """`EventBus.emit` fans out in registration order (mira/messaging/events.py:12-14).

    Registration order is therefore observable behaviour, not an implementation
    detail, and construction order is what determines it. Pinned in full: a
    duplicate registration would run a handler twice per event, which the count
    alone would not catch if the order still looked right.
    """
    assert graph["subscribers"] == {
        "action_completed": ["MainWindow.on_action_completed"],
        "action_failed": ["MainWindow.on_action_failed"],
        "action_started": ["MainWindow.on_action_started"],
        "input_focused": [
            "InteractionManager.on_input_focused",
            "EmbodiedBehavior.on_input_focused",
        ],
        "input_text_changed": [
            "InteractionManager.on_input_text_changed",
            "EmbodiedBehavior.on_input_text_changed",
        ],
        "input_unfocused": [
            "InteractionManager.on_input_unfocused",
            "EmbodiedBehavior.on_input_unfocused",
        ],
        "intent_inferred": ["EmbodiedBehavior.on_intent_inferred"],
        "processing_started": ["InteractionManager.on_processing_started"],
        "response_ready": [
            "InteractionManager.on_response_ready",
            "EmbodiedBehavior.on_response_ready",
        ],
        "state_changed": ["MainWindow.on_state_changed"],
        "user_input_received": ["InteractionManager.on_user_input_received"],
    }


def test_the_interaction_manager_subscribes_before_the_embodied_layer(graph):
    """Called out separately because it is the one order a reader would question.

    Both react to `response_ready` and to the three input events. The order is
    what construction fixed before the refactor, and the composition root keeps
    it; naming it here means a future swap fails a test that says why rather
    than only the wholesale comparison above.
    """
    for event in ("input_focused", "input_unfocused", "input_text_changed", "response_ready"):
        subscribers = graph["subscribers"][event]
        assert subscribers[0].startswith("InteractionManager."), event
        assert subscribers[1].startswith("EmbodiedBehavior."), event


# --- what construction must not do --------------------------------------


def test_construction_has_no_startup_side_effects(graph):
    """Building the graph starts nothing: no state transition, no live timer.

    The application settles at `IDLE` because that is `StateManager`'s initial
    value, not because anything set it — a `set_state(IDLE)` during construction
    would be a no-op (`mira/core/state_manager.py:11-12`) and would not show up
    here, but a transition to anything else would.
    """
    assert graph["initial_state"] == "IDLE"
    assert graph["pending_timers"] == 0
    assert graph["pending_completions"] == 0


def test_the_window_is_wired_to_the_same_graph_that_was_composed(graph):
    """The window uses the composed brain rather than one of its own."""
    assert graph["window_reaches_brain"] is True


# --- the composition seam -----------------------------------------------
#
# Everything below runs in-process, with no Qt event loop, because the seam
# makes that possible. That is the point of the tranche as much as the tidier
# MainWindow is.


def test_the_graph_holds_no_qt_object_when_a_double_is_supplied(qt_free_application):
    """Substituting the scheduler leaves *no* Qt object in the graph.

    An earlier version of this test asserted `QApplication.instance() is None`
    and that the components existed. That could not fail: `QtScheduler()`
    constructs perfectly well with no `QApplication`, so a graph that ignored
    the supplied double and built its own would have passed. Mutation testing
    caught it — handing `EmbodiedBehavior` a real `QtScheduler` left the old
    assertions green.

    This walks the graph instead and refuses any `QObject`, which is the
    property that actually makes the composition seam worth having: reaching
    the graph no longer drags in Qt.
    """
    from PySide6.QtCore import QObject

    assert isinstance(qt_free_application, Application)

    qt_objects = {
        field: value
        for field, value in vars(qt_free_application).items()
        if isinstance(value, QObject)
    }
    assert qt_objects == {}, f"Qt objects reached the substituted graph: {qt_objects}"

    # The scheduler is the one field a Qt implementation would hide behind, and
    # `QtScheduler` is not itself a QObject — so check its type by name too.
    for holder, value in (
        ("application", qt_free_application.scheduler),
        ("brain", qt_free_application.brain.scheduler),
        ("embodied_behavior", qt_free_application.embodied_behavior.scheduler),
    ):
        assert type(value).__name__ == "ManualScheduler", f"{holder} kept {type(value).__name__}"


def test_replacing_the_adapter_needs_no_monkeypatching(qt_free_application, manual_scheduler):
    """The double is passed in, and every component that needs one receives it.

    No patching of module attributes, no reaching into a constructed object.
    Both consumers of the port must end up on the *same* substituted instance,
    or a test would be driving one clock while the graph ran on another.
    """
    from PySide6.QtWidgets import QApplication

    assert qt_free_application.scheduler is manual_scheduler
    assert qt_free_application.brain.scheduler is manual_scheduler
    assert qt_free_application.embodied_behavior.scheduler is manual_scheduler
    # Weak on its own — it says only that *this* test built no QApplication —
    # so it rides along here rather than standing as a test of its own.
    assert QApplication.instance() is None


def test_a_whole_turn_flows_through_the_composed_graph(qt_free_application, manual_scheduler):
    """One user turn, end to end, on the real components.

    This is the non-vacuous half of the file: the assertions above describe the
    shape of the graph, and this one shows the shape carries a turn. The
    sequence also records something only synchronous fan-out produces —
    `state_changed` arrives *before* `user_input_received`, because
    `InteractionManager` is subscribed to `user_input_received` ahead of this
    spy and sets state from inside that handler, so the nested emit completes
    first.
    """
    seen: list[str] = []
    for event in (
        "user_input_received",
        "processing_started",
        "intent_inferred",
        "response_ready",
        "state_changed",
    ):
        qt_free_application.event_bus.subscribe(event, lambda _payload, e=event: seen.append(e))

    replies: list[str] = []
    qt_free_application.brain.process_text_async("che ore sono", lambda r: replies.append(r.text))

    assert seen == ["state_changed", "user_input_received"], "submit is synchronous up to the delay"
    assert replies == [], "nothing is answered before the scheduler runs"

    manual_scheduler.advance(600)
    assert seen[-2:] == ["state_changed", "processing_started"]

    manual_scheduler.run_all()
    assert seen == [
        "state_changed",
        "user_input_received",
        "state_changed",
        "processing_started",
        "intent_inferred",
        "state_changed",
        "response_ready",
    ]
    assert len(replies) == 1
    assert qt_free_application.activity.current() is FaceState.SPEAKING


def test_building_the_graph_twice_shares_nothing(manual_scheduler):
    """Two applications are two graphs.

    A module-level singleton or a cached bus would make the second application
    quietly reuse the first one's subscribers, so a second window would render
    the first window's events.
    """
    first = build_application(scheduler=manual_scheduler)
    second = build_application(scheduler=manual_scheduler)

    assert first.event_bus is not second.event_bus
    assert first.activity is not second.activity
    assert first.brain is not second.brain


# --- what MainWindow may no longer do -----------------------------------


FORBIDDEN_IN_THE_WINDOW = (
    "EventBus",
    "StateManager",
    "QtScheduler",
    "Brain",
    "InteractionManager",
    "EmbodiedBehavior",
    "SessionMemory",
    "ActionExecutor",
    "ActionRegistry",
)


def _main_window_tree() -> ast.AST:
    source = (REPO_ROOT / "mira" / "ui" / "main_window.py").read_text(encoding="utf-8")
    return ast.parse(source)


def test_the_window_constructs_no_core_or_cognitive_subsystem():
    """Read as source, not as behaviour: the window must not *call* these.

    A behavioural check would pass just as well if the window built a second
    brain and never used it. This fails on the construction itself.
    """
    called: list[str] = []
    for node in ast.walk(_main_window_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in FORBIDDEN_IN_THE_WINDOW:
            called.append(f"{name}() at line {node.lineno}")

    assert called == [], f"MainWindow still constructs subsystems: {called}"


def test_the_window_does_not_import_the_layers_it_used_to_build_from():
    """`mira.ui` no longer reaches messaging, core or adapters.

    `scripts/check_layering.py` enforces this for the whole package; this states
    it for the one module the tranche was about, so the reason a future import
    fails is legible here too.
    """
    imported: list[str] = []
    for node in ast.walk(_main_window_tree()):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)

    forbidden = [
        module
        for module in imported
        if module.startswith(("mira.core", "mira.adapters", "mira.messaging"))
    ]
    assert forbidden == [], f"MainWindow still imports: {forbidden}"
    assert "mira.application.composition" in imported, "it should receive the built graph"
