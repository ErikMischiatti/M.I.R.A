"""The expanded notch: eyes unchanged, a symbol and short label beneath them.

Layout is checked Qt-free; painting is checked by rendering into images on
the offscreen platform.
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from mira.domain.embodiment import ActivityState
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.expanded_layout import content_row, reveal_fraction
from mira.ui.notch.eye_layout import NotchEyeStyle
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize, Rect
from mira.ui.notch.presentation import NotchPresenter, NotchSymbol
from mira.ui.notch.startup import bind_presenter
from mira.ui.notch.symbol_painter import paint_symbol
from mira.ui.notch.visual_config import NotchVisualConfig, eye_layout_for, nominal_window
from mira.ui.notch.eye_layout import production_envelope


DEFAULT = NotchGeometry()
TUNED = NotchVisualConfig(
    geometry=NotchGeometry(compact=NotchSize(240.0, 36.0, 14.0), expanded=NotchSize(380.0, 100.0, 24.0)),
    eye_style=NotchEyeStyle(height_share=0.26),
)


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


# --- Layout ----------------------------------------------------------------------


@pytest.mark.parametrize("config", [NotchVisualConfig(), TUNED], ids=["defaults", "tuned"])
def test_the_content_row_lies_below_every_eye_pose_and_inside_the_expanded_notch(config):
    geometry = config.geometry
    window = nominal_window(geometry)
    row = content_row(window, geometry)
    layout = eye_layout_for(config, production_envelope())

    assert row is not None
    for side in ("left", "right"):
        assert layout.motion_bounds(side, production_envelope()).bottom <= row.area.y
    assert row.area.y == geometry.compact.height
    assert row.area.bottom == geometry.expanded.height
    assert row.area.x >= window.width / 2.0 - geometry.expanded.width / 2.0 + geometry.expanded.corner_radius - 1e-9
    assert row.area.right <= window.width / 2.0 + geometry.expanded.width / 2.0 - geometry.expanded.corner_radius + 1e-9


def test_the_symbol_stays_small_in_a_tall_notch():
    geometry = NotchGeometry(compact=NotchSize(200, 32, 14), expanded=NotchSize(400, 300, 24))

    row = content_row(nominal_window(geometry), geometry)

    assert row.symbol_px <= 20.0
    assert row.label_px < row.symbol_px


def test_no_content_row_without_room_for_a_symbol():
    geometry = NotchGeometry(compact=NotchSize(200, 32, 14), expanded=NotchSize(360, 40, 14))

    assert content_row(nominal_window(geometry), geometry) is None


def test_the_reveal_follows_the_drawn_height():
    assert reveal_fraction(DEFAULT.compact, DEFAULT) == 0.0
    assert reveal_fraction(DEFAULT.expanded, DEFAULT) == 1.0
    assert reveal_fraction(DEFAULT.compact.lerp(DEFAULT.expanded, 0.5), DEFAULT) == pytest.approx(0.5)
    assert reveal_fraction(NotchSize(200, 0, 0), DEFAULT) == 0.0


# --- Symbols ----------------------------------------------------------------------


def lit_pixels(image: QImage, region: QRectF | None = None) -> int:
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            inside = region is None or region.contains(x + 0.5, y + 0.5)
            if inside and image.pixelColor(x, y).alpha() > 0:
                count += 1
    return count


@pytest.mark.parametrize("symbol", list(NotchSymbol))
def test_every_symbol_draws_inside_its_square_only(symbol):
    image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    square = QRectF(16, 16, 32, 32)
    painter = QPainter(image)
    paint_symbol(painter, symbol, square, QColor(255, 255, 255))
    painter.end()

    total = lit_pixels(image)
    assert total > 20
    # Round caps may spill half a stroke past the square, never more.
    stroke = 32 * 0.1
    assert lit_pixels(image, square.adjusted(-stroke, -stroke, stroke, stroke)) == total


def test_the_thinking_symbol_moves_with_its_phase_and_the_others_do_not():
    def render(symbol, phase):
        image = QImage(32, 32, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        paint_symbol(painter, symbol, QRectF(0, 0, 32, 32), QColor(255, 255, 255), phase)
        painter.end()
        return image

    assert render(NotchSymbol.THINKING, 0.0) != render(NotchSymbol.THINKING, 0.4)
    assert render(NotchSymbol.SUCCESS, 0.0) == render(NotchSymbol.SUCCESS, 0.4)


# --- The window -----------------------------------------------------------------------


@pytest.fixture
def notch():
    controller = FaceController()
    animator = FaceAnimator(controller)
    window = NotchWindow(animator=animator)
    scheduler = ManualScheduler()
    presenter = NotchPresenter(scheduler)
    bind_presenter(window, presenter)
    presenter.show()
    window.transition.stop()
    window.set_presence(NotchPresence.COMPACT, animated=False)
    window.presenter = presenter
    yield window
    presenter.shutdown()
    window.hide()
    for obj in (window, animator):
        obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def settle(window: NotchWindow) -> None:
    transition = window.transition
    if transition.state() is not transition.State.Stopped:
        transition.setCurrentTime(transition.duration())


def render(window: NotchWindow) -> QImage:
    image = QImage(window.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    window.render(image)
    return image


def row_rect(window: NotchWindow) -> QRectF:
    area = window.content_row.area
    return QRectF(area.x, area.y, area.width, area.height - 2)


def compact_band(window: NotchWindow) -> QRectF:
    return QRectF(0, 0, window.width(), window.notch_geometry.compact.height)


def content_pixels(image: QImage, window: NotchWindow) -> int:
    """Pixels in the content row brighter than the black notch fill."""
    region = row_rect(window)
    count = 0
    for y in range(int(region.top()), int(region.bottom())):
        for x in range(int(region.left()), int(region.right())):
            if image.pixelColor(x, y).lightness() > 60:
                count += 1
    return count


def test_compact_shows_eyes_only_whatever_the_activity(notch):
    notch.presenter.set_activity(ActivityState.THINKING)

    assert notch.presence is NotchPresence.COMPACT
    assert notch.content_opacity == 0.0


def test_hover_reveals_the_activity_beneath_the_eyes(notch):
    notch.presenter.set_activity(ActivityState.THINKING)
    notch.hover_changed.emit(True)
    settle(notch)

    assert notch.content_opacity == 1.0
    assert content_pixels(render(notch), notch) > 20


def test_expanded_idle_draws_nothing_beneath_the_eyes(notch):
    notch.hover_changed.emit(True)
    settle(notch)

    assert notch.content_opacity == 0.0
    assert content_pixels(render(notch), notch) == 0


def test_content_fades_with_the_outline(notch):
    notch.presenter.present(Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low disk"))
    transition = notch.transition
    transition.pause()
    transition.setCurrentTime(transition.duration() // 4)
    early = notch.content_opacity
    transition.setCurrentTime(transition.duration())

    assert 0.0 < early < notch.content_opacity == 1.0


def test_content_never_changes_the_eyes(notch):
    # Freeze the embodiment so two renders show the same frame.
    notch.animator.stop()
    notch.presenter.set_pinned(True)
    settle(notch)
    without = render(notch).copy(compact_band(notch).toRect())

    notch.presenter.present(Notice(NoticeCategory.TASK, NoticeTone.ERROR, "Failed"))
    with_content = render(notch).copy(compact_band(notch).toRect())

    assert without == with_content


def test_a_long_label_is_elided_inside_the_row(notch):
    notch.presenter.present(Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "An extremely long device name " * 6))
    settle(notch)
    image = render(notch)
    area = notch.content_row.area

    for y in range(int(area.y), int(area.bottom) - 1):
        for x in list(range(0, int(area.x) - 1)) + list(range(int(area.right) + 1, image.width())):
            assert image.pixelColor(x, y).lightness() <= 60, (x, y)
