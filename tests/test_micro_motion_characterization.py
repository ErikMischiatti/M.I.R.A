"""Characterize the face's micro-motions through the controller's public surface.

These pin what a renderer can observe — the target pose each step and the
resolved frame — for blink, idle gaze, thinking drift, speaking pulse, gaze
deformation, cursor attention, gaze hold and scrutiny. They drive the controller
only through presentation calls, profile edits and scripted global randomness,
so they hold while the behaviour moves out of the controller.

Micro-motions advance one fixed step per `update()` call; only playback
interpolation uses the elapsed time. Expected values below are the legacy
formulas written out, not captured floats.
"""

from __future__ import annotations

import math
import random

import pytest

from mira.domain.attention import AttentionTarget
from mira.domain.embodiment import ActivityState, EmbodimentIntent
from mira.domain.state import FaceState
from mira.ui.face.face_controller import FaceController


TICK = 0.030

LOOK_STRENGTH_X = 82.0
LOOK_STRENGTH_Y = 54.0

LOOK_WEIGHT = {
    FaceState.IDLE: 1.00,
    FaceState.LISTENING: 0.95,
    FaceState.THINKING: 0.45,
    FaceState.SPEAKING: 0.35,
    FaceState.HAPPY: 0.80,
    FaceState.CONFUSED: 0.75,
    FaceState.TIRED: 0.50,
    FaceState.ANGRY: 0.65,
}


class ScriptedRandom:
    """Stand-in for the module-level `random` functions the face draws from."""

    IDLE_INTERVAL_BOUNDS = (40, 90)

    def __init__(self):
        self.idle_intervals: list[int] = []
        self.blink_intervals: list[int] = []
        self.offsets: list[float] = []
        self.calls: list[tuple] = []

    def randint(self, a: int, b: int) -> int:
        self.calls.append(("randint", a, b))
        if (a, b) == self.IDLE_INTERVAL_BOUNDS:
            return self.idle_intervals.pop(0)
        if self.blink_intervals:
            return self.blink_intervals.pop(0)
        return a

    def uniform(self, a: float, b: float) -> float:
        self.calls.append(("uniform", a, b))
        return self.offsets.pop(0)


@pytest.fixture
def scripted_random(monkeypatch) -> ScriptedRandom:
    scripted = ScriptedRandom()
    monkeypatch.setattr(random, "randint", scripted.randint)
    monkeypatch.setattr(random, "uniform", scripted.uniform)
    return scripted


def controller_in(state: FaceState, *, blink_enabled: bool = False, **overrides) -> FaceController:
    controller = FaceController()
    for profile in controller.expression_library.values():
        profile.blink_enabled = blink_enabled
    for name, value in overrides.items():
        setattr(controller.expression_library[state], name, value)
    controller.set_state(state)
    return controller


def look_at_ratio(controller: FaceController, x_ratio: float, y_ratio: float) -> None:
    """Cursor position as a fraction of the face canvas, (0.5, 0.5) centred."""
    controller.set_attention_target(
        AttentionTarget.clamped(x_ratio * 2.0 - 1.0, y_ratio * 2.0 - 1.0)
    )


def release_gaze(controller: FaceController) -> None:
    controller.clear_attention_target()


def target_offset(controller: FaceController) -> tuple[float, float]:
    pose = controller.target_pose
    profile = controller.profile
    return (pose.offset_x - profile.offset_x, pose.offset_y - profile.offset_y)


def eyes_closed(controller: FaceController) -> tuple[bool, bool]:
    frame = controller.get_frame()
    return (frame.left_eye.closed, frame.right_eye.closed)


def expected_deformation(width: float, height: float, dx: float, dy: float) -> tuple[float, float]:
    horizontal = min(abs(dx) / 25.0, 1.0)
    vertical = min(abs(dy) / 20.0, 1.0)
    if dy < 0:
        vertical_adjust = 0.04 * vertical
    elif dy > 0:
        vertical_adjust = -0.06 * vertical
    else:
        vertical_adjust = 0.0
    return (width + 0.10 * horizontal, height + vertical_adjust - 0.12 * horizontal)


def expected_look(attention_x: float, attention_y: float) -> tuple[float, float]:
    """Shape a smoothed canvas-ratio attention point into unit gaze."""
    look_x = (attention_x - 0.5) * 2.0
    look_y = (attention_y - 0.5) * 2.0
    look_x = 0.0 if abs(look_x) < 0.035 else look_x
    look_y = 0.0 if abs(look_y) < 0.035 else look_y
    look_x = math.copysign(abs(look_x) ** 1.35, look_x)
    look_y = math.copysign(abs(look_y) ** 1.35, look_y)
    norm = look_x**2 + look_y**2
    if norm > 1.0:
        look_x /= math.sqrt(norm)
        look_y /= math.sqrt(norm)
    return look_x, look_y


