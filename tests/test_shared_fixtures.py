"""Tests for the shared test infrastructure itself.

The autouse environment fixture and the shared doubles are relied on by most of
the suite, so their own contracts are asserted here rather than assumed. Two
properties of the autouse fixture matter most: that it actually clears the
variables, and that it cannot prevent a test from choosing its own value.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest
from conftest import MIRA_ENV_VARS
from doubles import (
    RecordingActionExecutor,
    RecordingEventBus,
    RecordingResponseBuilder,
    RecordingStateManager,
    StaticIntentEngine,
    make_recording_brain,
)

from mira.actions.action_models import ActionRequest
from mira.domain.models import IntentResult, UserInput
from mira.domain.scheduler import ManualScheduler
from mira.domain.state import FaceState

TESTS_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SHARED_FILES = ("conftest.py", "doubles.py", "layering_harness.py", "test_shared_fixtures.py")


# --- the autouse environment fixture -----------------------------------

AMBIENT = "ambient-value-that-no-test-chose"


@pytest.fixture(scope="module", autouse=True)
def hostile_ambient_environment():
    """Export every MIRA_* variable for this module, standing in for a shell.

    Module scope is deliberate: a higher-scoped fixture is set up before a
    function-scoped one, so these values are in `os.environ` by the time
    `isolate_mira_env` runs, and clearing them becomes observable. In a clean
    shell the assertion below would otherwise hold whether or not
    `isolate_mira_env` did anything.

    Yields the names it actually exported, so a test can require that this ran
    first rather than assume it.
    """
    saved = {name: os.environ.get(name) for name in MIRA_ENV_VARS}
    for name in MIRA_ENV_VARS:
        os.environ[name] = AMBIENT
    yield tuple(name for name in MIRA_ENV_VARS if os.environ.get(name) == AMBIENT)
    for name, previous in saved.items():
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def test_every_mira_variable_is_cleared(hostile_ambient_environment):
    """The fixture's whole purpose, asserted against genuinely exported values."""
    assert hostile_ambient_environment == MIRA_ENV_VARS, (
        "the ambient values were never exported, so this assertion would be vacuous"
    )

    present = [name for name in MIRA_ENV_VARS if name in os.environ]
    assert present == []


def test_the_cleared_list_covers_every_variable_the_code_reads():
    """A new MIRA_* read in production must be added to MIRA_ENV_VARS.

    Derived from the source rather than restated, so the two cannot drift.

    Equality, not subset: a subset assertion also holds when the scan finds
    nothing at all — a renamed package or a broken path would leave it green
    while checking nothing — and it would not notice a name left in
    MIRA_ENV_VARS after its production read was deleted. Single as well as
    double quotes, because `os.getenv('MIRA_X')` is the same read.
    """
    found: set[str] = set()
    for path in (REPO_ROOT / "mira").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        found |= set(re.findall(r"""["'](MIRA_[A-Z_]+)["']""", path.read_text(encoding="utf-8")))

    assert found == set(MIRA_ENV_VARS), (
        f"MIRA_ENV_VARS and the production reads have drifted.\n"
        f"read in mira/ but not cleared: {sorted(found - set(MIRA_ENV_VARS))}\n"
        f"cleared but no longer read:    {sorted(set(MIRA_ENV_VARS) - found)}"
    )


def test_a_test_can_still_choose_its_own_value(monkeypatch):
    """The fixture must not be able to mask a deliberate setting."""
    monkeypatch.setenv("MIRA_INTENT_ENGINE", "llm")
    assert os.environ["MIRA_INTENT_ENGINE"] == "llm"


