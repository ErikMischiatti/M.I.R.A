"""Pure frame resolution and compatibility with the existing face values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mira.domain.embodiment import ActivityState, AffectState, EmbodimentIntent, ExpressionKey
from mira.domain.embodiment_frame import (
    EmbodimentFrame,
    EyeFrame,
    FACE_HEIGHT_UNITS,
    FACE_WIDTH_UNITS,
    frame_from_definition,
    resolve_embodiment_frame,
)
from mira.domain.embodiment_playback import PlaybackPose
from mira.domain.state import FaceState
from mira.domain.scheduler import ManualScheduler
from mira.application.composition import build_application
from mira.ui.face.expression_store import load_expression_library
from mira.ui.face.face_controller import FaceController


def definitions():
    return {
        ExpressionKey[state.name]: profile.to_definition()
        for state, profile in load_expression_library().items()
    }


@pytest.mark.parametrize("state", list(FaceState))
def test_current_profile_maps_to_the_same_resolved_pose(state):
    controller = FaceController()
    controller.set_state(state)

    definition = controller.profile.to_definition()
    assert controller.base_frame == frame_from_definition(definition)
    assert controller.target_pose == PlaybackPose.from_definition(definition)


def test_resolution_is_deterministic_and_has_value_semantics():
    intent = EmbodimentIntent(ActivityState.LISTENING, AffectState.HAPPY)
    first = resolve_embodiment_frame(intent, definitions())
    second = resolve_embodiment_frame(intent, definitions())
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        first.left_eye = EyeFrame()  # type: ignore[misc]


@pytest.mark.parametrize(
    "intent,key",
    [
        (EmbodimentIntent(ActivityState.LISTENING), ExpressionKey.LISTENING),
        (EmbodimentIntent(ActivityState.LISTENING, AffectState.HAPPY), ExpressionKey.HAPPY),
        (EmbodimentIntent(ActivityState.THINKING, AffectState.CONFUSED), ExpressionKey.CONFUSED),
        (EmbodimentIntent(ActivityState.IDLE, AffectState.HAPPY), ExpressionKey.HAPPY),
    ],
)
def test_current_activity_and_affect_mapping_selects_the_expected_frame(intent, key):
    library = definitions()
    assert resolve_embodiment_frame(intent, library) == frame_from_definition(library[key])


@pytest.mark.parametrize("key", [ExpressionKey.TIRED, ExpressionKey.ANGRY])
def test_presentation_only_override_resolves_without_new_semantics(key):
    intent = EmbodimentIntent(ActivityState.IDLE, expression=key)
    library = definitions()
    assert resolve_embodiment_frame(intent, library) == frame_from_definition(library[key])


def test_confused_frame_keeps_left_and_right_eye_independent_and_normalized():
    frame = resolve_embodiment_frame(
        EmbodimentIntent(ActivityState.IDLE, expression=ExpressionKey.CONFUSED),
        definitions(),
    )
    assert frame.left_eye.offset_y * FACE_HEIGHT_UNITS == -4.0
    assert frame.right_eye.offset_y * FACE_HEIGHT_UNITS == 8.0
    assert frame.left_eye.height_scale == pytest.approx(0.924)
    assert frame.right_eye.height_scale == pytest.approx(0.7216)
    assert frame.left_eye.corner_radius * FACE_WIDTH_UNITS == 28.0


def test_frame_contains_no_color_timing_or_cognitive_fields():
    assert set(EmbodimentFrame.__dataclass_fields__) == {"left_eye", "right_eye"}
    assert set(EyeFrame.__dataclass_fields__) == {
        "offset_x",
        "offset_y",
        "width_scale",
        "height_scale",
        "corner_radius",
        "closed",
        "tired_lid",
        "angry_lid",
        "happy_lid",
    }


def test_full_turn_delivers_the_same_visual_sequence_as_resolved_frames():
    scheduler = ManualScheduler()
    application = build_application(scheduler=scheduler)
    controller = FaceController()
    observed = []

    def present(payload):
        controller.set_intent(payload["embodiment"])
        observed.append((payload["new_state"], controller.expression_key, controller.base_frame))

    application.event_bus.subscribe("state_changed", present)
    application.brain.process_text_async("che ore sono", lambda _response: None)
    scheduler.advance(600)
    scheduler.run_all()

    assert [(state.name, key.name) for state, key, _frame in observed] == [
        ("LISTENING", "LISTENING"),
        ("THINKING", "THINKING"),
        ("SPEAKING", "SPEAKING"),
    ]
    assert all(isinstance(frame, EmbodimentFrame) for _state, _key, frame in observed)
    library = definitions()
    assert [frame for _state, _key, frame in observed] == [
        frame_from_definition(library[ExpressionKey.LISTENING]),
        frame_from_definition(library[ExpressionKey.THINKING]),
        frame_from_definition(library[ExpressionKey.SPEAKING]),
    ]
    assert len({frame for _state, _key, frame in observed}) == 3


def test_expression_definition_materially_drives_controller_target_pose():
    controller = FaceController()
    controller.profile.offset_x = 70.0
    controller.profile.offset_y = 90.0
    controller.profile.width_scale = 1.3
    controller.profile.height_scale = 0.7
    controller.profile.corner_radius = 35.0
    controller.profile.eyelid_tired = 0.11
    controller.profile.eyelid_angry = 0.12
    controller.profile.eyelid_happy = 0.13

    controller.refresh_profile_targets()

    assert controller.target_pose == PlaybackPose(
        offset_x=70.0,
        offset_y=90.0,
        width_scale=1.3,
        height_scale=0.7,
        corner_radius=35.0,
        eyelid_tired=0.11,
        eyelid_angry=0.12,
        eyelid_happy=0.13,
    )