def assert_target_scales(controller: FaceController, width: float, height: float) -> None:
    pose = controller.target_pose
    assert (pose.width_scale, pose.height_scale) == pytest.approx((width, height))


# --- blink -----------------------------------------------------------------


def test_blink_waits_for_its_interval_then_closes_both_eyes_for_duration_minus_one_step():
    controller = controller_in(
        FaceState.LISTENING,
        blink_enabled=True,
        blink_min_interval_frames=3,
        blink_max_interval_frames=3,
        blink_duration_frames=4,
    )

    trace = []
    for _ in range(14):
        controller.update(TICK)
        trace.append(eyes_closed(controller))

    closed = (True, True)
    opened = (False, False)
    assert trace == [
        opened, opened, opened,  # counting to the 3-step interval; step 3 arms it
        closed, closed, closed,  # 4-step duration, last step reopens
        opened,
        opened, opened, opened,  # next interval, counted from zero
        closed, closed, closed,
        opened,
    ]


def test_blink_interval_is_drawn_between_the_profile_bounds_in_either_order(scripted_random):
    # Construction draws for IDLE first; the LISTENING switch then draws 2.
    scripted_random.blink_intervals = [99, 2, 5]
    controller = controller_in(
        FaceState.LISTENING,
        blink_enabled=True,
        blink_min_interval_frames=9,
        blink_max_interval_frames=4,
        blink_duration_frames=2,
    )
    blink_draws = [call for call in scripted_random.calls if call[0] == "randint"]
    assert blink_draws[-1] == ("randint", 4, 9)

    trace = []
    for _ in range(10):
        controller.update(TICK)
        trace.append(eyes_closed(controller)[0])

    # Interval 2 → armed at step 2, closed step 3, reopened step 4 with a fresh
    # draw of 5 → armed at step 9, closed step 10.
    assert trace == [False, False, True, False, False, False, False, False, False, True]
    assert scripted_random.calls[-1] == ("randint", 4, 9)


def test_changing_expression_redraws_the_interval_but_keeps_the_elapsed_count():
    controller = controller_in(
        FaceState.IDLE,
        blink_enabled=True,
        blink_min_interval_frames=10,
        blink_max_interval_frames=10,
    )
    listening = controller.expression_library[FaceState.LISTENING]
    listening.blink_min_interval_frames = 5
    listening.blink_max_interval_frames = 5

    for _ in range(4):
        controller.update(TICK)
    controller.set_state(FaceState.LISTENING)

    trace = []
    for _ in range(3):
        controller.update(TICK)
        trace.append(eyes_closed(controller)[0])

    # The fifth step overall reaches the new 5-step interval.
    assert trace == [False, True, True]


def test_disabled_blink_never_closes_the_eyes():
    controller = controller_in(
        FaceState.IDLE,
        blink_enabled=False,
        blink_min_interval_frames=1,
        blink_max_interval_frames=1,
    )

    for _ in range(300):
        controller.update(TICK)
        assert eyes_closed(controller) == (False, False)


def test_micro_motions_advance_one_step_per_update_whatever_the_elapsed_time():
    controller = controller_in(
        FaceState.LISTENING,
        blink_enabled=True,
        blink_min_interval_frames=3,
        blink_max_interval_frames=3,
    )

    trace = []
    for dt in (0.0, 0.090, 0.001, 0.500):
        controller.update(dt)
        trace.append(eyes_closed(controller)[0])

    assert trace == [False, False, False, True]


# --- idle gaze -------------------------------------------------------------


def test_idle_gaze_retargets_after_sixty_steps_then_at_drawn_intervals(scripted_random):
    scripted_random.idle_intervals = [45, 70]
    scripted_random.offsets = [10.0, -20.0, 30.0, 5.0]
    controller = controller_in(FaceState.IDLE)
    profile = controller.profile

    for step in range(1, 60):
        controller.update(TICK)
        assert target_offset(controller) == (0.0, 0.0), step
        assert_target_scales(controller, profile.width_scale, profile.height_scale)

    controller.update(TICK)
    assert target_offset(controller) == pytest.approx((10.0, -20.0))
    assert_target_scales(
        controller, *expected_deformation(profile.width_scale, profile.height_scale, 10.0, -20.0)
    )

    for _ in range(44):
        controller.update(TICK)
        assert target_offset(controller) == pytest.approx((10.0, -20.0))

    controller.update(TICK)
    assert target_offset(controller) == pytest.approx((30.0, 5.0))

    uniform_calls = [call for call in scripted_random.calls if call[0] == "uniform"]
    amplitude_x, amplitude_y = profile.idle_amplitude_x, profile.idle_amplitude_y
    assert uniform_calls == [
        ("uniform", -amplitude_x, amplitude_x),
        ("uniform", -amplitude_y, amplitude_y),
    ] * 2
    assert ("randint", 40, 90) in scripted_random.calls


