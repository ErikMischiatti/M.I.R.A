"""Who may commit a face-state transition, and what the transitions are.

Characterization first. Every sequence in the first half of this file was
recorded against the pre-refactor code, where `Brain`, `InteractionManager` and
`EmbodiedBehavior` each called `StateManager.set_state` directly, and was green
before `ActivityAuthority` existed. The refactor had to keep them green, so they
are the evidence that centralizing the commit changed no observable behaviour.

The second half is about the authority itself: that it is the only production
component committing a transition, and that the components which used to commit
now request instead.

Everything runs on `ManualScheduler` through the real composition root, so the
sequences below are the ones the application actually produces — not a model of
them.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from layering_harness import run_checker

from mira.application.composition import build_application
from mira.domain.scheduler import ManualScheduler
from mira.domain.state import FaceState

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "mira"


@pytest.fixture
def app():
    """The real graph on an explicit clock, with the transitions it emits."""
    scheduler = ManualScheduler()
    application = build_application(scheduler=scheduler)
    observed: list[FaceState] = []
    application.event_bus.subscribe(
        "state_changed", lambda payload: observed.append(payload["new_state"])
    )
    return application, scheduler, observed


def names(states: list[FaceState]) -> list[str]:
    return [state.name for state in states]


# --- characterization: the transitions themselves ------------------------


def test_the_application_starts_idle_and_announces_nothing(app):
    """`StateManager` begins at IDLE, and construction emits no transition."""
    application, _scheduler, observed = app
    assert application.activity.current() is FaceState.IDLE
    assert observed == []


def test_focus_and_text_drive_listening_and_idle(app):
    """Input engagement is the whole trigger; no turn is involved."""
    application, _scheduler, observed = app
    bus = application.event_bus

    bus.emit("input_focused")
    assert names(observed) == ["LISTENING"]

    bus.emit("input_unfocused")
    assert names(observed) == ["LISTENING", "IDLE"]

    bus.emit("input_text_changed", "ciao")
    bus.emit("input_text_changed", "")
    assert names(observed) == ["LISTENING", "IDLE", "LISTENING", "IDLE"]


def test_clearing_the_text_while_still_focused_stays_listening(app):
    """The `or input_has_focus` disjunct, which nothing else covered.

    `on_input_text_changed` asks `input_has_text or input_has_focus`
    (`mira/core/interaction_manager.py:60`). Every other test here unfocuses
    before touching text, so `input_has_focus` is False throughout and dropping
    the disjunct changes nothing they observe — a mutation run proved exactly
    that. With the cursor still in the box, losing it drops the face to IDLE
    while the user is plainly still there.
    """
    application, _scheduler, observed = app
    bus = application.event_bus

    bus.emit("input_focused")
    bus.emit("input_text_changed", "ciao")
    bus.emit("input_text_changed", "")

    assert names(observed) == ["LISTENING"], "focus alone keeps it listening"
    assert application.activity.current() is FaceState.LISTENING


def test_affect_is_visible_before_the_response_overwrites_it(app):
    """`express` must commit at `intent_inferred`, not merely agree with the reply.

    The response carries the same `face_state` the affect reaction chose, so a
    later `conclude` commits the identical value and `StateManager` absorbs it.
    Asserting only the final sequence therefore cannot tell a working `express`
    from one that does nothing — a no-op mutation survived that check. Sampling
    the state *inside* the `intent_inferred` fan-out is what distinguishes them.
    """
    application, scheduler, _observed = app
    sampled: list[FaceState] = []
    application.event_bus.subscribe(
        "intent_inferred", lambda _intent: sampled.append(application.activity.current())
    )

    application.brain.process_text_async("ciao", lambda response: None)
    scheduler.advance(600)
    scheduler.run_all()

    assert sampled == [FaceState.HAPPY], "affect is committed during intent_inferred"


def test_a_full_turn_runs_listening_thinking_speaking(app):
    """The turn's activity arc, on an action-backed intent."""
    application, scheduler, observed = app

    application.brain.process_text_async("che ore sono", lambda response: None)
    assert names(observed) == ["LISTENING"], "listening is committed before the delay"

    scheduler.advance(600)
    assert names(observed) == ["LISTENING", "THINKING"]

    scheduler.run_all()
    assert names(observed) == ["LISTENING", "THINKING", "SPEAKING"]


