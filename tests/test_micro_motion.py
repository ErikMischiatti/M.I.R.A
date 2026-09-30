"""Renderer-independent micro-motion and attention vocabulary."""

from __future__ import annotations

import ast
import random
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mira.domain.attention as attention_module
import mira.domain.micro_motion as micro_motion_module
from mira.domain.attention import AttentionTarget
from mira.domain.embodiment_playback import PlaybackPose
from mira.domain.micro_motion import BlinkMotion, BlinkSettings, MicroMotionState


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


@pytest.mark.parametrize("module", [attention_module, micro_motion_module])
def test_micro_motion_vocabulary_has_no_qt_or_ui_imports(module):
    names = imported_modules(module)
    assert not any(name == "PySide6" or name.startswith("PySide6.") for name in names)
    assert not any(name == "mira.ui" or name.startswith("mira.ui.") for name in names)
