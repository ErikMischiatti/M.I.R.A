"""Semantic cues: a brief overlay on MIRA's eyes that never replaces the expression.

The domain playback is checked on its own; the controller with a seeded twin
(same motions, no cue) to prove the expression underneath is untouched; the
painter by rendering into images, including inside the real notch.
"""

from __future__ import annotations

import ast
import pathlib
import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from mira.application.composition import build_application
from mira.domain.embodiment import ActivityState, EmbodimentIntent, ExpressionKey
from mira.domain.embodiment_cue import (
    CUE_TREATMENTS,
    TRANSITION_S,
    CuePlayback,
    EmbodimentCue,
    EyeShape,
    cue_for_notice,
)
from mira.domain.embodiment_frame import EmbodimentFrame, EyeFrame
from mira.domain.micro_motion import MicroMotions
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui.face.eye import Eye
from mira.ui.face.eye_painter import EyePainter
from mira.ui.face.face_controller import FaceController

REPO = pathlib.Path(__file__).resolve().parents[1]
DT = 0.03


@pytest.fixture(scope="module", autouse=True)
def application():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def run(playback_or_controller, seconds: float) -> None:
    for _ in range(round(seconds / DT)):
        if isinstance(playback_or_controller, CuePlayback):
            playback_or_controller.advance(DT)
        else:
            playback_or_controller.update(DT)


def lifetime(cue: EmbodimentCue) -> float:
    return 2 * TRANSITION_S + CUE_TREATMENTS[cue].hold_s


# --- Vocabulary and lifetime ----------------------------------------------------------


def test_notice_tones_map_to_cues_and_information_gets_none():
    assert cue_for_notice(Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)) is EmbodimentCue.SUCCESS
    assert cue_for_notice(Notice(NoticeCategory.CONNECTIVITY, NoticeTone.WARNING)) is EmbodimentCue.WARNING
    assert cue_for_notice(Notice(NoticeCategory.ALERT, NoticeTone.ERROR)) is EmbodimentCue.ERROR
    assert cue_for_notice(Notice(NoticeCategory.DEVICE, NoticeTone.INFO)) is None


def test_each_cue_has_its_shape():
    assert {cue: t.shape for cue, t in CUE_TREATMENTS.items()} == {
        EmbodimentCue.SUCCESS: EyeShape.CHEVRON,
        EmbodimentCue.WARNING: EyeShape.TRIANGLE,
        EmbodimentCue.ERROR: EyeShape.CROSS,
    }


def test_a_cue_eases_in_holds_eases_out_and_ends():
    playback = CuePlayback()
    assert playback.show(EmbodimentCue.WARNING)
    assert playback.amount == 0.0

    playback.advance(TRANSITION_S / 2)
    assert playback.amount == pytest.approx(0.5)  # the natural eye is shut
    playback.advance(TRANSITION_S / 2)
    assert playback.amount == pytest.approx(1.0)
    playback.advance(CUE_TREATMENTS[EmbodimentCue.WARNING].hold_s)
    assert playback.amount == pytest.approx(1.0)
    playback.advance(TRANSITION_S / 2)
    assert playback.amount == pytest.approx(0.5)
    playback.advance(TRANSITION_S)

    assert playback.cue is None and playback.amount == 0.0 and playback.shape is EyeShape.NATURAL


def test_higher_or_equal_priority_replaces_and_lower_is_dropped():
    playback = CuePlayback()
    playback.show(EmbodimentCue.WARNING)
    run(playback, TRANSITION_S + 0.1)

    assert playback.show(EmbodimentCue.SUCCESS) is False
    assert playback.cue is EmbodimentCue.WARNING
    assert playback.show(EmbodimentCue.ERROR) is True
    assert playback.cue is EmbodimentCue.ERROR
    # The replacement continues from the shut eye rather than popping open.
    assert playback.amount == pytest.approx(0.5)


def test_the_same_cue_holds_again_without_closing_the_eye():
    playback = CuePlayback()
    playback.show(EmbodimentCue.ERROR)
    run(playback, lifetime(EmbodimentCue.ERROR) - TRANSITION_S / 2)  # easing out
    assert 0.0 < playback.amount < 1.0
    before = playback.amount

    playback.show(EmbodimentCue.ERROR)

    assert playback.amount == pytest.approx(before)
    run(playback, CUE_TREATMENTS[EmbodimentCue.ERROR].hold_s)
    assert playback.cue is EmbodimentCue.ERROR


def test_replacement_order_is_deterministic():
    def final(sequence):
        playback = CuePlayback()
        for cue in sequence:
            playback.show(cue)
            playback.advance(DT)
        return playback.cue

    assert final([EmbodimentCue.SUCCESS, EmbodimentCue.ERROR, EmbodimentCue.WARNING]) is EmbodimentCue.ERROR
    assert final([EmbodimentCue.WARNING, EmbodimentCue.WARNING, EmbodimentCue.SUCCESS]) is EmbodimentCue.WARNING