def test_executing_an_action_commits_no_transition_of_its_own(app):
    """There is no EXECUTING state.

    `ActionExecutor` only emits `action_started`/`action_completed`/
    `action_failed` (`mira/actions/action_executor.py:118-120`); nothing
    subscribes them to a transition, and the UI renders them as chat status
    text. Pinned because "add an EXECUTING state" is the obvious next idea, and
    this records that today's behaviour does not have one.
    """
    application, scheduler, observed = app
    application.brain.process_text_async("che ore sono", lambda response: None)
    scheduler.advance(600)
    scheduler.run_all()

    # get_time really did run: the arc is exactly the turn's, with nothing extra.
    assert names(observed) == ["LISTENING", "THINKING", "SPEAKING"]

    before = list(observed)
    application.event_bus.emit("action_started", None)
    application.event_bus.emit("action_completed", None)
    application.event_bus.emit("action_failed", None)
    assert observed == before, "action events are notifications, not transitions"


def test_the_expressive_state_decays_back_to_idle(app):
    """`EmbodiedBehavior` holds the response state, then returns to neutral."""
    application, scheduler, observed = app
    application.brain.process_text_async("che ore sono", lambda response: None)
    scheduler.advance(600)
    scheduler.run_all()

    scheduler.advance(5000)
    assert names(observed) == ["LISTENING", "THINKING", "SPEAKING", "IDLE"]


def test_decay_returns_to_listening_while_the_input_is_engaged(app):
    """The same decay resolves differently when the user is still engaged."""
    application, scheduler, observed = app
    application.event_bus.emit("input_focused")
    application.brain.process_text_async("ciao", lambda response: None)
    scheduler.advance(600)
    scheduler.run_all()
    scheduler.advance(5000)

    assert names(observed) == ["LISTENING", "THINKING", "HAPPY", "LISTENING"]


@pytest.mark.parametrize(
    "text,affect",
    [("ciao", "HAPPY"), ("zzzz qqq", "CONFUSED")],
    ids=["greeting-is-happy", "unknown-is-confused"],
)
def test_intent_drives_an_affect_transition_before_the_response(app, text, affect):
    """`EmbodiedBehavior.on_intent_inferred` commits affect, not activity.

    The affect value reaches the same variable as the activity states, which is
    the reason the authority owns both commits — see `mira/core/activity
    _authority.py` and the classification in its docstring.
    """
    application, scheduler, observed = app
    application.brain.process_text_async(text, lambda response: None)
    scheduler.advance(600)
    scheduler.run_all()
    scheduler.advance(5000)

    assert names(observed) == ["LISTENING", "THINKING", affect, "IDLE"]


def test_a_second_message_mid_turn_recommits_listening(app):
    """The one case where two writers genuinely disagreed, pinned.

    `InteractionManager.on_user_input_received` returns early while
    `is_processing` is true (`mira/core/interaction_manager.py:62-67`), so it
    does *not* ask for LISTENING here. `Brain` asks unconditionally
    (`mira/core/brain.py:161`), and that is what produces the second LISTENING
    below. Before the authority existed the two were separate `set_state` calls
    and the outcome depended on which ran; the authority keeps the same outcome
    by keeping both requests.
    """
    application, scheduler, observed = app

    application.brain.process_text_async("che ore sono", lambda response: None)
    scheduler.advance(600)
    assert names(observed) == ["LISTENING", "THINKING"], "first turn is mid-flight"

    # Arrives while `is_processing` is true. LISTENING is committed anyway.
    application.brain.process_text_async("che giorno e", lambda response: None)
    assert names(observed) == ["LISTENING", "THINKING", "LISTENING"]

    # The first turn's result is now stale and is dropped; the second turn's
    # own listening delay then carries it into THINKING.
    scheduler.run_all()
    scheduler.advance(600)
    assert names(observed) == ["LISTENING", "THINKING", "LISTENING", "THINKING"]


