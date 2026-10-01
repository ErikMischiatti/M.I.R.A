"""The notch's vector symbols, drawn with Qt painting primitives only.

No emoji, fonts or icon files: every symbol is a few strokes on a square, so
it renders identically on every platform and stays crisp at any size. One
visual language throughout — monochrome, round caps and joins, one stroke
width proportional to the square — so new symbols added later match.

Each symbol is a function of the painter, the square it fills, the colour
and a `phase` in seconds. Only THINKING uses the phase today (a restrained
three-dot sequence); the parameter exists so any symbol can be animated
later without changing callers. Adding a symbol means adding a
`NotchSymbol` member and one drawing function to `_DRAW`.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from mira.ui.notch.presentation import NotchSymbol


STROKE_SHARE = 0.1


def paint_symbol(painter: QPainter, symbol: NotchSymbol, rect: QRectF, color: QColor, phase: float = 0.0) -> None:
    """Draw ``symbol`` centred in the largest square inside ``rect``."""

    side = min(rect.width(), rect.height())
    if side <= 0:
        return
    square = QRectF(rect.center().x() - side / 2.0, rect.center().y() - side / 2.0, side, side)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, max(side * STROKE_SHARE, 1.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    _DRAW[symbol](painter, square, color, phase)
    painter.restore()


def _at(square: QRectF, x: float, y: float) -> QPointF:
    """A point given in unit coordinates of ``square``."""
    return QPointF(square.x() + x * square.width(), square.y() + y * square.height())


def _unit_rect(square: QRectF, x: float, y: float, w: float, h: float) -> QRectF:
    return QRectF(_at(square, x, y), _at(square, x + w, y + h))


def _ring(painter: QPainter, square: QRectF) -> None:
    painter.drawEllipse(_unit_rect(square, 0.08, 0.08, 0.84, 0.84))


def _dot(painter: QPainter, centre: QPointF, radius: float, color: QColor) -> None:
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(centre, radius, radius)
    painter.restore()


def _listening(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    # A microphone: capsule, cradle, stem and base.
    capsule = _unit_rect(square, 0.34, 0.06, 0.32, 0.5)
    radius = capsule.width() / 2.0
    painter.drawRoundedRect(capsule, radius, radius)
    painter.drawArc(_unit_rect(square, 0.2, 0.2, 0.6, 0.52), 180 * 16, 180 * 16)
    painter.drawLine(_at(square, 0.5, 0.72), _at(square, 0.5, 0.88))
    painter.drawLine(_at(square, 0.34, 0.92), _at(square, 0.66, 0.92))


def _thinking(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    # Three dots; one at a time brightens, slowly.
    radius = square.width() * 0.09
    for index, x in enumerate((0.2, 0.5, 0.8)):
        wave = 0.5 + 0.5 * math.cos(2.0 * math.pi * (phase / 1.2 - index / 3.0))
        dot = QColor(color)
        dot.setAlphaF(color.alphaF() * (0.35 + 0.65 * wave))
        _dot(painter, _at(square, x, 0.5), radius, dot)


def _speaking(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    # A speaker with two sound arcs.
    body = QPainterPath(_at(square, 0.1, 0.38))
    for x, y in ((0.26, 0.38), (0.46, 0.2), (0.46, 0.8), (0.26, 0.62), (0.1, 0.62)):
        body.lineTo(_at(square, x, y))
    body.closeSubpath()
    painter.drawPath(body)
    painter.drawArc(_unit_rect(square, 0.38, 0.3, 0.3, 0.4), -50 * 16, 100 * 16)
    painter.drawArc(_unit_rect(square, 0.34, 0.14, 0.58, 0.72), -50 * 16, 100 * 16)


def _success(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    _ring(painter, square)
    check = QPainterPath(_at(square, 0.3, 0.52))
    check.lineTo(_at(square, 0.44, 0.66))
    check.lineTo(_at(square, 0.7, 0.38))
    painter.drawPath(check)


def _warning(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    triangle = QPainterPath(_at(square, 0.5, 0.08))
    triangle.lineTo(_at(square, 0.94, 0.88))
    triangle.lineTo(_at(square, 0.06, 0.88))
    triangle.closeSubpath()
    painter.drawPath(triangle)
    painter.drawLine(_at(square, 0.5, 0.38), _at(square, 0.5, 0.6))
    _dot(painter, _at(square, 0.5, 0.74), square.width() * STROKE_SHARE * 0.6, color)


def _error(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    _ring(painter, square)
    painter.drawLine(_at(square, 0.34, 0.34), _at(square, 0.66, 0.66))
    painter.drawLine(_at(square, 0.66, 0.34), _at(square, 0.34, 0.66))


def _task(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    # A progress ring, three quarters drawn, with a dot where it ends.
    painter.drawArc(_unit_rect(square, 0.08, 0.08, 0.84, 0.84), 90 * 16, -270 * 16)
    _dot(painter, _at(square, 0.5, 0.5), square.width() * 0.08, color)


def _device(painter: QPainter, square: QRectF, color: QColor, phase: float) -> None:
    # A generic device: a rounded slab with a home dot.
    slab = _unit_rect(square, 0.26, 0.06, 0.48, 0.88)
    painter.drawRoundedRect(slab, slab.width() * 0.2, slab.width() * 0.2)
    _dot(painter, _at(square, 0.5, 0.78), square.width() * 0.05, color)


_DRAW: dict[NotchSymbol, Callable[[QPainter, QRectF, QColor, float], None]] = {
    NotchSymbol.LISTENING: _listening,
    NotchSymbol.THINKING: _thinking,
    NotchSymbol.SPEAKING: _speaking,
    NotchSymbol.SUCCESS: _success,
    NotchSymbol.WARNING: _warning,
    NotchSymbol.ERROR: _error,
    NotchSymbol.TASK: _task,
    NotchSymbol.DEVICE: _device,
}
