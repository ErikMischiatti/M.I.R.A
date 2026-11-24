"""Hover drives the notch's presence through the presenter, never directly.

Real Qt enter and leave events are delivered to a real `NotchWindow` bound to
a `NotchPresenter` on a `ManualScheduler`, so notice lifetimes are exact and
transitions are seeked, never waited for. What only a window manager decides —
whether the pointer really crosses the input mask — is left to the manual
smoke test.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QLineEdit

from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize
from mira.ui.notch.presentation import NotchBehaviour, NotchPresenter
from mira.ui.notch.startup import bind_presenter


GEOMETRY = NotchGeometry(
    compact=NotchSize(width=200.0, height=32.0, corner_radius=14.0),
    expanded=NotchSize(width=360.0, height=96.0, corner_radius=24.0),
)
NOTICE_MS = 3000
WARNING = Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low disk")

H, C, E = NotchPresence.HIDDEN, NotchPresence.COMPACT, NotchPresence.EXPANDED


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def scheduler():
    return ManualScheduler()


@pytest.fixture
def notch(scheduler):
    window = NotchWindow(GEOMETRY)
    presenter = NotchPresenter(scheduler, NotchBehaviour(notice_duration_ms=NOTICE_MS))
    bind_presenter(window, presenter)
    presenter.show()
    finish(window)
    window.presenter = presenter  # for the tests' convenience only
    yield window
    presenter.shutdown()
    window.transition.stop()
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def enter(window: NotchWindow) -> None:
    point = QPointF(window.width() / 2.0, 4.0)
    QCoreApplication.sendEvent(window, QEnterEvent(point, point, window.mapToGlobal(point)))


def leave(window: NotchWindow) -> None:
    QCoreApplication.sendEvent(window, QEvent(QEvent.Type.Leave))


def seek(window: NotchWindow, ms: int) -> None:
    transition = window.transition
    if transition.state() is QAbstractAnimation.State.Running:
        transition.pause()
    transition.setCurrentTime(ms)


def finish(window: NotchWindow) -> None:
    if window.transition.state() is not QAbstractAnimation.State.Stopped:
        seek(window, window.transition.duration())


# --- Hover ---------------------------------------------------------------------


def test_entering_the_compact_notch_expands_it_and_leaving_collapses_it(notch):
    assert notch.presence is C

    enter(notch)
    assert notch.hovered
    assert notch.presence is E
    finish(notch)
    assert notch.visual_size == GEOMETRY.expanded

    leave(notch)
    assert not notch.hovered
    assert notch.presence is C
    finish(notch)
    assert notch.visual_size == GEOMETRY.compact


def test_staying_inside_stays_expanded(notch):
    enter(notch)
    finish(notch)
    enter(notch)  # repeated enters are not new hovers

    assert notch.presence is E
    assert notch.transition.state() is QAbstractAnimation.State.Stopped


def test_the_window_does_not_expand_itself_on_hover(scheduler):
    window = NotchWindow(GEOMETRY)
    try:
        window.set_presence(C, animated=False)
        enter(window)
        assert window.hovered
        assert window.presence is C
    finally:
        window.hide()
        window.deleteLater()


def test_hover_does_not_expand_when_hover_expansion_is_off(notch):
    notch.presenter.set_behaviour(NotchBehaviour(expand_on_hover=False))

    enter(notch)

    assert notch.presence is C


def test_hiding_releases_the_hover(notch):
    enter(notch)
    notch.presenter.hide()
    finish(notch)

    assert not notch.hovered
    notch.presenter.show()
    assert notch.presence is C


# --- Hover and notices -----------------------------------------------------------


def test_a_notice_keeps_the_notch_open_after_the_pointer_leaves(notch, scheduler):
    enter(notch)
    notch.presenter.present(WARNING)
    leave(notch)
    assert notch.presence is E

    scheduler.advance(NOTICE_MS)

    assert notch.presence is C


def test_hover_keeps_the_notch_open_after_the_notice_expires(notch, scheduler):
    notch.presenter.present(WARNING)
    assert notch.presence is E
    enter(notch)

    scheduler.advance(NOTICE_MS)
    assert notch.presence is E
    assert notch.presentation.notice is None

    leave(notch)
    assert notch.presence is C


# --- Rapid hover -------------------------------------------------------------------


def test_rapid_enter_leave_enter_reverses_from_the_drawn_shape(notch):
    enter(notch)
    seek(notch, 80)
    midway = notch.visual_size
    assert GEOMETRY.compact.height < midway.height < GEOMETRY.expanded.height

    leave(notch)
    assert notch.presence is C
    # Reversal starts where the notch is drawn, never from a settled size.
    assert notch.visual_size == midway
    seek(notch, 30)
    reversing = notch.visual_size
    assert GEOMETRY.compact.height < reversing.height < midway.height

    enter(notch)
    assert notch.presence is E
    assert notch.visual_size == reversing
    # Only the share of the path still left is animated.
    assert notch.transition.duration() < notch.transition_duration_ms
    finish(notch)
    assert notch.visual_size == GEOMETRY.expanded


def test_many_rapid_hovers_settle_on_the_last_one(notch):
    for _ in range(10):
        enter(notch)
        seek(notch, 10)
        leave(notch)
        seek(notch, 5)
    finish(notch)

    assert notch.presence is C
    assert notch.visual_size == GEOMETRY.compact


# --- Focus ------------------------------------------------------------------------


def test_hover_does_not_take_keyboard_focus(notch):
    editor = QLineEdit()
    editor.show()
    editor.activateWindow()
    editor.setFocus()
    QCoreApplication.processEvents()
    focused = QApplication.focusWidget()
    try:
        enter(notch)
        QCoreApplication.processEvents()

        assert QApplication.focusWidget() is focused
        assert notch.focusPolicy() == Qt.NoFocus
        assert notch.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert notch.testAttribute(Qt.WA_ShowWithoutActivating)
    finally:
        editor.close()
        editor.deleteLater()


def test_the_notch_stays_an_always_on_top_tool_window(notch):
    enter(notch)
    flags = notch.windowFlags()

    assert notch.isWindow()
    assert flags & Qt.Tool == Qt.Tool
    assert flags & Qt.WindowStaysOnTopHint
    assert flags & Qt.FramelessWindowHint