def test_the_processing_guard_keeps_the_interaction_layer_silent_mid_turn(app):
    """The `is_processing` early return, observed where it is actually visible.

    The test above cannot see this guard: `Brain` re-attends unconditionally and
    `StateManager` dedups, so removing the guard leaves the state *names*
    identical — a mutation run confirmed it. What does change is the order in
    which work happens: with the guard, `InteractionManager` commits nothing
    during `user_input_received`, so the LISTENING transition arrives after the
    event rather than nested inside it.
    """
    application, scheduler, _observed = app
    trace: list[str] = []
    bus = application.event_bus
    bus.subscribe("user_input_received", lambda _payload: trace.append("user_input_received"))
    bus.subscribe("state_changed", lambda payload: trace.append(f"state:{payload['new_state'].name}"))

    application.brain.process_text_async("che ore sono", lambda response: None)
    scheduler.advance(600)
    trace.clear()

    # Mid-turn: `is_processing` is true, so the interaction layer abstains and
    # the nested commit that would otherwise precede the event does not happen.
    application.brain.process_text_async("che giorno e", lambda response: None)
    assert trace == ["user_input_received", "state:LISTENING"]


def test_a_repeated_transition_is_not_re_announced(app):
    """`StateManager` drops a no-op transition, so requests can be redundant.

    This is what makes the duplicated requests above harmless, and it is why
    centralizing the commit could not change the observable sequence.
    """
    application, _scheduler, observed = app
    bus = application.event_bus

    bus.emit("input_focused")
    bus.emit("input_focused")
    bus.emit("input_text_changed", "ciao")
    assert names(observed) == ["LISTENING"]


# --- the authority -------------------------------------------------------


CHECKER = REPO_ROOT / "scripts" / "check_state_authority.py"
THE_AUTHORITY = PACKAGE_ROOT / "core" / "activity_authority.py"


# `run_checker` is the one in `layering_harness`, not a fourth copy of it: the
# contract is identical — a checker that derives its root from its own location,
# so the pair must travel together — and commit 476293a exists to stop exactly
# this helper being re-declared per module.