# --- The controller keeps the expression underneath --------------------------------------


def twins():
    return FaceController(MicroMotions(random.Random(11))), FaceController(MicroMotions(random.Random(11)))


def natural(frame: EmbodimentFrame) -> EmbodimentFrame:
    from dataclasses import replace

    return EmbodimentFrame(
        replace(frame.left_eye, shape=EyeShape.NATURAL, shape_amount=0.0),
        replace(frame.right_eye, shape=EyeShape.NATURAL, shape_amount=0.0),
    )


def test_thinking_then_a_warning_returns_to_thinking():
    cued, plain = twins()
    for controller in (cued, plain):
        controller.set_intent(EmbodimentIntent(ActivityState.THINKING))
    run(cued, 0.3)
    run(plain, 0.3)

    assert cued.show_cue(EmbodimentCue.WARNING)
    run(cued, 0.5)
    run(plain, 0.5)
    shown = cued.get_frame()
    assert shown.left_eye.shape is EyeShape.TRIANGLE and shown.left_eye.shape_amount == 1.0
    # Underneath, nothing changed: same intent, expression and motion.
    assert cued.intent == plain.intent and cued.expression_key is ExpressionKey.THINKING
    assert natural(shown) == plain.get_frame()

    run(cued, lifetime(EmbodimentCue.WARNING))
    run(plain, lifetime(EmbodimentCue.WARNING))
    assert cued.active_cue is None
    assert cued.get_frame() == plain.get_frame()


def test_an_expression_change_during_a_cue_is_what_returns():
    controller = FaceController(MicroMotions(random.Random(3)))
    controller.set_intent(EmbodimentIntent(ActivityState.THINKING))
    controller.show_cue(EmbodimentCue.ERROR)
    run(controller, 0.5)

    controller.set_intent(EmbodimentIntent(ActivityState.LISTENING))
    run(controller, lifetime(EmbodimentCue.ERROR))

    assert controller.active_cue is None
    assert controller.expression_key is ExpressionKey.LISTENING
    assert controller.get_frame().left_eye.shape is EyeShape.NATURAL


def test_without_a_cue_frames_are_unchanged():
    cued, plain = twins()
    run(cued, 0.4)
    run(plain, 0.4)

    assert cued.get_frame() == plain.get_frame()
    assert cued.get_frame().left_eye.shape_amount == 0.0


# --- Rendering --------------------------------------------------------------------------

EYE_COLOR = QColor(245, 245, 245)
LEFT = Eye(x_ratio=0.2, y_ratio=0.3, width_ratio=0.18, height_ratio=0.22)
RIGHT = Eye(x_ratio=0.62, y_ratio=0.3, width_ratio=0.18, height_ratio=0.22)