def test_expression_change_discards_the_idle_offset_but_not_the_idle_count(scripted_random):
    scripted_random.idle_intervals = [45, 50]
    scripted_random.offsets = [10.0, -20.0, 30.0, 5.0]
    controller = controller_in(FaceState.IDLE)

    for _ in range(70):
        controller.update(TICK)
    controller.set_state(FaceState.IDLE)
    assert target_offset(controller) == (0.0, 0.0)

    for _ in range(34):
        controller.update(TICK)
        assert target_offset(controller) == (0.0, 0.0)
    controller.update(TICK)
    assert target_offset(controller) == pytest.approx((30.0, 5.0))


def test_profile_edit_discards_the_idle_offset(scripted_random):
    scripted_random.idle_intervals = [45]
    scripted_random.offsets = [10.0, -20.0]
    controller = controller_in(FaceState.IDLE)
    for _ in range(60):
        controller.update(TICK)
    assert target_offset(controller) != (0.0, 0.0)

    controller.refresh_profile_targets()

    assert target_offset(controller) == (0.0, 0.0)


def test_expressions_without_idle_gaze_hold_the_profile_offset(scripted_random):
    controller = controller_in(FaceState.LISTENING)
    profile = controller.profile

    for _ in range(200):
        controller.update(TICK)
        assert target_offset(controller) == (0.0, 0.0)
        assert_target_scales(controller, profile.width_scale, profile.height_scale)

    assert not [call for call in scripted_random.calls if call[0] == "uniform"]
    assert ("randint", 40, 90) not in scripted_random.calls


@pytest.mark.parametrize(
    "dy,height_adjust",
    [(-30.0, 0.04), (-10.0, 0.02), (10.0, -0.03), (40.0, -0.06)],
)
def test_vertical_gaze_deformation_lifts_up_and_squashes_down(scripted_random, dy, height_adjust):
    scripted_random.idle_intervals = [45]
    scripted_random.offsets = [0.0, dy]
    controller = controller_in(FaceState.IDLE)
    for _ in range(60):
        controller.update(TICK)

    profile = controller.profile
    assert_target_scales(controller, profile.width_scale, profile.height_scale + height_adjust)


# --- thinking drift and speaking pulse --------------------------------------


def test_thinking_drift_sweeps_the_target_left_and_deforms_it():
    controller = controller_in(FaceState.THINKING)
    profile = controller.profile

    for step in range(1, 81):
        controller.update(TICK)
        drift = -8.0 + 8.0 * math.sin(0.05 * (step - 1) * 0.35)
        assert target_offset(controller) == pytest.approx((drift, 0.0)), step
        assert_target_scales(
            controller,
            *expected_deformation(profile.width_scale, profile.height_scale, drift, 0.0),
        )


def test_thinking_drift_overrides_the_idle_offset_on_both_axes(scripted_random):
    scripted_random.idle_intervals = [45]
    scripted_random.offsets = [12.0, 6.0]
    controller = controller_in(
        FaceState.HAPPY, idle_enabled=True, thinking_drift=True, idle_amplitude_x=20.0
    )

    for _ in range(60):
        controller.update(TICK)

    drift = -8.0 + 8.0 * math.sin(0.05 * 59 * 0.35)
    assert target_offset(controller) == pytest.approx((drift, 0.0))


def test_leaving_thinking_restores_the_profile_target():
    controller = controller_in(FaceState.THINKING)
    for _ in range(20):
        controller.update(TICK)

    controller.set_state(FaceState.LISTENING)
    controller.update(TICK)

    profile = controller.profile
    assert target_offset(controller) == (0.0, 0.0)
    assert_target_scales(controller, profile.width_scale, profile.height_scale)


def test_speaking_pulse_raises_the_height_target_rhythmically():
    controller = controller_in(FaceState.SPEAKING)
    profile = controller.profile

    for step in range(1, 81):
        controller.update(TICK)
        phase = 0.27 * (step - 1) + 0.22
        assert target_offset(controller) == (0.0, 0.0)
        assert_target_scales(
            controller,
            profile.width_scale,
            profile.height_scale + 0.16 * abs(math.sin(phase)),
        )


