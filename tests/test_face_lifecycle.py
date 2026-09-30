"""Ownership and lifecycle of the face's micro-motion components."""

from __future__ import annotations

import random

import pytest

from mira.domain.attention import AttentionTarget
from mira.domain.embodiment_playback import PlaybackPose
from mira.domain.micro_motion import MicroMotions
from mira.domain.state import FaceState
from mira.ui.face.face_controller import FaceController


TICK = 0.030


def scripted_run(controller: FaceController) -> list:
    frames = []
    for step in range(300):
        if step == 20:
            controller.set_attention_target(AttentionTarget(0.7, -0.3))
        if step == 120:
            controller.clear_attention_target()
        if step == 180:
            controller.set_state(FaceState.SPEAKING)
        controller.update(TICK)
        frames.append(controller.get_frame())
    return frames


def test_injected_seeded_motions_are_reproducible_without_global_randomness():
    global_state = random.getstate()

    first = scripted_run(FaceController(MicroMotions(random.Random(42))))
    second = scripted_run(FaceController(MicroMotions(random.Random(42))))

    assert first == second
    assert random.getstate() == global_state


def test_different_seeds_produce_different_motion():
    first = scripted_run(FaceController(MicroMotions(random.Random(1))))
    second = scripted_run(FaceController(MicroMotions(random.Random(2))))

    assert first != second


def test_each_controller_owns_independent_micro_motions():
    watched = FaceController(MicroMotions(random.Random(0)))
    other = FaceController(MicroMotions(random.Random(0)))

    watched.set_attention_target(AttentionTarget(1.0, 0.0))
    for _ in range(5):
        watched.update(TICK)
        other.update(TICK)

    assert other.attention_target is None
    assert watched.target_pose != other.target_pose


class RecordingRandom(random.Random):
    def __init__(self, seed: int):
        super().__init__(seed)
        self.calls: list[str] = []

    def randint(self, a: int, b: int) -> int:
        self.calls.append("randint")
        return super().randint(a, b)

    def uniform(self, a: float, b: float) -> float:
        self.calls.append("uniform")
        return super().uniform(a, b)


def test_startup_draws_only_the_first_blink_interval_and_moves_nothing():
    rng = RecordingRandom(0)
    controller = FaceController(MicroMotions(rng))

    assert rng.calls == ["randint"]
    assert controller.target_pose == PlaybackPose.from_definition(
        controller.profile.to_definition()
    )
    assert controller.eyes_closed is False


@pytest.fixture
def qt_application(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_face_widget_drives_the_injected_controller(qt_application):
    from mira.ui.face.face_widget import FaceWidget

    controller = FaceController(MicroMotions(random.Random(3)))
    widget = FaceWidget(controller)
    try:
        assert widget.controller is controller
        before = controller.current_pose
        widget.animator.advance()
        assert controller.current_pose != before
    finally:
        widget.frame_timer.stop()
        widget.close()


def test_the_frame_timer_is_the_only_clock_and_dies_with_its_widget(qt_application):
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from shiboken6 import isValid

    from mira.ui.face.face_widget import FaceWidget

    widget = FaceWidget()
    assert widget.findChildren(QTimer) == [widget.frame_timer]
    assert widget.frame_timer.isActive()

    timer = widget.frame_timer
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert not isValid(timer)
