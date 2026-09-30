"""Renderer-independent micro-motion and attention vocabulary."""

from __future__ import annotations

import ast
import random
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mira.domain.attention as attention_module
import mira.domain.gaze as gaze_module
import mira.domain.micro_motion as micro_motion_module
from mira.domain.attention import AttentionTarget
from mira.domain.embodiment import ExpressionKey
from mira.domain.embodiment_playback import PlaybackPose
from mira.domain.gaze import GAZE_STRENGTH_X, GAZE_STRENGTH_Y, GazeAttention, gaze_weight_for
import math

from mira.domain.micro_motion import (
    BlinkMotion,
    BlinkSettings,
    IdleGaze,
    MicroMotions,
    MicroMotionState,
    MotionSettings,
    gaze_deformation,
    speaking_pulse,
    thinking_drift,
)


class SequenceRandom:
    """Deterministic draws that record what they were asked for."""

    def __init__(self, *, ints=(), floats=()):
        self.ints = list(ints)
        self.floats = list(floats)
        self.calls: list[tuple] = []

    def randint(self, a: int, b: int) -> int:
        self.calls.append(("randint", a, b))
        return self.ints.pop(0)

    def uniform(self, a: float, b: float) -> float:
        self.calls.append(("uniform", a, b))
        return self.floats.pop(0)