def test_thinking_drift_continues_the_phase_the_speaking_pulse_advanced():
    controller = controller_in(FaceState.SPEAKING)
    for _ in range(10):
        controller.update(TICK)

    controller.set_state(FaceState.THINKING)
    for step in range(1, 6):
        controller.update(TICK)
        phase = 10 * 0.27 + 0.05 * (step - 1)
        drift = -8.0 + 8.0 * math.sin(phase * 0.35)
        assert target_offset(controller)[0] == pytest.approx(drift)


def test_leaving_speaking_restores_the_profile_height():
    controller = controller_in(FaceState.SPEAKING)
    for _ in range(7):
        controller.update(TICK)

    controller.set_intent(EmbodimentIntent(ActivityState.LISTENING))
    controller.update(TICK)

    profile = controller.profile
    assert_target_scales(controller, profile.width_scale, profile.height_scale)


# --- cursor attention, hold and scrutiny ------------------------------------


def test_first_step_toward_a_cursor_uses_the_entry_boost_and_deforms_the_target():
    controller = controller_in(FaceState.IDLE)
    profile = controller.profile
    look_at_ratio(controller, 1.0, 0.5)

    controller.update(TICK)

    look_x, _ = expected_look(0.5 + 0.5 * 0.32, 0.5)
    dx = look_x * LOOK_STRENGTH_X
    assert target_offset(controller) == pytest.approx((dx, 0.0))
    assert_target_scales(
        controller, *expected_deformation(profile.width_scale, profile.height_scale, dx, 0.0)
    )


@pytest.mark.parametrize("state", list(FaceState))
def test_each_expression_scales_cursor_gaze_by_its_look_weight(state):
    controller = controller_in(state)
    look_at_ratio(controller, 1.0, 0.5)

    controller.update(TICK)

    look_x, _ = expected_look(0.66, 0.5)
    assert target_offset(controller)[0] == pytest.approx(
        look_x * LOOK_STRENGTH_X * LOOK_WEIGHT[state]
    )


def test_cursor_ratios_are_clamped_to_the_canvas():
    outside = controller_in(FaceState.IDLE)
    edge = controller_in(FaceState.IDLE)
    look_at_ratio(outside, 5.0, -3.0)
    look_at_ratio(edge, 1.0, 0.0)

    for _ in range(4):
        outside.update(TICK)
        edge.update(TICK)
        assert outside.target_pose == edge.target_pose


def test_small_gaze_displacements_fall_inside_the_deadzone():
    controller = controller_in(FaceState.IDLE)
    look_at_ratio(controller, 0.51, 1.0)

    controller.update(TICK)

    _, look_y = expected_look(0.5, 0.66)
    assert target_offset(controller) == pytest.approx((0.0, look_y * LOOK_STRENGTH_Y))
    assert target_offset(controller)[0] == 0.0


def test_diagonal_gaze_is_clipped_to_the_unit_ellipse():
    controller = controller_in(FaceState.IDLE)
    look_at_ratio(controller, 1.0, 1.0)

    for _ in range(6):
        controller.update(TICK)

    assert target_offset(controller) == pytest.approx(
        (LOOK_STRENGTH_X / math.sqrt(2.0), LOOK_STRENGTH_Y / math.sqrt(2.0))
    )


def test_entry_boost_lasts_ten_steps_then_steady_following_takes_over():
    controller = controller_in(FaceState.LISTENING)
    attention_x = 0.5

    for step in range(1, 16):
        # Alternating edges keep the gaze unsettled, so no scrutiny is added.
        cursor_x = 1.0 if step % 2 else 0.0
        look_at_ratio(controller, cursor_x, 0.5)
        controller.update(TICK)

        alpha = 0.32 if step <= 10 else 0.18
        attention_x += (cursor_x - attention_x) * alpha
        look_x, _ = expected_look(attention_x, 0.5)
        assert target_offset(controller)[0] == pytest.approx(
            look_x * LOOK_STRENGTH_X * LOOK_WEIGHT[FaceState.LISTENING]
        ), step


def test_settled_attention_orbits_in_a_growing_scrutiny_ellipse():
    controller = controller_in(FaceState.IDLE)
    look_at_ratio(controller, 0.5, 0.5)

    for step in range(1, 121):
        controller.update(TICK)
        activation = 1.0 - 0.92**step
        phase = 0.11 * step
        expected = (
            math.cos(phase) * 0.12 * activation * LOOK_STRENGTH_X,
            math.sin(phase) * 0.07 * activation * LOOK_STRENGTH_Y,
        )
        assert target_offset(controller) == pytest.approx(expected), step
        assert abs(target_offset(controller)[0]) <= 0.12 * LOOK_STRENGTH_X
        assert abs(target_offset(controller)[1]) <= 0.07 * LOOK_STRENGTH_Y


