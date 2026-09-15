"""Unit tests for the pure embodiment playback boundary."""

from __future__ import annotations

import ast
import math
from dataclasses import astuple
from pathlib import Path

import pytest

import mira.domain.embodiment_playback as playback_module
from mira.domain.embodiment_frame import EyeFrame
from mira.domain.embodiment_playback import (
    REFERENCE_DT_SECONDS,
    EmbodimentPlayback,
    EyeAsymmetry,
    PlaybackPose,
)


TARGET = PlaybackPose(
    offset_x=10.0,
    offset_y=-20.0,
    width_scale=1.5,
    height_scale=0.5,
    corner_radius=48.0,
    eyelid_tired=0.2,
    eyelid_angry=0.4,
    eyelid_happy=0.6,
)


def test_initial_pose_and_frame_match_the_legacy_neutral_values():
    playback = EmbodimentPlayback()

    assert playback.current_pose == PlaybackPose()
    assert playback.get_frame().left_eye == EyeFrame()
    assert playback.get_frame().right_eye == EyeFrame()


def test_reference_timestep_uses_the_exact_legacy_property_alphas():
    playback = EmbodimentPlayback()

    frame = playback.advance(TARGET, REFERENCE_DT_SECONDS)

    assert playback.current_pose == PlaybackPose(
        offset_x=0.0 + (10.0 - 0.0) * 0.10,
        offset_y=0.0 + (-20.0 - 0.0) * 0.10,
        width_scale=1.0 + (1.5 - 1.0) * 0.07,
        height_scale=1.0 + (0.5 - 1.0) * 0.07,
        corner_radius=28.0 + (48.0 - 28.0) * 0.10,
        eyelid_tired=0.0 + (0.2 - 0.0) * 0.10,
        eyelid_angry=0.0 + (0.4 - 0.0) * 0.10,
        eyelid_happy=0.0 + (0.6 - 0.0) * 0.10,
    )
    assert frame.left_eye.offset_x * 700.0 == pytest.approx(1.0)
    assert frame.left_eye.offset_y * 450.0 == pytest.approx(-2.0)


def test_zero_dt_returns_a_frame_without_changing_the_pose():
    initial = PlaybackPose(offset_x=3.0, height_scale=0.8)
    playback = EmbodimentPlayback(initial)

    frame = playback.advance(TARGET, 0.0)

    assert playback.current_pose is initial
    assert frame.left_eye.offset_x * 700.0 == pytest.approx(3.0)
    assert frame.left_eye.height_scale == pytest.approx(0.8)


def test_multiple_advances_follow_closed_form_convergence():
    playback = EmbodimentPlayback()
    step_count = 6

    for _ in range(step_count):
        playback.advance(TARGET, REFERENCE_DT_SECONDS)

    offset_progress = 1.0 - 0.9**step_count
    scale_progress = 1.0 - 0.93**step_count
    assert playback.current_pose.offset_x == pytest.approx(
        TARGET.offset_x * offset_progress
    )
    assert playback.current_pose.height_scale == pytest.approx(
        1.0 + (TARGET.height_scale - 1.0) * scale_progress
    )


def test_splitting_elapsed_time_is_coherent_for_a_static_target():
    whole = EmbodimentPlayback()
    split = EmbodimentPlayback()

    whole.advance(TARGET, REFERENCE_DT_SECONDS)
    split.advance(TARGET, REFERENCE_DT_SECONDS / 2.0)
    split.advance(TARGET, REFERENCE_DT_SECONDS / 2.0)

    assert astuple(split.current_pose) == pytest.approx(astuple(whole.current_pose))


def test_target_replacement_continues_from_the_current_pose():
    playback = EmbodimentPlayback()
    playback.advance(TARGET, REFERENCE_DT_SECONDS)
    replacement = PlaybackPose(offset_x=-10.0, height_scale=1.5)

    playback.advance(replacement, REFERENCE_DT_SECONDS)

    assert playback.current_pose.offset_x == pytest.approx(-0.1)
    assert playback.current_pose.height_scale == pytest.approx(1.00245)


def test_identical_inputs_produce_identical_frame_sequences():
    first = EmbodimentPlayback()
    second = EmbodimentPlayback()
    elapsed = [0.0, 0.010, 0.020, 0.030, 0.075]

    first_frames = [first.advance(TARGET, dt) for dt in elapsed]
    second_frames = [second.advance(TARGET, dt) for dt in elapsed]

    assert first_frames == second_frames
    assert first.current_pose == second.current_pose


def test_left_right_asymmetry_is_resolved_after_shared_interpolation():
    playback = EmbodimentPlayback()
    asymmetry = EyeAsymmetry(
        offset_y_left=-6.0,
        offset_y_right=6.0,
        height_left=1.05,
        height_right=0.82,
    )

    frame = playback.advance(
        PlaybackPose(offset_y=2.0, height_scale=0.88),
        REFERENCE_DT_SECONDS,
        asymmetry=asymmetry,
        left_eye_closed=True,
    )

    assert frame.left_eye.offset_y * 450.0 == pytest.approx(-5.8)
    assert frame.right_eye.offset_y * 450.0 == pytest.approx(6.2)
    assert frame.left_eye.height_scale == pytest.approx(0.9916 * 1.05)
    assert frame.right_eye.height_scale == pytest.approx(0.9916 * 0.82)
    assert frame.left_eye.closed is True
    assert frame.right_eye.closed is False


@pytest.mark.parametrize("dt_seconds", [-0.001, math.inf, -math.inf, math.nan])
def test_invalid_elapsed_time_is_rejected_without_mutation(dt_seconds):
    playback = EmbodimentPlayback()

    with pytest.raises(ValueError, match="finite and non-negative"):
        playback.advance(TARGET, dt_seconds)

    assert playback.current_pose == PlaybackPose()


def test_playback_module_has_no_qt_or_ui_imports():
    source = Path(playback_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert not any(name == "PySide6" or name.startswith("PySide6.") for name in imported_modules)
    assert not any(name == "mira.ui" or name.startswith("mira.ui.") for name in imported_modules)
    assert "random" not in imported_modules
