"""Characterize the values the current Qt face consumes before frame extraction."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mira.domain.state import FaceState
from mira.ui.face.face_controller import FaceController


def rendered_eye_values(controller: FaceController, side: str) -> tuple[float, ...]:
    """The renderer's current pre-frame calculation, expressed without Qt."""
    profile = controller.profile
    asymmetry_y = getattr(profile, f"asymmetry_offset_y_{side}")
    asymmetry_height = getattr(profile, f"asymmetry_height_{side}")
    return (
        controller.current_offset_x,
        controller.current_offset_y + asymmetry_y,
        controller.current_width_scale,
        controller.current_height_scale * asymmetry_height,
        controller.current_corner_radius,
        controller.current_eyelid_tired,
        controller.current_eyelid_angry,
        controller.current_eyelid_happy,
        controller.left_eye_closed if side == "left" else controller.right_eye_closed,
    )


@pytest.mark.parametrize("state", list(FaceState))
def test_each_profile_sets_the_exact_current_controller_targets(state):
    controller = FaceController()
    controller.set_state(state)
    profile = controller.expression_library[state]

    assert (
        controller.target_offset_x,
        controller.target_offset_y,
        controller.target_width_scale,
        controller.target_height_scale,
        controller.target_corner_radius,
        controller.target_eyelid_tired,
        controller.target_eyelid_angry,
        controller.target_eyelid_happy,
    ) == (
        profile.offset_x,
        profile.offset_y,
        profile.width_scale,
        profile.height_scale,
        profile.corner_radius,
        profile.eyelid_tired,
        profile.eyelid_angry,
        profile.eyelid_happy,
    )


def test_confused_profile_resolves_independent_left_and_right_eye_geometry():
    controller = FaceController()
    controller.set_state(FaceState.CONFUSED)
    controller.current_offset_y = controller.profile.offset_y
    controller.current_height_scale = controller.profile.height_scale

    left = rendered_eye_values(controller, "left")
    right = rendered_eye_values(controller, "right")

    assert left[1] == -4.0
    assert right[1] == 8.0
    assert left[3] == pytest.approx(0.924)
    assert right[3] == pytest.approx(0.7216)


def test_one_frame_preserves_animation_then_interpolation_then_render_order():
    controller = FaceController()
    controller.set_state(FaceState.SPEAKING)
    controller.blink_interval_frames = 999

    controller.update()

    expected_pulse = controller.profile.height_scale + 0.16 * abs(__import__("math").sin(0.22))
    assert controller.target_height_scale == pytest.approx(expected_pulse)
    assert controller.current_height_scale == pytest.approx(1.0 + (expected_pulse - 1.0) * 0.07)
    assert rendered_eye_values(controller, "left") == rendered_eye_values(controller, "right")


def test_blink_closes_both_resolved_eyes_for_the_existing_duration():
    controller = FaceController()
    controller.set_state(FaceState.LISTENING)
    controller.blink_interval_frames = 1

    with patch.object(controller, "random_blink_interval", return_value=100):
        trace = []
        for _ in range(controller.blink_duration_frames + 2):
            controller.update_blink()
            trace.append((controller.left_eye_closed, controller.right_eye_closed))

    assert trace == [(False, False), *[(True, True)] * 3, (False, False), (False, False)]


@pytest.mark.parametrize("state", list(FaceState))
def test_renderer_frame_is_equivalent_to_the_characterized_legacy_values(state):
    controller = FaceController()
    controller.set_state(state)
    controller.update_interpolation()
    frame = controller.get_frame()

    for side, eye in (("left", frame.left_eye), ("right", frame.right_eye)):
        legacy = rendered_eye_values(controller, side)
        assert (
            eye.offset_x * 700.0,
            eye.offset_y * 450.0,
            eye.width_scale,
            eye.height_scale,
            eye.corner_radius * 700.0,
            eye.tired_lid,
            eye.angry_lid,
            eye.happy_lid,
            eye.closed,
        ) == pytest.approx(legacy)


def test_qt_widget_converts_each_frame_side_to_the_legacy_rect(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from mira.ui.face.face_widget import FaceWidget

    application = QApplication.instance() or QApplication([])
    widget = FaceWidget()
    widget.resize(700, 450)
    try:
        for state in FaceState:
            widget.controller.set_state(state)
            controller = widget.controller
            controller.current_offset_x = controller.target_offset_x
            controller.current_offset_y = controller.target_offset_y
            controller.current_width_scale = controller.target_width_scale
            controller.current_height_scale = controller.target_height_scale

            for side, eye_model in (("left", widget.left_eye), ("right", widget.right_eye)):
                rect = widget.get_eye_rect(eye_model, side)
                legacy = rendered_eye_values(controller, side)
                assert (rect.x(), rect.y(), rect.width(), rect.height()) == pytest.approx(
                    (
                        700.0 * eye_model.x_ratio + legacy[0],
                        450.0 * eye_model.y_ratio + legacy[1],
                        700.0 * eye_model.width_ratio * legacy[2],
                        450.0 * eye_model.height_ratio * legacy[3],
                    )
                )
    finally:
        widget.frame_timer.stop()
        widget.close()
        application.processEvents()