def imported_modules(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    return names


# --- AttentionTarget -------------------------------------------------------


def test_attention_target_defaults_to_straight_ahead():
    assert AttentionTarget() == AttentionTarget(0.0, 0.0)


@pytest.mark.parametrize("x,y", [(-1.0, -1.0), (1.0, 1.0), (0.25, -0.5)])
def test_attention_target_accepts_the_closed_unit_square(x, y):
    target = AttentionTarget(x, y)
    assert (target.x, target.y) == (x, y)


@pytest.mark.parametrize(
    "x,y",
    [(1.01, 0.0), (0.0, -1.5), (float("nan"), 0.0), (0.0, float("inf"))],
)
def test_attention_target_rejects_points_outside_the_view_space(x, y):
    with pytest.raises(ValueError, match="within \\[-1, 1\\]"):
        AttentionTarget(x, y)


def test_clamped_attention_target_limits_each_axis():
    assert AttentionTarget.clamped(3.0, -7.0) == AttentionTarget(1.0, -1.0)
    assert AttentionTarget.clamped(0.3, -0.2) == AttentionTarget(0.3, -0.2)


@pytest.mark.parametrize("x,y", [(float("nan"), 0.0), (0.0, float("-inf"))])
def test_clamped_attention_target_still_rejects_non_finite_points(x, y):
    with pytest.raises(ValueError, match="finite"):
        AttentionTarget.clamped(x, y)


def test_attention_target_is_an_immutable_value():
    target = AttentionTarget(0.5, -0.5)
    assert target == AttentionTarget(0.5, -0.5)
    assert hash(target) == hash(AttentionTarget(0.5, -0.5))
    with pytest.raises((FrozenInstanceError, AttributeError)):
        target.x = 0.0  # type: ignore[misc]


# --- MicroMotionState ------------------------------------------------------


POSE = PlaybackPose(
    offset_x=-3.0,
    offset_y=4.0,
    width_scale=0.85,
    height_scale=2.0,
    corner_radius=47.0,
    eyelid_tired=0.1,
    eyelid_angry=0.2,
    eyelid_happy=0.3,
)


def test_neutral_micro_motion_leaves_the_pose_unchanged():
    assert MicroMotionState().apply_to(POSE) == POSE
    assert MicroMotionState().eyes_closed is False


def test_micro_motion_adds_offsets_and_scale_deltas_only():
    state = MicroMotionState(
        offset_x=10.0, offset_y=-6.0, width_delta=0.04, height_delta=-0.1, eyes_closed=True
    )

    assert state.apply_to(POSE) == PlaybackPose(
        offset_x=7.0,
        offset_y=-2.0,
        width_scale=0.89,
        height_scale=1.9,
        corner_radius=47.0,
        eyelid_tired=0.1,
        eyelid_angry=0.2,
        eyelid_happy=0.3,
    )


def test_micro_motion_composition_is_deterministic_and_does_not_mutate():
    state = MicroMotionState(offset_x=1.5, height_delta=0.2)

    first = state.apply_to(POSE)
    second = state.apply_to(POSE)

    assert first == second
    assert POSE.offset_x == -3.0
    with pytest.raises((FrozenInstanceError, AttributeError)):
        state.offset_x = 0.0  # type: ignore[misc]


# --- BlinkMotion -----------------------------------------------------------


def blink_trace(blink: BlinkMotion, steps: int) -> list[bool]:
    return [blink.step() for _ in range(steps)]


def test_blink_draws_nothing_until_configured():
    rng = SequenceRandom()
    blink = BlinkMotion(rng)

    assert rng.calls == []
    assert blink.closed is False


def test_blink_stays_open_before_the_deadline_then_closes_and_reopens():
    rng = SequenceRandom(ints=[4, 3])
    blink = BlinkMotion(rng)
    blink.configure(BlinkSettings(min_interval_steps=2, max_interval_steps=6, duration_steps=3))

    trace = blink_trace(blink, 12)

    assert trace == [
        False, False, False, False,  # interval of 4; the fourth step arms the blink
        True, True,                  # duration 3 → two closed steps
        False,                       # reopened; next interval of 3 drawn
        False, False, False,
        True, True,
    ]
    assert rng.calls == [("randint", 2, 6)] * 2


def test_blink_interval_bounds_may_be_given_in_either_order():
    rng = SequenceRandom(ints=[5])
    BlinkMotion(rng).configure(BlinkSettings(min_interval_steps=9, max_interval_steps=4))

    assert rng.calls == [("randint", 4, 9)]


def test_reconfiguring_redraws_the_interval_but_keeps_the_elapsed_count():
    rng = SequenceRandom(ints=[10, 5])
    blink = BlinkMotion(rng)
    blink.configure(BlinkSettings())
    blink_trace(blink, 3)

    blink.configure(BlinkSettings(duration_steps=2))

    # Three steps already elapsed, so the new five-step interval arms on the
    # second step and closes on the third.
    assert blink_trace(blink, 3) == [False, False, True]


def test_disabling_blink_opens_the_eyes_and_suspends_a_blink_in_progress():
    rng = SequenceRandom(ints=[2, 9, 9, 9])
    blink = BlinkMotion(rng)
    blink.configure(BlinkSettings(duration_steps=4))
    blink_trace(blink, 3)
    assert blink.closed is True

    blink.configure(BlinkSettings(enabled=False, duration_steps=4))
    assert blink_trace(blink, 5) == [False] * 5

    blink.configure(BlinkSettings(duration_steps=4))
    assert blink_trace(blink, 3) == [True, True, False]


def test_seeded_blinks_are_reproducible():
    def run(seed: int) -> list[bool]:
        blink = BlinkMotion(random.Random(seed))
        blink.configure(BlinkSettings(min_interval_steps=3, max_interval_steps=12))
        return blink_trace(blink, 400)

    assert run(7) == run(7)
    assert True in run(7)


# --- IdleGaze and modulation functions --------------------------------------


def test_idle_gaze_first_wanders_after_sixty_steps_then_at_drawn_intervals():
    rng = SequenceRandom(ints=[40, 90], floats=[3.0, -2.0, -5.0, 1.0])
    idle = IdleGaze(rng)

    offsets = [idle.step(18.0, 10.0) for _ in range(100)]

    assert [step for step, offset in enumerate(offsets, 1) if offset] == [60, 100]
    assert offsets[59] == (3.0, -2.0)
    assert offsets[99] == (-5.0, 1.0)
    assert rng.calls[:3] == [("randint", 40, 90), ("uniform", -18.0, 18.0), ("uniform", -10.0, 10.0)]


def test_thinking_drift_glances_left_and_back():
    assert thinking_drift(0.0) == -8.0
    assert thinking_drift(math.pi / 0.35 / 2) == pytest.approx(0.0)
    assert thinking_drift(3 * math.pi / 0.35 / 2) == pytest.approx(-16.0)


def test_speaking_pulse_is_a_non_negative_rectified_sine():
    assert speaking_pulse(0.0) == 0.0
    assert speaking_pulse(math.pi / 2) == pytest.approx(0.16)
    assert speaking_pulse(-math.pi / 2) == pytest.approx(0.16)


@pytest.mark.parametrize(
    "offset,expected",
    [
        ((0.0, 0.0), (0.0, 0.0)),
        ((12.5, 0.0), (0.05, -0.06)),
        ((-50.0, 0.0), (0.10, -0.12)),
        ((0.0, -10.0), (0.0, 0.02)),
        ((0.0, 30.0), (0.0, -0.06)),
    ],
)
def test_gaze_deformation_widens_sideways_lifts_up_and_squashes_down(offset, expected):
    assert gaze_deformation(*offset) == pytest.approx(expected)


# --- MicroMotions ----------------------------------------------------------


NO_BLINK = BlinkSettings(enabled=False)


def motions(settings: MotionSettings, rng=None) -> MicroMotions:
    system = MicroMotions(rng or SequenceRandom(ints=[100] * 10))
    system.configure(settings)
    return system


def test_micro_motions_start_neutral_and_stay_neutral_without_active_motions():
    system = motions(MotionSettings(blink=NO_BLINK))
    assert system.state == MicroMotionState()

    for _ in range(200):
        assert system.step() == MicroMotionState()


def test_micro_motions_report_the_blink_closed_state():
    system = motions(
        MotionSettings(blink=BlinkSettings(duration_steps=3)), SequenceRandom(ints=[2, 50])
    )

    assert [system.step().eyes_closed for _ in range(5)] == [False, False, True, True, False]


def test_idle_gaze_offset_persists_and_deforms_until_the_next_wander():
    rng = SequenceRandom(ints=[100, 45], floats=[10.0, -20.0])
    system = motions(MotionSettings(blink=NO_BLINK, idle_gaze=True, idle_amplitude_x=30.0), rng)

    states = [system.step() for _ in range(80)]

    assert all(state == MicroMotionState() for state in states[:59])
    width_delta, height_delta = gaze_deformation(10.0, -20.0)
    assert set(states[59:]) == {
        MicroMotionState(
            offset_x=10.0, offset_y=-20.0, width_delta=width_delta, height_delta=height_delta
        )
    }


def test_configure_discards_the_wander_offset_but_keeps_the_idle_schedule():
    rng = SequenceRandom(ints=[100, 100, 45], floats=[10.0, -20.0])
    settings = MotionSettings(blink=NO_BLINK, idle_gaze=True)
    system = motions(settings, rng)
    for _ in range(59):
        system.step()

    system.configure(settings)

    assert system.state == MicroMotionState()
    assert system.step().offset_x == 10.0


def test_without_idle_gaze_the_offset_returns_to_neutral_after_the_gaze_hold():
    system = motions(MotionSettings(blink=NO_BLINK))
    system.set_attention_target(AttentionTarget(1.0, 0.0))
    system.step()
    system.clear_attention_target()

    held = [system.step() for _ in range(GazeAttention.HOLD_STEPS)]

    assert all(state.offset_x > 0.0 for state in held[:-1])
    assert held[-1] == MicroMotionState()


def test_thinking_drift_advances_with_the_shared_phase():
    system = motions(MotionSettings(blink=NO_BLINK, thinking_drift=True))

    for step in range(40):
        state = system.step()
        drift = thinking_drift(0.05 * step)
        assert (state.offset_x, state.offset_y) == pytest.approx((drift, 0.0))
        assert (state.width_delta, state.height_delta) == pytest.approx(
            gaze_deformation(drift, 0.0)
        )


def test_speaking_pulse_advances_the_phase_faster_and_raises_height_only():
    system = motions(MotionSettings(blink=NO_BLINK, speaking_pulse=True))

    for step in range(1, 41):
        state = system.step()
        assert (state.offset_x, state.offset_y, state.width_delta) == (0.0, 0.0, 0.0)
        assert state.height_delta == pytest.approx(speaking_pulse(0.27 * (step - 1) + 0.22))


def test_switching_from_speaking_to_thinking_continues_the_phase():
    system = motions(MotionSettings(blink=NO_BLINK, speaking_pulse=True))
    for _ in range(10):
        system.step()

    system.configure(MotionSettings(blink=NO_BLINK, thinking_drift=True))

    assert system.step().offset_x == pytest.approx(thinking_drift(10 * 0.27))


def test_leaving_a_modulated_expression_restores_the_neutral_state():
    system = motions(MotionSettings(blink=NO_BLINK, thinking_drift=True, speaking_pulse=True))
    for _ in range(15):
        system.step()

    system.configure(MotionSettings(blink=NO_BLINK))

    assert system.state == MicroMotionState()
    assert system.step() == MicroMotionState()


def test_thinking_drift_replaces_the_idle_wander_on_both_axes():
    rng = SequenceRandom(ints=[100, 45], floats=[12.0, 6.0])
    system = motions(
        MotionSettings(blink=NO_BLINK, idle_gaze=True, thinking_drift=True), rng
    )

    states = [system.step() for _ in range(60)]

    assert (states[-1].offset_x, states[-1].offset_y) == pytest.approx(
        (thinking_drift(0.05 * 59), 0.0)
    )


def test_attention_gaze_replaces_drift_is_weighted_and_is_deformed():
    system = motions(
        MotionSettings(blink=NO_BLINK, thinking_drift=True, speaking_pulse=True, gaze_weight=0.5)
    )
    reference = GazeAttention()
    target = AttentionTarget(0.6, -0.4)
    system.set_attention_target(target)
    reference.look_at(target)

    state = system.step()

    gaze_x, gaze_y = reference.step(0.5)
    width_delta, height_delta = gaze_deformation(gaze_x, gaze_y)
    assert (state.offset_x, state.offset_y, state.width_delta) == pytest.approx(
        (gaze_x, gaze_y, width_delta)
    )
    assert state.height_delta == pytest.approx(speaking_pulse(0.22) + height_delta)


def test_the_last_gaze_offset_persists_after_the_hold_when_idle_gaze_is_on():
    system = motions(MotionSettings(blink=NO_BLINK, idle_gaze=True))
    system.set_attention_target(AttentionTarget(1.0, 1.0))
    system.step()
    system.clear_attention_target()
    for _ in range(GazeAttention.HOLD_STEPS - 1):
        last_held = system.step()

    assert [system.step() for _ in range(20)] == [last_held] * 20


def test_attention_survives_an_expression_change():
    system = motions(MotionSettings(blink=NO_BLINK))
    target = AttentionTarget(0.5, 0.5)
    system.set_attention_target(target)
    system.step()

    system.configure(MotionSettings(blink=NO_BLINK, gaze_weight=0.35))

    assert system.attention_target == target
    assert system.step().offset_x > 0.0


def test_seeded_micro_motions_are_reproducible():
    def run(seed: int) -> list[MicroMotionState]:
        system = MicroMotions(random.Random(seed))
        system.configure(MotionSettings(idle_gaze=True, speaking_pulse=True))
        return [system.step() for _ in range(500)]

    assert run(11) == run(11)


# --- GazeAttention ---------------------------------------------------------


def test_gaze_is_inactive_until_attention_arrives():
    gaze = GazeAttention()

    assert gaze.target is None
    assert [gaze.step(1.0) for _ in range(20)] == [None] * 20
    assert gaze.point == AttentionTarget()


def test_gaze_accepts_a_target_and_enters_quickly():
    gaze = GazeAttention()
    gaze.look_at(AttentionTarget(1.0, 0.0))

    offset = gaze.step(1.0)

    assert gaze.point == AttentionTarget(0.32, 0.0)
    assert offset == pytest.approx((0.32**1.35 * GAZE_STRENGTH_X, 0.0))


def test_updating_the_target_retargets_without_restarting_the_entry_boost():
    gaze = GazeAttention()
    gaze.look_at(AttentionTarget(1.0, 0.0))
    for _ in range(GazeAttention.ENTRY_BOOST_STEPS):
        gaze.step(1.0)
    x = gaze.point.x

    gaze.look_at(AttentionTarget(-1.0, 0.0))
    gaze.step(1.0)

    assert gaze.point.x == pytest.approx(x + (-1.0 - x) * 0.18)


def test_released_gaze_holds_its_point_then_returns_to_centre():
    gaze = GazeAttention()
    gaze.look_at(AttentionTarget(-1.0, 0.0))
    for _ in range(3):
        gaze.step(1.0)
    held = gaze.point

    gaze.release()
    offsets = [gaze.step(1.0) for _ in range(GazeAttention.HOLD_STEPS)]

    assert gaze.target is None
    assert gaze.point == held
    assert all(offset is not None and offset[0] < 0.0 for offset in offsets[:-1])
    assert offsets[-1] is None
    gaze.step(1.0)
    assert gaze.point.x == pytest.approx(held.x * 0.92)


def test_gaze_weight_scales_the_offset_linearly():
    gaze_full, gaze_half = GazeAttention(), GazeAttention()
    for gaze in (gaze_full, gaze_half):
        gaze.look_at(AttentionTarget(0.4, -0.8))

    full = gaze_full.step(1.0)
    half = gaze_half.step(0.5)

    assert half == pytest.approx((full[0] * 0.5, full[1] * 0.5))


def test_settled_gaze_orbits_while_following_and_fades_after_release():
    gaze = GazeAttention()
    gaze.look_at(AttentionTarget())
    orbit = [gaze.step(1.0) for _ in range(60)]

    assert orbit[0] == pytest.approx(
        (math.cos(0.11) * 0.12 * 0.08 * GAZE_STRENGTH_X, math.sin(0.11) * 0.07 * 0.08 * GAZE_STRENGTH_Y)
    )
    assert max(abs(x) for x, _ in orbit) <= 0.12 * GAZE_STRENGTH_X
    assert len({round(x, 6) for x, _ in orbit}) == 60

    gaze.release()
    fading = [gaze.step(1.0) for _ in range(GazeAttention.HOLD_STEPS - 1)]
    radii = [math.hypot(x / GAZE_STRENGTH_X / 0.12, y / GAZE_STRENGTH_Y / 0.07) for x, y in fading]
    assert radii == sorted(radii, reverse=True)


def test_every_expression_has_an_explicit_gaze_weight():
    assert {key: gaze_weight_for(key) for key in ExpressionKey} == {
        ExpressionKey.IDLE: 1.00,
        ExpressionKey.LISTENING: 0.95,
        ExpressionKey.THINKING: 0.45,
        ExpressionKey.SPEAKING: 0.35,
        ExpressionKey.HAPPY: 0.80,
        ExpressionKey.CONFUSED: 0.75,
        ExpressionKey.TIRED: 0.50,
        ExpressionKey.ANGRY: 0.65,
    }


@pytest.mark.parametrize("module", [attention_module, gaze_module, micro_motion_module])
def test_micro_motion_vocabulary_has_no_qt_or_ui_imports(module):
    names = imported_modules(module)
    assert not any(name == "PySide6" or name.startswith("PySide6.") for name in names)
    assert not any(name == "mira.ui" or name.startswith("mira.ui.") for name in names)