def render(frame: EmbodimentFrame, size=(700, 450)) -> tuple[QImage, EyePainter, QRectF]:
    image = QImage(*size, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = EyePainter(LEFT, RIGHT, eye_color=EYE_COLOR, lid_color=QColor(0, 0, 0))
    canvas = QRectF(0, 0, *size)
    qp = QPainter(image)
    painter.paint(qp, canvas, frame)
    qp.end()
    return image, painter, canvas


def lit(image: QImage):
    return {(x, y) for y in range(0, image.height(), 2) for x in range(0, image.width(), 2)
            if image.pixelColor(x, y).alpha() > 16}


def cue_frame(shape: EyeShape, amount: float, **eye) -> EmbodimentFrame:
    return EmbodimentFrame(EyeFrame(shape=shape, shape_amount=amount, **eye),
                           EyeFrame(shape=shape, shape_amount=amount, **eye))


@pytest.mark.parametrize("shape", [EyeShape.TRIANGLE, EyeShape.CROSS, EyeShape.CHEVRON])
@pytest.mark.parametrize("amount", [0.2, 0.5, 0.75, 1.0])
def test_cue_eyes_are_drawn_only_inside_the_eye_rectangles(shape, amount):
    frame = cue_frame(shape, amount, offset_x=0.03, offset_y=-0.02)
    image, painter, canvas = render(frame)
    rects = [painter.eye_rect(canvas, LEFT, frame.left_eye), painter.eye_rect(canvas, RIGHT, frame.right_eye)]

    outside = [p for p in lit(image) if not any(r.adjusted(-1, -1, 1, 1).contains(*p) for r in rects)]

    assert outside == []


@pytest.mark.parametrize("shape", [EyeShape.TRIANGLE, EyeShape.CROSS, EyeShape.CHEVRON])
def test_each_cue_is_visibly_different_from_the_natural_eye_and_from_each_other(shape):
    natural_pixels = lit(render(EmbodimentFrame(EyeFrame(), EyeFrame()))[0])
    shaped = lit(render(cue_frame(shape, 1.0))[0])
    others = [lit(render(cue_frame(other, 1.0))[0]) for other in (EyeShape.TRIANGLE, EyeShape.CROSS, EyeShape.CHEVRON)
              if other is not shape]

    assert shaped and len(shaped ^ natural_pixels) > len(natural_pixels) * 0.3
    assert all(shaped != other for other in others)


def test_the_cross_reads_as_an_x_and_the_triangle_points_up():
    image, painter, canvas = render(cue_frame(EyeShape.CROSS, 1.0))
    rect = painter.eye_rect(canvas, LEFT, EyeFrame())
    lit_at = lambda fx, fy: image.pixelColor(int(rect.x() + rect.width() * fx), int(rect.y() + rect.height() * fy)).alpha() > 16
    assert lit_at(0.5, 0.5) and lit_at(0.2, 0.2) and lit_at(0.8, 0.8)
    assert not lit_at(0.5, 0.1)

    image, painter, canvas = render(cue_frame(EyeShape.TRIANGLE, 1.0))
    assert lit_at(0.5, 0.2) and not lit_at(0.1, 0.2) and lit_at(0.15, 0.95)


def test_a_cued_eye_ignores_blinks_and_eyelids():
    shaped = lit(render(cue_frame(EyeShape.CROSS, 1.0))[0])
    blinking = lit(render(cue_frame(EyeShape.CROSS, 1.0, closed=True, angry_lid=0.6, happy_lid=0.4))[0])

    assert shaped == blinking


# --- In the notch ------------------------------------------------------------------------


@pytest.mark.parametrize("presence", ["compact", "expanded"])
@pytest.mark.parametrize("cue", list(EmbodimentCue))
def test_cue_eyes_stay_inside_the_notch_and_off_the_content(presence, cue):
    from mira.domain.scheduler import ManualScheduler
    from mira.ui.face.face_animator import FaceAnimator
    from mira.ui.notch.eye_layout import production_envelope
    from mira.ui.notch.notch_window import NotchWindow
    from mira.ui.notch.presence import NotchPresence
    from mira.ui.notch.presentation import NotchPresenter
    from mira.ui.notch.startup import bind_presenter

    controller = FaceController(MicroMotions(random.Random(2)))
    animator = FaceAnimator(controller)
    notch = NotchWindow(animator=animator)
    presenter = NotchPresenter(ManualScheduler())
    bind_presenter(notch, presenter)
    try:
        presenter.show()
        presenter.set_pinned(presence == "expanded")
        notch.transition.setCurrentTime(notch.transition.duration())
        controller.show_cue(cue)
        run(controller, TRANSITION_S + 0.06)
        assert controller.get_frame().left_eye.shape_amount == 1.0

        image = notch.grab().toImage()
        envelope = production_envelope()
        bounds = [notch.eye_layout.motion_bounds(side, envelope) for side in ("left", "right")]
        row = notch.content_row
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() < 200 or color.lightness() < 200:
                    continue  # the notch fill, or nothing
                in_eyes = any(b.x - 1 <= x <= b.right + 1 and b.y - 1 <= y <= b.bottom + 1 for b in bounds)
                assert in_eyes, (x, y)
                assert not (row.area.y <= y <= row.area.bottom and presence == "expanded" and not in_eyes)
        assert notch.presence is (NotchPresence.EXPANDED if presence == "expanded" else NotchPresence.COMPACT)
    finally:
        presenter.shutdown()
        notch.hide()
        for obj in (notch, animator):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- Ownership -----------------------------------------------------------------------------


def imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {(node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}


def test_cues_belong_to_the_embodiment_not_to_the_notch():
    assert not any(name.startswith(("mira.ui", "PySide6")) for name in imports_of(REPO / "mira/domain/embodiment_cue.py"))
    assert not any("notch" in name for name in imports_of(REPO / "mira/ui/face/face_controller.py"))
    notch_source = (REPO / "mira/ui/notch/notch_window.py").read_text()
    assert "show_cue" not in notch_source and "CuePlayback" not in notch_source


@pytest.mark.parametrize("mode", ["main window", "notch"])
def test_a_notice_on_the_bus_makes_the_shared_eyes_react_on_every_surface(mode):
    from mira.ui.desktop_shell import DesktopShell, ShellMode

    application = build_application(scheduler=ManualScheduler())
    shell = DesktopShell(application, mode=ShellMode(mode))
    try:
        application.event_bus.emit("notice", Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Mouse connected"))
        assert shell.controller.active_cue is None

        application.event_bus.emit("action_failed", object())  # a real producer: TaskNoticeProducer
        assert shell.controller.active_cue is EmbodimentCue.ERROR
        assert shell.controller.intent.activity is ActivityState.IDLE
    finally:
        shell.shutdown()
        application.shutdown()
        for obj in (shell.notch, shell.window, shell.animator):
            if obj is not None:
                obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
