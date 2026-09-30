"""One `FaceAnimator` steps an embodiment once per tick, whatever observes it."""

from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from mira.domain.attention import AttentionTarget
from mira.domain.embodiment_playback import REFERENCE_DT_SECONDS
from mira.domain.micro_motion import MicroMotions
from mira.domain.state import FaceState
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.face.face_widget import FaceWidget


SEED = 11


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


class CountingController(FaceController):
    def __init__(self):
        super().__init__(MicroMotions(random.Random(SEED)))
        self.updates: list[float] = []

    def update(self, dt_seconds: float):
        self.updates.append(dt_seconds)
        super().update(dt_seconds)


class CountingFaceWidget(FaceWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.paints = 0

    def paintEvent(self, event):
        self.paints += 1
        super().paintEvent(event)


def reference_frames(steps: int, arrange=lambda controller: None) -> list:
    """What an unobserved controller shows, stepped once per tick."""
    controller = FaceController(MicroMotions(random.Random(SEED)))
    arrange(controller)
    frames = []
    for _ in range(steps):
        controller.update(REFERENCE_DT_SECONDS)
        frames.append(controller.get_frame())
    return frames


def attend(controller: FaceController) -> None:
    controller.set_attention_target(AttentionTarget(0.8, -0.4))
    controller.set_state(FaceState.SPEAKING)


def render(widget: FaceWidget) -> None:
    widget.resize(400, 300)
    widget.render(QImage(400, 300, QImage.Format_ARGB32_Premultiplied))


def dispose(*objects) -> None:
    for obj in objects:
        obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def run_event_loop(milliseconds: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def test_each_tick_advances_the_controller_once_by_the_reference_interval():
    controller = CountingController()
    animator = FaceAnimator(controller)
    ticks = []
    animator.frame_advanced.connect(lambda: ticks.append(controller.get_frame()))

    for _ in range(3):
        animator.advance()

    assert controller.updates == [REFERENCE_DT_SECONDS] * 3
    assert len(ticks) == 3
    dispose(animator)


def test_the_animator_runs_only_between_start_and_stop():
    animator = FaceAnimator(CountingController())
    assert not animator.is_running()

    animator.start()
    assert animator.is_running()
    assert animator.timer.interval() == FaceAnimator.FRAME_INTERVAL_MS == 30

    animator.stop()
    assert not animator.is_running()
    dispose(animator)


def test_a_standalone_widget_owns_and_starts_its_own_animator():
    controller = CountingController()
    widget = FaceWidget(controller)

    assert widget.animator.controller is controller
    assert widget.animator.parent() is widget
    assert widget.animator.is_running()
    assert widget.frame_timer is widget.animator.timer

    widget.frame_timer.stop()
    dispose(widget)


def test_a_single_view_shows_exactly_the_unobserved_embodiment():
    controller = CountingController()
    attend(controller)
    animator = FaceAnimator(controller)
    widget = FaceWidget(animator=animator)

    shown = []
    for _ in range(200):
        animator.advance()
        render(widget)
        shown.append(widget.controller.get_frame())

    assert shown == reference_frames(200, attend)
    dispose(widget, animator)


def test_several_views_share_one_embodiment_without_stepping_it_more_often():
    controller = CountingController()
    attend(controller)
    animator = FaceAnimator(controller)
    views = [FaceWidget(animator=animator) for _ in range(3)]

    shown = []
    for _ in range(200):
        animator.advance()
        for view in views:
            render(view)
            render(view)
        shown.append(controller.get_frame())

    assert len(controller.updates) == 200
    assert shown == reference_frames(200, attend)
    assert all(view.controller is controller for view in views)
    dispose(*views, animator)


def test_a_running_shared_clock_steps_once_per_tick_with_views_attached():
    controller = CountingController()
    animator = FaceAnimator(controller)
    views = [CountingFaceWidget(animator=animator) for _ in range(3)]
    for view in views:
        view.resize(200, 150)
        view.show()
    ticks = []
    animator.frame_advanced.connect(lambda: ticks.append(None))

    animator.start()
    run_event_loop(200)
    animator.stop()

    assert ticks, "the timer never fired"
    assert len(controller.updates) == len(ticks)
    assert all(view.paints > 0 for view in views)
    dispose(*views, animator)


def test_a_view_given_an_animator_leaves_the_clock_to_its_owner():
    animator = FaceAnimator(CountingController())
    widget = FaceWidget(animator=animator)

    assert not animator.is_running()
    assert widget.animator is animator
    assert animator.parent() is None
    dispose(widget, animator)


def test_a_view_rejects_a_controller_its_animator_does_not_step():
    animator = FaceAnimator(CountingController())

    with pytest.raises(ValueError):
        FaceWidget(CountingController(), animator=animator)

    same = FaceWidget(animator.controller, animator=animator)
    assert same.controller is animator.controller
    dispose(same, animator)


def test_destroying_one_view_leaves_the_shared_clock_and_other_views_working():
    controller = CountingController()
    animator = FaceAnimator(controller)
    gone = FaceWidget(animator=animator)
    kept = CountingFaceWidget(animator=animator)
    kept.resize(200, 150)
    kept.show()

    dispose(gone)
    assert not isValid(gone)
    assert isValid(animator)

    animator.advance()
    QCoreApplication.processEvents()

    assert len(controller.updates) == 1
    assert kept.paints > 0
    dispose(kept, animator)
