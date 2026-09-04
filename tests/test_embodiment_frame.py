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
    controller.current_offset_x = controller.target_offset_x
    controller.current_offset_y = controller.target_offset_y
    controller.current_width_scale = controller.target_width_scale
    controller.current_height_scale = controller.target_height_scale
    controller.current_corner_radius = controller.target_corner_radius
    controller.current_eyelid_tired = controller.target_eyelid_tired
    controller.current_eyelid_angry = controller.target_eyelid_angry
    controller.current_eyelid_happy = controller.target_eyelid_happy

    assert controller.get_frame() == frame_from_definition(controller.profile.to_definition())


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


def test_resolver_output_materially_drives_controller_targets(monkeypatch):
    sentinel = EmbodimentFrame(
        left_eye=EyeFrame(
            offset_x=0.1,
            offset_y=0.2,
            width_scale=1.3,
            height_scale=0.7,
            corner_radius=0.05,
            tired_lid=0.11,
            angry_lid=0.12,
            happy_lid=0.13,
        ),
        right_eye=EyeFrame(
            offset_x=0.1,
            offset_y=0.2,
            width_scale=1.3,
            height_scale=0.7,
            corner_radius=0.05,
            tired_lid=0.11,
            angry_lid=0.12,
            happy_lid=0.13,
        ),
    )
    monkeypatch.setattr(
        "mira.ui.face.face_controller.resolve_embodiment_frame",
        lambda _intent, _definitions: sentinel,
    )

    controller = FaceController()

    assert controller.base_frame is sentinel
    assert controller.target_offset_x == 70.0
    assert controller.target_offset_y == 90.0
    assert controller.target_width_scale == 1.3
    assert controller.target_height_scale == controller.profile.height_scale
    assert controller.target_corner_radius == 35.0
    assert controller.target_eyelid_tired == 0.11
    assert controller.target_eyelid_angry == 0.12
    assert controller.target_eyelid_happy == 0.13
