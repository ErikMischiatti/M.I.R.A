"""The Qt face turns cursor input into renderer-independent attention."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from mira.domain.attention import AttentionTarget
from mira.ui.face.face_widget import FaceWidget, cursor_attention_target


CANVAS = QRectF(100.0, 50.0, 700.0, 450.0)


@pytest.mark.parametrize(
    "point,expected",
    [
        ((450.0, 275.0), (0.0, 0.0)),
        ((100.0, 50.0), (-1.0, -1.0)),
        ((800.0, 500.0), (1.0, 1.0)),
        ((625.0, 162.5), (0.5, -0.5)),
    ],
)
def test_canvas_positions_map_onto_the_normalized_view_space(point, expected):
    target = cursor_attention_target(QPointF(*point), CANVAS)
    assert (target.x, target.y) == pytest.approx(expected)


def test_positions_beyond_the_canvas_attend_to_its_nearest_edge():
    assert cursor_attention_target(QPointF(0.0, 900.0), CANVAS) == AttentionTarget(-1.0, 1.0)


def test_an_empty_canvas_yields_no_attention():
    assert cursor_attention_target(QPointF(10.0, 10.0), QRectF()) is None


@pytest.fixture
def widget(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    face = FaceWidget()
    face.resize(1000, 450)
    yield face
    face.frame_timer.stop()
    face.close()
    application.processEvents()


def move_to(widget: FaceWidget, x: float, y: float) -> None:
    point = QPointF(x, y)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        point,
        widget.mapToGlobal(point),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(event)


def test_cursor_movement_reaches_the_controller_as_an_attention_target(widget):
    # 1000×450 letterboxes the 700×450 design canvas 150 px from the left.
    move_to(widget, 150.0 + 525.0, 112.5)

    target = widget.controller.attention_target
    assert (target.x, target.y) == pytest.approx((0.5, -0.5))


def test_cursor_leaving_the_face_releases_attention(widget):
    move_to(widget, 500.0, 225.0)
    assert widget.controller.attention_target is not None

    widget.leaveEvent(QEvent(QEvent.Type.Leave))

    assert widget.controller.attention_target is None
