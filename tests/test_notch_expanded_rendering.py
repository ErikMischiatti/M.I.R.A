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
from mira.ui.notch.expanded_layout import (
    NotchContentStyle,
    content_problems,
    content_row,
    corner_inset,
    reveal_fraction,
)
from mira.ui.notch.eye_layout import NotchEyeStyle
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize, Rect
from mira.ui.notch.presentation import NotchPresenter, NotchSymbol
from mira.ui.notch.startup import bind_presenter
from mira.ui.notch.symbol_painter import paint_symbol
from mira.ui.notch.visual_config import (
    InvalidVisualConfig,
    NotchVisualConfig,
    content_row_for,
    eye_layout_for,
    eye_motion_bottom,
    nominal_window,
    visual_config_problems,
)
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


def with_content(config: NotchVisualConfig, **content) -> NotchVisualConfig:
    return dataclasses.replace(config, content_style=NotchContentStyle(**content))


@pytest.mark.parametrize("config", [NotchVisualConfig(), TUNED], ids=["defaults", "tuned"])
def test_the_content_lies_below_every_eye_pose_and_inside_the_expanded_notch(config):
    envelope = production_envelope()
    geometry = config.geometry
    window = nominal_window(geometry)
    row = content_row_for(config)
    layout = eye_layout_for(config, envelope)

    for side in ("left", "right"):
        assert layout.motion_bounds(side, envelope).bottom <= row.area.y
    assert row.area.y == pytest.approx(eye_motion_bottom(layout, envelope) + config.content_style.eye_gap_px)
    assert row.area.bottom <= geometry.expanded.height - 1.0
    assert row.area.x >= window.width / 2.0 - geometry.expanded.width / 2.0
    assert row.area.right <= window.width / 2.0 + geometry.expanded.width / 2.0
    assert visual_config_problems(config) == []


def test_the_defaults_are_larger_than_the_old_fixed_layout():
    row = content_row_for(NotchVisualConfig())

    # The old layout capped the symbol at 20 px and drew the label at 72 % of it.
    assert row.symbol_px > 20.0
    assert row.label_px > 14.4


def test_the_style_sizes_the_content():
    config = with_content(TUNED, symbol_px=30.0, text_px=22.0, spacing_px=14.0, eye_gap_px=4.0)

    row = content_row_for(config)

    assert (row.symbol_px, row.label_px, row.gap_px) == (30.0, 22.0, 14.0)
    assert row.area.height == pytest.approx(max(30.0, 22.0 * 1.25))


def test_the_gap_moves_the_content_relative_to_the_eyes():
    near = content_row_for(with_content(TUNED, eye_gap_px=0.0))
    far = content_row_for(with_content(TUNED, eye_gap_px=20.0))

    assert far.area.y - near.area.y == pytest.approx(20.0)


def test_moving_the_eyes_moves_the_content_with_them():
    base = content_row_for(TUNED)
    lowered = content_row_for(dataclasses.replace(TUNED, eye_style=dataclasses.replace(TUNED.eye_style, top_offset=-2.0)))

    assert base.area.y - lowered.area.y == pytest.approx(2.0)


def test_content_below_the_notch_is_invalid_and_never_applied(application):
    too_low = with_content(TUNED, eye_gap_px=80.0)
    problems = visual_config_problems(too_low)

    assert any("leaves the notch bottom" in problem for problem in problems)
    notch = NotchWindow()
    try:
        notch.apply_visual_config(TUNED)
        with pytest.raises(InvalidVisualConfig):
            notch.apply_visual_config(too_low)
        # The last valid configuration stays.
        assert notch.visual_config == TUNED
    finally:
        notch.deleteLater()


def test_a_symbol_wider_than_the_room_between_the_corners_is_invalid():
    geometry = NotchGeometry(compact=NotchSize(60, 32, 14), expanded=NotchSize(60, 130, 28))
    config = with_content(NotchVisualConfig(geometry=geometry), symbol_px=60.0, text_px=12.0, eye_gap_px=0.0)

    problems = content_problems(content_row_for(config), geometry)

    assert any("wider than the room" in problem for problem in problems)


def test_the_rounded_corners_narrow_the_content_near_the_bottom():
    size = NotchSize(360.0, 96.0, 24.0)

    assert corner_inset(size, 10.0, 60.0) == 0.0
    assert corner_inset(size, 10.0, 96.0) == pytest.approx(24.0)
    assert 0.0 < corner_inset(size, 10.0, 84.0) < 24.0


@pytest.mark.parametrize("field, value", [
    ("symbol_px", 4.0), ("symbol_px", 13.0), ("symbol_px", 200.0), ("text_px", 2.0), ("text_px", 11.0),
    ("spacing_px", -1.0),
    ("eye_gap_px", -0.5), ("eye_gap_px", float("nan")),
])
def test_impossible_content_values_are_rejected(field, value):
    with pytest.raises(ValueError):
        NotchContentStyle(**{field: value})


def test_larger_content_paints_more_pixels(application):
    def lit_content(config):
        notch = NotchWindow()
        try:
            notch.apply_visual_config(config)
            presenter = NotchPresenter(ManualScheduler())
            bind_presenter(notch, presenter)
            presenter.show()
            presenter.set_pinned(True)
            notch.transition.setCurrentTime(notch.transition.duration())
            presenter.present(Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low battery"))
            image = notch.grab().toImage()
            row = notch.content_row
            count = 0
            for y in range(int(row.area.y), min(int(row.area.bottom) + 1, image.height())):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    if color.alpha() > 0 and color.lightness() > 128:
                        count += 1
            presenter.shutdown()
            return count
        finally:
            notch.hide()
            notch.deleteLater()

    small = lit_content(with_content(TUNED, symbol_px=14.0, text_px=12.0, spacing_px=6.0, eye_gap_px=10.0))
    large = lit_content(with_content(TUNED, symbol_px=30.0, text_px=22.0, spacing_px=10.0, eye_gap_px=10.0))

    assert small > 0
    assert large > 2 * small


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