def test_releasing_attention_fades_scrutiny_during_the_nine_step_hold():
    controller = controller_in(FaceState.IDLE)
    look_at_ratio(controller, 0.5, 0.5)
    for _ in range(30):
        controller.update(TICK)
    activation = 1.0 - 0.92**30

    release_gaze(controller)
    for step in range(1, 10):
        controller.update(TICK)
        activation *= 0.92
        phase = 0.11 * (30 + step)
        assert target_offset(controller) == pytest.approx(
            (
                math.cos(phase) * 0.12 * activation * LOOK_STRENGTH_X,
                math.sin(phase) * 0.07 * activation * LOOK_STRENGTH_Y,
            )
        ), step


def test_released_gaze_holds_then_freezes_when_idle_gaze_is_enabled():
    controller = controller_in(FaceState.IDLE)
    look_at_ratio(controller, 1.0, 1.0)
    for _ in range(6):
        controller.update(TICK)
    looked = target_offset(controller)

    release_gaze(controller)
    for _ in range(30):
        controller.update(TICK)
        assert target_offset(controller) == pytest.approx(looked)


def test_released_gaze_holds_nine_steps_then_returns_to_the_profile_offset():
    controller = controller_in(FaceState.LISTENING)
    look_at_ratio(controller, 1.0, 1.0)
    for _ in range(6):
        controller.update(TICK)
    looked = target_offset(controller)

    # The ten-step hold counter is decremented before it is read, so the held
    # gaze stays visible for nine steps.
    release_gaze(controller)
    for _ in range(9):
        controller.update(TICK)
        assert target_offset(controller) == pytest.approx(looked)
    for _ in range(5):
        controller.update(TICK)
        assert target_offset(controller) == (0.0, 0.0)


def test_attention_decays_to_centre_after_the_hold_and_reentry_is_boosted_again():
    controller = controller_in(FaceState.LISTENING)
    weight = LOOK_WEIGHT[FaceState.LISTENING]
    attention_x = 0.5
    look_at_ratio(controller, 1.0, 0.5)
    for _ in range(3):
        controller.update(TICK)
        attention_x += (1.0 - attention_x) * 0.32

    release_gaze(controller)
    for step in range(20):
        controller.update(TICK)
        if step >= 10:
            attention_x += (0.5 - attention_x) * 0.08

    look_at_ratio(controller, 0.0, 0.5)
    controller.update(TICK)
    attention_x += (0.0 - attention_x) * 0.32

    look_x, _ = expected_look(attention_x, 0.5)
    assert target_offset(controller)[0] == pytest.approx(look_x * LOOK_STRENGTH_X * weight)


def test_cursor_gaze_overrides_thinking_drift_and_speaking_pulse_still_applies():
    thinking = controller_in(FaceState.THINKING)
    speaking = controller_in(FaceState.SPEAKING)
    for controller in (thinking, speaking):
        look_at_ratio(controller, 1.0, 0.5)
        controller.update(TICK)

    look_x, _ = expected_look(0.66, 0.5)
    assert target_offset(thinking) == pytest.approx(
        (look_x * LOOK_STRENGTH_X * LOOK_WEIGHT[FaceState.THINKING], 0.0)
    )
    dx = look_x * LOOK_STRENGTH_X * LOOK_WEIGHT[FaceState.SPEAKING]
    profile = speaking.profile
    assert target_offset(speaking) == pytest.approx((dx, 0.0))
    assert_target_scales(
        speaking,
        *expected_deformation(
            profile.width_scale,
            profile.height_scale + 0.16 * abs(math.sin(0.22)),
            dx,
            0.0,
        ),
    )


# --- determinism -----------------------------------------------------------


def test_identical_seed_and_inputs_produce_identical_frames():
    def run() -> list:
        random.seed(20260930)
        controller = FaceController()
        frames = []
        for step in range(400):
            if step == 50:
                look_at_ratio(controller, 0.8, 0.2)
            if step == 150:
                release_gaze(controller)
            if step == 200:
                controller.set_state(FaceState.SPEAKING)
            if step == 300:
                controller.set_state(FaceState.THINKING)
            controller.update(TICK)
            frames.append(controller.get_frame())
        return frames

    assert run() == run()