def test_absent_variables_select_the_production_defaults():
    """All five, so `isolate_mira_env` provably invents no behaviour.

    Each assertion reads the value the production code lands on when its variable
    is absent, and compares it with the constant the production module declares
    as that default — never with a literal restated here.
    """
    from mira.cognition import llm_client, llm_intent_engine
    from mira.core.brain import Brain
    from mira.core.state_manager import StateManager
    from mira.messaging.events import EventBus

    assert [name for name in MIRA_ENV_VARS if name in os.environ] == []

    bus = EventBus()
    brain = Brain(bus, StateManager(bus), scheduler=ManualScheduler())
    # mira/core/brain.py:88 defaults to "rule" when the variable is absent.
    assert type(brain.intent_engine).__name__ == "RuleIntentEngine"

    # Constructing the client performs no request; only generate_structured does.
    client = llm_client.OllamaClient()
    assert client.model == llm_client.DEFAULT_OLLAMA_MODEL
    assert client.base_url == llm_client.DEFAULT_OLLAMA_BASE_URL.rstrip("/")
    assert client.timeout_s == llm_client.DEFAULT_OLLAMA_TIMEOUT_S

    engine = llm_intent_engine.LLMIntentEngine(client=client)
    assert engine.action_min_confidence == llm_intent_engine.DEFAULT_LLM_ACTION_MIN_CONFIDENCE


# --- the references the shared infrastructure documents itself with -----


def test_each_variable_is_read_at_the_line_it_is_attributed_to():
    """`conftest.py` names an os.getenv site per variable; check every one.

    A line reference is only support while it still points at the code it
    claims. Asserting `getenv` is on the cited line is what distinguishes the
    read site from, say, the constant that merely holds the variable's name.
    """
    source = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    attributions = {
        variable: (path, int(line))
        for variable, path, line in re.findall(r"#\s+(MIRA_[A-Z_]+)\s+(\S+\.py):(\d+)", source)
    }

    assert set(attributions) == set(MIRA_ENV_VARS), (
        f"every variable needs an attribution comment; missing "
        f"{sorted(set(MIRA_ENV_VARS) - set(attributions))}"
    )
    for variable, (relative_path, line_number) in sorted(attributions.items()):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        cited = text.splitlines()[line_number - 1]
        assert "getenv" in cited, f"{relative_path}:{line_number} is not a read site: {cited!r}"
        # The read may go through a constant, so the name is checked file-wide.
        assert variable in text, f"{relative_path} never mentions {variable}"


def test_every_cited_source_line_exists_and_is_not_blank():
    """Catch the common rot in the `file.py:LINE` references across these files.

    A line that moved, went blank, or fell off the end of a shrinking file is a
    claim that no longer supports anything.
    """
    pattern = re.compile(r"\b((?:mira|scripts|tests)/[A-Za-z0-9_/]+\.py):(\d+)(?:[-,](\d+))?")
    checked = 0

    for name in SHARED_FILES:
        text = (TESTS_DIR / name).read_text(encoding="utf-8")
        for relative_path, first, last in pattern.findall(text):
            target = REPO_ROOT / relative_path
            assert target.is_file(), f"{name} cites {relative_path}, which does not exist"
            lines = target.read_text(encoding="utf-8").splitlines()

            for number in range(int(first), int(last or first) + 1):
                assert number <= len(lines), (
                    f"{name} cites {relative_path}:{number}, "
                    f"past the end of a {len(lines)}-line file"
                )
                assert lines[number - 1].strip(), (
                    f"{name} cites {relative_path}:{number}, which is blank"
                )
                checked += 1

    assert checked >= 8, f"the references were not found, so nothing was checked (saw {checked})"


