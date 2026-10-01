"""The notch's visual parameters: Qt-free derivation, then run-time replacement."""

from __future__ import annotations

import ast
import pathlib

import pytest

from mira.domain.embodiment_frame import EyeFrame
from mira.ui.notch.eye_layout import (
    BASE_EYE_HEIGHT_SHARE,
    EYE_SPACING_UNITS,
    NotchEyeLayout,
    NotchEyeStyle,
    notch_eye_layout,
)
from mira.ui.notch.presence import (
    NotchGeometry,
    NotchPresence,
    NotchShape,
    NotchSize,
    Rect,
    window_bounds,
    window_shape,
)


AREA = Rect(0.0, 32.0, 1920.0, 1048.0)
WINDOW = Rect(0.0, 0.0, 360.0, 96.0)
GEOMETRY = NotchGeometry(
    compact=NotchSize(width=200.0, height=32.0, corner_radius=14.0),
    expanded=NotchSize(width=360.0, height=96.0, corner_radius=24.0),
)
H, C, E = NotchPresence.HIDDEN, NotchPresence.COMPACT, NotchPresence.EXPANDED


# --- Horizontal offset (Qt-free) -------------------------------------------------


def test_the_default_geometry_has_no_horizontal_offset():
    assert NotchGeometry().offset_x == 0.0


@pytest.mark.parametrize("offset", [-150.0, 40.0])
def test_the_horizontal_offset_moves_the_window_after_centring(offset):
    moved = NotchGeometry(compact=GEOMETRY.compact, expanded=GEOMETRY.expanded, offset_x=offset)

    assert window_bounds(AREA, moved).x == window_bounds(AREA, GEOMETRY).x + offset
    assert window_bounds(AREA, moved).y == window_bounds(AREA, GEOMETRY).y


def test_a_fractional_offset_follows_the_floor_policy():
    moved = NotchGeometry(compact=GEOMETRY.compact, expanded=GEOMETRY.expanded, offset_x=0.5)

    # (1920 - 360) / 2 + 0.5 = 780.5 floors to 780.
    assert window_bounds(AREA, moved).x == 780


def test_the_offset_moves_the_area_placement_but_not_the_window_local_shape():
    moved = NotchGeometry(compact=GEOMETRY.compact, expanded=GEOMETRY.expanded, offset_x=30.0)

    assert moved.shape_for(AREA, C).bounds.x == GEOMETRY.shape_for(AREA, C).bounds.x + 30.0
    window = window_bounds(AREA, moved)
    assert window_shape(window, moved, C) == window_shape(window, GEOMETRY, C)


# --- Eye style (Qt-free) ------------------------------------------------------------


def test_the_default_style_is_the_production_layout():
    style = NotchEyeStyle()
    layout = notch_eye_layout(GEOMETRY, WINDOW)

    assert (style.height_share, style.top_offset, style.center_offset, style.spacing) == (
        BASE_EYE_HEIGHT_SHARE,
        0.0,
        0.0,
        EYE_SPACING_UNITS,
    )
    assert notch_eye_layout(GEOMETRY, WINDOW, style) == layout
    assert layout == NotchEyeLayout(scale=12.0 / 99.0, center_x=180.0, top=4.0)


def test_the_height_share_sets_the_face_scale():
    layout = notch_eye_layout(GEOMETRY, WINDOW, NotchEyeStyle(height_share=0.5))

    assert layout.scale == pytest.approx(16.0 / 99.0)
    # The tallest standard eye (32 px) stays centred in the compact notch.
    assert layout.top == pytest.approx(0.0)


def test_offsets_move_the_eyes_from_their_derived_position():
    layout = notch_eye_layout(GEOMETRY, WINDOW, NotchEyeStyle(top_offset=6.0, center_offset=-20.0))

    assert layout.top == 10.0
    assert layout.center_x == 160.0


def test_the_spacing_reaches_the_layout():
    layout = notch_eye_layout(GEOMETRY, WINDOW, NotchEyeStyle(spacing=420.0))

    assert layout.spacing == 420.0
    right, left = layout.resting_eye("right"), layout.resting_eye("left")
    assert right.center_x - left.center_x == pytest.approx(420.0 * layout.scale)


@pytest.mark.parametrize("kwargs", [{"height_share": 0.0}, {"height_share": -0.1}, {"spacing": -1.0}])
def test_impossible_eye_styles_are_rejected(kwargs):
    with pytest.raises(ValueError):
        NotchEyeStyle(**kwargs)


