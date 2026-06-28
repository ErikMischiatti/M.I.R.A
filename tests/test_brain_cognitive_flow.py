from __future__ import annotations

from mira.core.brain import Brain
from mira.core.events import EventBus
from mira.ui.face.face_state import FaceState


class RecordingStateManager:
    def __init__(self):
        self.current_state = FaceState.IDLE
        self.states = []

    def set_state(self, new_state):
        self.current_state = new_state
        self.states.append(new_state)


class RecordingActionExecutor:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        raise AssertionError("No action should be executed in this cognitive flow")


def make_brain():
    brain = Brain(event_bus=EventBus(), state_manager=RecordingStateManager())
    brain.action_executor = RecordingActionExecutor()
    return brain


def test_full_sync_flow_stores_and_recalls_user_name():
    brain = make_brain()

    first = brain.process_text("mi chiamo Erik")
    second = brain.process_text("come mi chiamo?")

    assert first.text == "Va bene, ti chiamerò Erik."
    assert second.text == "Ti chiami Erik."
    assert brain.memory.get_context_value("user_name") == "Erik"
    assert brain.action_executor.requests == []
    assert second.metadata["selected_intent"] == "ask_user_name"
    assert second.metadata["response_source"] == "deterministic"


def test_full_sync_flow_updates_user_name():
    brain = make_brain()

    brain.process_text("mi chiamo Erik")
    update = brain.process_text("chiamami Marco")
    recall = brain.process_text("come mi chiamo?")

    assert update.text == "Va bene, ti chiamerò Marco."
    assert recall.text == "Ti chiami Marco."
    assert brain.memory.get_context_value("user_name") == "Marco"
    assert brain.action_executor.requests == []


def test_unknown_user_name_flow_does_not_execute_action():
    brain = make_brain()

    response = brain.process_text("come mi chiamo?")

    assert "Non conosco ancora il tuo nome" in response.text
    assert brain.action_executor.requests == []


def test_project_context_is_cognitive_not_desktop_action():
    brain = make_brain()

    response = brain.process_text("di cosa stiamo parlando?")

    assert "M.I.R.A." in response.text
    assert brain.action_executor.requests == []
    assert response.metadata["selected_intent"] == "project_context"
