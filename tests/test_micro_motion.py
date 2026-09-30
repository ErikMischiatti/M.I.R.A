"""Renderer-independent micro-motion and attention vocabulary."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mira.domain.attention as attention_module
import mira.domain.micro_motion as micro_motion_module
from mira.domain.attention import AttentionTarget
from mira.domain.embodiment_playback import PlaybackPose
from mira.domain.micro_motion import MicroMotionState


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


@pytest.mark.parametrize("module", [attention_module, micro_motion_module])
def test_micro_motion_vocabulary_has_no_qt_or_ui_imports(module):
    names = imported_modules(module)
    assert not any(name == "PySide6" or name.startswith("PySide6.") for name in names)
    assert not any(name == "mira.ui" or name.startswith("mira.ui.") for name in names)