def test_the_eye_layout_module_imports_no_qt():
    source = pathlib.Path(__file__).resolve().parents[1] / "mira/ui/notch/eye_layout.py"
    tree = ast.parse(source.read_text())
    imported = {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

    assert "PySide6" not in imported


# --- Run-time replacement (Qt) ------------------------------------------------------

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mira.ui.notch.notch_window import NotchWindow  # noqa: E402


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(application):
    notch = NotchWindow(GEOMETRY)
    yield notch
    notch.transition.stop()
    notch.hide()
    notch.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def with_sizes(compact=GEOMETRY.compact, expanded=GEOMETRY.expanded, **kwargs) -> NotchGeometry:
    return NotchGeometry(compact=compact, expanded=expanded, **kwargs)


def test_replacing_the_compact_size_at_rest_applies_at_once(window):
    window.set_presence(C, animated=False)

    window.set_notch_geometry(with_sizes(compact=NotchSize(240.0, 40.0, 16.0)))

    assert window.visual_size == NotchSize(240.0, 40.0, 16.0)
    assert window.shape.bounds.width == 240.0 and window.shape.bounds.height == 40.0
    assert window.mask().boundingRect().width() >= 240
    assert window.transition.state() is QAbstractAnimation.State.Stopped


def test_replacing_the_expanded_size_resizes_the_fixed_window(window):
    window.set_presence(E, animated=False)
    centre = window.x() + window.width() / 2.0

    window.set_notch_geometry(with_sizes(expanded=NotchSize(420.0, 120.0, 30.0)))

    assert (window.width(), window.height()) == (420, 120)
    assert window.minimumSize() == window.maximumSize() == window.size()
    assert window.visual_size == NotchSize(420.0, 120.0, 30.0)
    assert window.x() + window.width() / 2.0 == centre


def test_the_placement_offsets_move_the_window(window):
    before = (window.x(), window.y())

    window.set_notch_geometry(with_sizes(offset_x=-100.0, top_margin=12.0))

    assert (window.x(), window.y()) == (before[0] - 100, before[1] + 12)


def test_replacing_the_geometry_keeps_the_selected_screen(window):
    screen = window.tracked_screen

    window.set_notch_geometry(with_sizes(expanded=NotchSize(500.0, 140.0, 30.0), offset_x=50.0))

    assert window.tracked_screen is screen


def test_a_hidden_notch_stays_hidden_when_its_geometry_changes(window):
    window.set_notch_geometry(with_sizes(compact=NotchSize(260.0, 36.0, 12.0)))

    assert not window.isVisible()
    assert window.visual_size == NotchSize(260.0, 0.0)


def test_a_change_mid_transition_continues_the_one_transition_without_a_jump(window):
    window.set_presence(C, animated=False)
    window.expand_notch()
    window.transition.pause()
    window.transition.setCurrentTime(80)
    drawn = window.visual_size
    animation = window.transition

    window.set_notch_geometry(with_sizes(expanded=NotchSize(420.0, 120.0, 30.0)))

    assert window.transition is animation
    assert window.transition.state() is QAbstractAnimation.State.Running
    assert window.transition.duration() == 200 - 80
    assert window.visual_size == drawn
    window.transition.setCurrentTime(window.transition.duration())
    assert window.visual_size == NotchSize(420.0, 120.0, 30.0)
    assert window.presence is E


def test_a_change_during_a_close_still_hides_at_the_end(window):
    window.set_presence(C, animated=False)
    window.hide_notch()
    window.transition.pause()
    window.transition.setCurrentTime(100)

    window.set_notch_geometry(with_sizes(compact=NotchSize(240.0, 40.0, 16.0)))
    assert window.isVisible()
    window.transition.setCurrentTime(window.transition.duration())

    assert not window.isVisible()
    assert window.visual_size.height == 0.0


def test_the_transition_duration_is_a_run_time_setting(window):
    assert window.transition_duration_ms == NotchWindow.TRANSITION_DURATION_MS

    window.transition_duration_ms = 500
    window.show_notch()

    assert window.transition.duration() == 500


def test_replacing_the_eye_style_re_derives_the_painted_layout(window):
    window.set_presence(C, animated=False)
    canvas = window.eye_canvas

    window.set_eye_style(NotchEyeStyle(height_share=0.5, top_offset=3.0, center_offset=10.0, spacing=360.0))

    layout = window.eye_layout
    assert layout == notch_eye_layout(GEOMETRY, Rect(0, 0, window.width(), window.height()), window.eye_style)
    assert window.eye_canvas != canvas
    left = window.eye_painter.eye_rect(window.eye_canvas, window.eye_painter.left, EyeFrame())
    resting = layout.resting_eye("left")
    assert (left.x(), left.y(), left.height()) == pytest.approx((resting.x, resting.y, resting.height))


def test_a_compact_height_change_rescales_the_derived_eyes(window):
    scale = window.eye_layout.scale

    window.set_notch_geometry(with_sizes(compact=NotchSize(200.0, 48.0, 14.0)))

    assert window.eye_layout.scale == pytest.approx(scale * 48.0 / 32.0)
