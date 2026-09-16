"""Characterize the values the current Qt face consumes before frame extraction."""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from mira.domain.state import FaceState
from mira.ui.face.face_controller import FaceController


REFERENCE_TICK_SECONDS = 0.030


def rendered_eye_values(controller: FaceController, side: str) -> tuple[float, ...]:
    """The renderer's current pre-frame calculation, expressed without Qt."""
    profile = controller.profile
    pose = controller.current_pose
    asymmetry_y = getattr(profile, f"asymmetry_offset_y_{side}")
    asymmetry_height = getattr(profile, f"asymmetry_height_{side}")
    return (
        pose.offset_x,
        pose.offset_y + asymmetry_y,
        pose.width_scale,
        pose.height_scale * asymmetry_height,
        pose.corner_radius,
        pose.eyelid_tired,
        pose.eyelid_angry,
        pose.eyelid_happy,
        controller.left_eye_closed if side == "left" else controller.right_eye_closed,
    )


def frame_eye_values(controller: FaceController, side: str) -> tuple[float, ...]:
    """Return renderer units from the public frame boundary."""
    frame = controller.get_frame()
    eye = frame.left_eye if side == "left" else frame.right_eye
    return (
        eye.offset_x * 700.0,
        eye.offset_y * 450.0,
        eye.width_scale,
        eye.height_scale,
        eye.corner_radius * 700.0,
        eye.tired_lid,
        eye.angry_lid,
        eye.happy_lid,
        eye.closed,
    )


def update_without_blink(controller: FaceController) -> None:
    controller.blink_interval_frames = 999
    controller.update(REFERENCE_TICK_SECONDS)


def test_idle_to_listening_uses_the_legacy_property_specific_alphas():
    controller = FaceController()
    controller.set_state(FaceState.LISTENING)

    update_without_blink(controller)

    expected = (
        -1.4,  # 0 + (-14 - 0) * 0.10
        -1.2,  # 0 + (-12 - 0) * 0.10
        0.9874,  # 1 + (0.82 - 1) * 0.07
        1.0154,  # 1 + (1.22 - 1) * 0.07
        28.0,
        0.0,
        0.0,
        0.0,
        False,
    )
    assert frame_eye_values(controller, "left") == pytest.approx(expected)
    assert frame_eye_values(controller, "right") == pytest.approx(expected)


def test_listening_to_thinking_mutates_the_target_before_interpolation():
    controller = FaceController()
    controller.set_state(FaceState.LISTENING)
    update_without_blink(controller)
    controller.set_state(FaceState.THINKING)

    update_without_blink(controller)

    drift_target_x = -28.0 + (-8.0 + 8.0 * math.sin(0.05 * 0.35))
    horizontal_amount = abs(drift_target_x - -28.0) / 25.0
    target_width = 1.10 + 0.10 * horizontal_amount
    target_height = 0.56 - 0.12 * horizontal_amount
    shared_x = -1.4 + (drift_target_x - -1.4) * 0.10
    shared_y = -1.2 + (10.0 - -1.2) * 0.10
    shared_width = 0.9874 + (target_width - 0.9874) * 0.07
    shared_height = 1.0154 + (target_height - 1.0154) * 0.07
    expected_common = (shared_x, shared_width, 27.6, 0.0, 0.0, 0.0, False)

    left = frame_eye_values(controller, "left")
    right = frame_eye_values(controller, "right")
    assert left == pytest.approx(
        (
            expected_common[0],
            shared_y - 1.0,
            expected_common[1],
            shared_height * 0.63,
            *expected_common[2:],
        )
    )
    assert right == pytest.approx(
        (
            expected_common[0],
            shared_y,
            expected_common[1],
            shared_height * 0.64,
            *expected_common[2:],
        )
    )


def test_idle_to_confused_applies_asymmetry_after_shared_interpolation():
    controller = FaceController()
    controller.set_state(FaceState.CONFUSED)

    assert frame_eye_values(controller, "left") == pytest.approx(
        (0.0, -6.0, 1.0, 1.05, 28.0, 0.0, 0.0, 0.0, False)
    )
    assert frame_eye_values(controller, "right") == pytest.approx(
        (0.0, 6.0, 1.0, 0.82, 28.0, 0.0, 0.0, 0.0, False)
    )

    update_without_blink(controller)

    assert frame_eye_values(controller, "left") == pytest.approx(
        (0.0, -5.8, 1.0, 0.9916 * 1.05, 28.0, 0.0, 0.0, 0.0, False)
    )
    assert frame_eye_values(controller, "right") == pytest.approx(
        (0.0, 6.2, 1.0, 0.9916 * 0.82, 28.0, 0.0, 0.0, 0.0, False)
    )