def test_the_suite_collects_under_the_importlib_import_mode():
    """`conftest.py` puts this directory on sys.path so both import modes work.

    Without that, `from doubles import ...` and `from conftest import ...` fail
    at collection under `--import-mode=importlib`. Collection only, because
    running the suite inside itself would recurse.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "--import-mode=importlib"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, f"collection failed.\n{result.stdout[-2000:]}"
    # Matching on test names would give false hits, so match the report lines.
    assert "errors during collection" not in result.stdout, result.stdout[-2000:]
    assert "ModuleNotFoundError" not in result.stdout, result.stdout[-2000:]
    assert re.search(r"\n\d+ tests collected", result.stdout), result.stdout[-2000:]


# --- the shared doubles -------------------------------------------------


def test_recording_event_bus_records_and_still_delivers():
    """It must record without replacing real dispatch, or tests would drift."""
    bus = RecordingEventBus()
    delivered: list[object] = []
    bus.subscribe("e", delivered.append)

    bus.emit("e", 1)

    assert bus.emitted == [("e", 1)]
    assert delivered == [1], "the double must delegate to the real bus"


def test_recording_state_manager_records_and_reports_current():
    manager = RecordingStateManager()
    assert manager.get_state() is FaceState.IDLE

    manager.set_state(FaceState.THINKING)
    manager.set_state(FaceState.SPEAKING)

    assert manager.states == [FaceState.THINKING, FaceState.SPEAKING]
    assert manager.get_state() is FaceState.SPEAKING
    assert manager.current_state is FaceState.SPEAKING


def test_static_intent_engine_returns_the_same_intent_and_records_inputs():
    intent = IntentResult(intent="time_query")
    engine = StaticIntentEngine(intent)
    first, second = UserInput(text="a"), UserInput(text="b")

    assert engine.infer(first) is intent
    assert engine.infer(second) is intent
    assert engine.calls == [first, second]


def test_recording_action_executor_records_and_reports_the_action_name():
    executor = RecordingActionExecutor()
    request = ActionRequest("get_time")

    result = executor.execute(request)

    assert executor.requests == [request]
    assert result.success is True
    assert result.action_name == "get_time"
    # test_brain_async_contract.py asserts this text through the response.
    assert result.message == "executed get_time"


def test_recording_response_builder_derives_text_from_the_action_result():
    builder = RecordingResponseBuilder()
    intent = IntentResult(intent="time_query")
    user_input = UserInput(text="che ore sono")
    result = RecordingActionExecutor().execute(ActionRequest("get_time"))

    with_action = builder.build(intent, user_input, result)
    without_action = builder.build(intent, user_input)

    assert with_action.text == "executed get_time"
    assert without_action.text == "no action"
    assert with_action.face_state is FaceState.SPEAKING
    assert with_action.metadata == {"intent": "time_query"}
    assert builder.calls == [
        (intent, user_input, result),
        (intent, user_input, None),
    ]


def test_make_recording_brain_wires_every_collaborator_to_a_double():
    """Two modules share this factory, so what it wires is asserted once here."""
    intent = IntentResult(intent="time_query")
    brain = make_recording_brain(intent)

    assert isinstance(brain.event_bus, RecordingEventBus)
    assert isinstance(brain.state_manager, RecordingStateManager)
    assert isinstance(brain.intent_engine, StaticIntentEngine)
    assert isinstance(brain.response_builder, RecordingResponseBuilder)
    assert isinstance(brain.scheduler, ManualScheduler)
    # Brain builds its own executor, so the double has to replace it afterwards.
    assert isinstance(brain.action_executor, RecordingActionExecutor)
    assert brain.intent_engine.intent is intent


def test_make_recording_brain_returns_an_independent_brain_each_call():
    """Shared factory, unshared state: a recorded turn must not leak forward."""
    first = make_recording_brain(IntentResult(intent="time_query"))
    second = make_recording_brain(IntentResult(intent="greeting"))

    first.state_manager.set_state(FaceState.SPEAKING)
    first.event_bus.emit("response_ready", None)

    assert second.state_manager.states == []
    assert second.event_bus.emitted == []
    assert second.memory.history == []
    assert first.scheduler is not second.scheduler


@pytest.mark.parametrize(
    "construct,recorder",
    [
        pytest.param(RecordingEventBus, "emitted", id="RecordingEventBus"),
        pytest.param(RecordingStateManager, "states", id="RecordingStateManager"),
        pytest.param(RecordingActionExecutor, "requests", id="RecordingActionExecutor"),
        pytest.param(RecordingResponseBuilder, "calls", id="RecordingResponseBuilder"),
        # Takes a constructor argument, hence the lambda; a class-level `calls`
        # would otherwise slip past every other test in this file.
        pytest.param(
            lambda: StaticIntentEngine(IntentResult(intent="time_query")),
            "calls",
            id="StaticIntentEngine",
        ),
    ],
)
def test_doubles_do_not_share_state_between_instances(construct, recorder):
    """Each construction must start clean, or tests would leak into each other.

    The recorder attribute is named per double rather than derived, so adding a
    double to the list forces naming what it records.
    """
    first, second = construct(), construct()
    getattr(first, recorder).append("sentinel")

    assert getattr(second, recorder) == []
