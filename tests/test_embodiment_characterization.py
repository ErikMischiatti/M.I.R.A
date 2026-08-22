"""Characterize the mixed FaceState contract before splitting its vocabulary.

The end-to-end presentation tests were added and run green on main before the
vocabulary existed. The final authority integration test uses the new semantic
API to prove that the same legacy overwrite remains visible through the bridge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mira.application.composition import build_application
from mira.domain.embodiment import AffectState
from mira.domain.scheduler import ManualScheduler
from mira.domain.state import FaceState
from mira.ui.face.expression_library import DEFAULT_EXPRESSION_LIBRARY


REPO_ROOT = Path(__file__).resolve().parents[1]


# Primary semantic role plus normal-production reachability. TIRED and ANGRY
# remain available to DebugPanel and keyboard preview, so they are not globally
# unreachable; they are unreachable only from the normal cognitive/runtime path.
EXPECTED_CLASSIFICATION = {
    FaceState.IDLE: ("ACTIVITY", True),
    FaceState.LISTENING: ("ACTIVITY", True),
    FaceState.THINKING: ("ACTIVITY", True),
    FaceState.SPEAKING: ("MIXED", True),
    FaceState.HAPPY: ("AFFECT", True),
    FaceState.CONFUSED: ("AFFECT", True),
    FaceState.TIRED: ("PRESENTATION_ONLY", False),
    FaceState.ANGRY: ("PRESENTATION_ONLY", False),
}


def state_names(states: list[FaceState]) -> list[str]:
    return [state.name for state in states]


def observed_application():
    scheduler = ManualScheduler()
    application = build_application(scheduler=scheduler)
    observed: list[FaceState] = []
    application.event_bus.subscribe(
        "state_changed", lambda payload: observed.append(payload["new_state"])
    )
    return application, scheduler, observed


def test_every_legacy_face_state_has_an_explicit_classification():
    assert set(EXPECTED_CLASSIFICATION) == set(FaceState)
    assert {role for role, _reachable in EXPECTED_CLASSIFICATION.values()} == {
        "ACTIVITY",
        "AFFECT",
        "MIXED",
        "PRESENTATION_ONLY",
    }
    assert all(role != "UNREACHABLE" for role, _ in EXPECTED_CLASSIFICATION.values())


def test_every_legacy_face_state_selects_one_expression_profile():
    assert set(DEFAULT_EXPRESSION_LIBRARY) == set(FaceState)
    for state, profile in DEFAULT_EXPRESSION_LIBRARY.items():
        assert profile.name == state.name


@pytest.mark.parametrize(
    "text,expected,decay_ms",
    [
        ("che ore sono", ["LISTENING", "THINKING", "SPEAKING"], 1800),
        ("ciao", ["LISTENING", "THINKING", "HAPPY"], 2200),
        ("zzzz qqq", ["LISTENING", "THINKING", "CONFUSED"], 1600),
    ],
)
def test_response_presentation_overwrites_thinking_then_decays(text, expected, decay_ms):
    application, scheduler, observed = observed_application()

    application.brain.process_text_async(text, lambda _response: None)
    scheduler.advance(600)
    scheduler.run_all()
    assert state_names(observed) == expected

    scheduler.advance(decay_ms - 1)
    assert state_names(observed) == expected
    scheduler.advance(1)
    assert state_names(observed) == [*expected, "IDLE"]


def test_engagement_wins_when_an_affect_decays():
    application, scheduler, observed = observed_application()
    application.event_bus.emit("input_focused")
    application.brain.process_text_async("ciao", lambda _response: None)
    scheduler.advance(600)
    scheduler.run_all()
    scheduler.advance(2200)

    assert state_names(observed) == ["LISTENING", "THINKING", "HAPPY", "LISTENING"]


def test_a_new_activity_request_overwrites_a_visible_affect():
    application, _scheduler, observed = observed_application()
    application.activity.express(AffectState.HAPPY)
    application.activity.deliberate()

    assert state_names(observed) == ["HAPPY", "THINKING"]


def test_debug_overrides_present_directly_without_committing_application_state():
    source = (REPO_ROOT / "mira" / "ui" / "debug_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]

    assert "set_state" in calls
    assert "attend" not in calls
    assert "deliberate" not in calls
    assert "express" not in calls
    assert "conclude" not in calls