def test_confused_to_idle_removes_asymmetry_before_shared_pose_converges():
    controller = FaceController()
    controller.set_state(FaceState.CONFUSED)
    update_without_blink(controller)

    controller.set_state(FaceState.IDLE)

    expected_before_tick = (0.0, 0.2, 1.0, 0.9916, 28.0, 0.0, 0.0, 0.0, False)
    assert frame_eye_values(controller, "left") == pytest.approx(expected_before_tick)
    assert frame_eye_values(controller, "right") == pytest.approx(expected_before_tick)

    update_without_blink(controller)

    expected_after_tick = (
        -0.3,
        0.18,
        0.9895,
        1.062188,
        29.9,
        0.0,
        0.0,
        0.0,
        False,
    )
    assert frame_eye_values(controller, "left") == pytest.approx(expected_after_tick)
    assert frame_eye_values(controller, "right") == pytest.approx(expected_after_tick)


def test_expression_switch_mid_transition_retargets_from_the_current_pose():
    controller = FaceController()
    controller.set_state(FaceState.LISTENING)
    update_without_blink(controller)

    controller.set_state(FaceState.ANGRY)
    update_without_blink(controller)

    expected = (
        -1.26,
        -0.48,
        0.996682,
        0.987722,
        27.0,
        0.0,
        0.042,
        0.0,
        False,
    )
    assert frame_eye_values(controller, "left") == pytest.approx(expected)
    assert frame_eye_values(controller, "right") == pytest.approx(expected)


def test_repeated_ticks_follow_closed_form_legacy_convergence():
    controller = FaceController()
    controller.set_state(FaceState.LISTENING)
    tick_count = 8

    for _ in range(tick_count):
        update_without_blink(controller)

    offset_progress = 1.0 - (1.0 - 0.10) ** tick_count
    scale_progress = 1.0 - (1.0 - 0.07) ** tick_count
    expected = (
        -14.0 * offset_progress,
        -12.0 * offset_progress,
        1.0 + (0.82 - 1.0) * scale_progress,
        1.0 + (1.22 - 1.0) * scale_progress,
        28.0,
        0.0,
        0.0,
        0.0,
        False,
    )
    assert frame_eye_values(controller, "left") == pytest.approx(expected)
    assert frame_eye_values(controller, "right") == pytest.approx(expected)


def test_speaking_pulse_mutates_height_target_before_interpolation():
    controller = FaceController()
    controller.set_state(FaceState.SPEAKING)

    update_without_blink(controller)

    pulse_target = 0.95 + 0.16 * abs(math.sin(0.22))
    expected_height = 1.0 + (pulse_target - 1.0) * 0.07
    assert frame_eye_values(controller, "left")[3] == pytest.approx(expected_height)
    assert frame_eye_values(controller, "right")[3] == pytest.approx(expected_height)


def test_thinking_drift_and_deformation_mutate_target_before_interpolation():
    controller = FaceController()
    controller.set_state(FaceState.THINKING)

    update_without_blink(controller)

    # At phase zero, drift targets x=-36. Its 8-unit displacement then widens
    # by 0.032 and squashes height by 0.0384 before the playback step.
    assert frame_eye_values(controller, "left") == pytest.approx(
        (-3.6, 0.0, 1.00924, 0.966512 * 0.63, 27.6, 0.0, 0.0, 0.0, False)
    )
    assert frame_eye_values(controller, "right") == pytest.approx(
        (-3.6, 1.0, 1.00924, 0.966512 * 0.64, 27.6, 0.0, 0.0, 0.0, False)
    )


def test_eyelid_amount_uses_legacy_interpolation_before_frame_resolution():
    controller = FaceController()
    controller.set_state(FaceState.HAPPY)

    update_without_blink(controller)

    assert frame_eye_values(controller, "left")[7] == pytest.approx(0.042)
    assert frame_eye_values(controller, "right")[7] == pytest.approx(0.042)


@pytest.mark.parametrize("state", list(FaceState))
def test_each_profile_sets_the_exact_current_controller_targets(state):
    controller = FaceController()
    controller.set_state(state)
    profile = controller.expression_library[state]

    assert (
        controller.target_pose.offset_x,
        controller.target_pose.offset_y,
        controller.target_pose.width_scale,
        controller.target_pose.height_scale,
        controller.target_pose.corner_radius,
        controller.target_pose.eyelid_tired,
        controller.target_pose.eyelid_angry,
        controller.target_pose.eyelid_happy,
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
    frame = controller.base_frame
    left = (
        frame.left_eye.offset_y * 450.0,
        frame.left_eye.height_scale,
    )
    right = (
        frame.right_eye.offset_y * 450.0,
        frame.right_eye.height_scale,
    )

    assert left == pytest.approx((-4.0, 0.924))
    assert right == pytest.approx((8.0, 0.7216))


def test_one_frame_preserves_animation_then_interpolation_then_render_order():
    controller = FaceController()
    controller.set_state(FaceState.SPEAKING)
    controller.blink_interval_frames = 999

    controller.update(REFERENCE_TICK_SECONDS)

    expected_pulse = controller.profile.height_scale + 0.16 * abs(__import__("math").sin(0.22))
    assert controller.target_pose.height_scale == pytest.approx(expected_pulse)
    assert controller.current_pose.height_scale == pytest.approx(
        1.0 + (expected_pulse - 1.0) * 0.07
    )
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