def isolated_tree(tmp_path: Path, module_path: str, source: str) -> Path:
    """A throwaway package holding the checker, a real authority, and one module."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(CHECKER, tmp_path / "scripts" / "check_state_authority.py")

    authority = tmp_path / "mira" / "core" / "activity_authority.py"
    authority.parent.mkdir(parents=True)
    shutil.copy(THE_AUTHORITY, authority)

    target = tmp_path / module_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return tmp_path / "scripts" / "check_state_authority.py"


def test_the_repository_has_exactly_one_committer():
    """The real tree passes its own rule."""
    result = run_checker(REPO_ROOT, CHECKER)
    assert result.returncode == 0, result.stderr
    assert "only committer" in result.stdout


@pytest.mark.parametrize(
    "module_path,source,expected",
    [
        pytest.param(
            "mira/core/offender.py",
            "def go(self):\n    self.state_manager.set_state(1)\n",
            "only the activity authority may commit",
            id="a-component-may-not-commit",
        ),
        pytest.param(
            "mira/ui/offender.py",
            "from mira.core.state_manager import StateManager\n\nUSED = StateManager\n",
            "may not hold a StateManager",
            id="the-ui-may-not-hold-a-state-manager",
        ),
        pytest.param(
            "mira/core/offender.py",
            "from mira.core.state_manager import StateManager\n\nUSED = StateManager\n",
            "may not hold a StateManager",
            id="a-core-component-may-not-hold-a-state-manager",
        ),
        # Rule B's other import forms. Each of these passed the first version of
        # the rule, which matched only the bare name `StateManager`.
        pytest.param(
            "mira/core/offender.py",
            "import mira.core.state_manager as sm\n\nUSED = sm\n",
            "may not hold a StateManager",
            id="a-dotted-import-is-still-holding-one",
        ),
        pytest.param(
            "mira/core/offender.py",
            "from mira.core import state_manager\n\nUSED = state_manager\n",
            "may not hold a StateManager",
            id="a-package-attribute-import-is-still-holding-one",
        ),
        # Rule C: the route the refactor itself opened. Before the authority
        # existed, `self.brain.state_manager.set_state(...)` from the UI was
        # caught by Rule A; renaming the handle to `activity` moved it outside
        # that key, so this rule is what restores the coverage.
        pytest.param(
            "mira/ui/offender.py",
            "def go(self):\n    self.brain.activity.attend()\n",
            "may not request a state transition",
            id="the-ui-may-not-request-a-transition",
        ),
        pytest.param(
            "mira/ui/offender.py",
            "def go(self, application):\n    application.activity.express(1)\n",
            "may not request a state transition",
            id="the-ui-may-not-reach-the-authority-through-the-application",
        ),
        # Rule D: an assignment commits without emitting, so every subscriber
        # keeps rendering the state it last heard about.
        pytest.param(
            "mira/core/offender.py",
            "def go(self, manager):\n    manager.current_state = 1\n",
            "may not assign the state storage directly",
            id="nobody-may-write-the-storage-silently",
        ),
    ],
)
def test_the_checker_rejects_a_bypass(tmp_path, module_path, source, expected):
    """Fault injection: each bypass is planted and must be reported.

    Without these the checker could be silently weakened — an empty rule set
    reports success just as loudly as a satisfied one.
    """
    checker = isolated_tree(tmp_path, module_path, source)
    result = run_checker(tmp_path, checker)

    assert result.returncode == 1, f"the bypass was accepted:\n{result.stdout}"
    assert expected in result.stderr
    # Naming the planted file matters: inverting the checker's own exemption
    # makes the copied authority flag *itself*, which produces the right exit
    # code and the right message while detecting nothing.
    assert module_path in result.stderr, result.stderr


@pytest.mark.parametrize(
    "authority_source,expected",
    [
        pytest.param(None, "no activity authority", id="authority-file-deleted"),
        # The file surviving an edit that removed the class is the likelier
        # accident — a bad merge, or moving the class out and leaving the module.
        pytest.param("VALUE = 1\n", "defines no ActivityAuthority", id="authority-file-gutted"),
    ],
)
def test_the_checker_fails_loudly_without_an_authority(tmp_path, authority_source, expected):
    """No authority must never read as a clean bill of health.

    Both of these used to matter differently: deletion already exited 2, while a
    gutted file printed "State authority OK" and exited 0 — a green run on a
    codebase with no authority at all.
    """
    (tmp_path / "scripts").mkdir()
    shutil.copy(CHECKER, tmp_path / "scripts" / "check_state_authority.py")
    (tmp_path / "mira" / "core").mkdir(parents=True)
    (tmp_path / "mira" / "core" / "harmless.py").write_text("VALUE = 1\n", encoding="utf-8")
    if authority_source is not None:
        (tmp_path / "mira" / "core" / "activity_authority.py").write_text(
            authority_source, encoding="utf-8"
        )

    result = run_checker(tmp_path, tmp_path / "scripts" / "check_state_authority.py")
    assert result.returncode == 2
    assert expected in result.stderr


def test_no_component_still_holds_a_state_manager():
    """Read as source: the three former writers name the authority, not the manager."""
    for module in ("brain.py", "interaction_manager.py", "embodied_behavior.py"):
        source = (PACKAGE_ROOT / "core" / module).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        assert "StateManager" not in imported, module
        assert "ActivityAuthority" in imported, module


def test_composition_wires_one_shared_authority(app):
    """Every requester holds the same authority, and it holds the one manager."""
    application, _scheduler, _observed = app

    assert application.brain.activity is application.activity
    assert application.interaction_manager.activity is application.activity
    assert application.embodied_behavior.activity is application.activity


def test_settle_resolves_by_engagement(app):
    """The one branch the authority owns, exercised directly.

    Three call sites collapsed into `settle`; this is what they now share.
    """
    application, _scheduler, observed = app
    activity = application.activity

    activity.settle(engaged=True)
    activity.settle(engaged=False)
    assert names(observed) == ["LISTENING", "IDLE"]


def test_the_authority_reports_what_was_committed(app):
    """`current()` is how `EmbodiedBehavior` reads state without a manager."""
    application, _scheduler, _observed = app
    activity = application.activity

    assert activity.current() is FaceState.IDLE
    activity.deliberate()
    assert activity.current() is FaceState.THINKING
