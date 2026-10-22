"""The expanded notch's label and its detail: a name, then the reading.

Offscreen, through the real window's own layout (`NotchWindow.content_layout`,
what `paintEvent` draws), with the default geometry and font.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent

from mira.application.composition import build_application
from mira.domain.scheduler import ManualScheduler
from mira.ui.desktop_shell import DesktopShell, Surface
from mira.ui.notch.expanded_layout import DETAIL_GAP_EMS, MIN_LABEL_EMS, label_room
from mira.ui.notch.presentation import NotchContent, NotchSymbol


@pytest.fixture(scope="module", autouse=True)
def application():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def finish(notch) -> None:
    transition = notch.transition
    if transition.state() is QAbstractAnimation.State.Running:
        transition.pause()
    transition.setCurrentTime(transition.duration())


@pytest.fixture
def shell():
    application = build_application(scheduler=ManualScheduler())
    desktop = DesktopShell(application, initial_surface=Surface.NOTCH)
    desktop.start()
    finish(desktop.notch)
    yield desktop
    desktop.shutdown()
    application.shutdown()
    for obj in (desktop.notch, desktop.window, desktop.animator):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_label_and_detail_share_the_room_detail_first():
    text = 20.0
    gap = DETAIL_GAP_EMS * text

    fits = label_room(300, 100, 80, text)
    assert (fits.label, fits.detail, fits.gap) == (100, 80, gap)

    tight = label_room(200, 150, 100, text)
    assert tight.detail == 100 and tight.label == pytest.approx(200 - gap - 100)

    tiny = label_room(100, 150, 100, text)  # the name keeps its floor; the detail gives way
    assert tiny.label == pytest.approx(MIN_LABEL_EMS * text)
    assert tiny.label + tiny.gap + tiny.detail == pytest.approx(100)

    alone = label_room(50, 80, 0, text)
    assert (alone.label, alone.detail, alone.gap) == (50, 0, 0)
    detail_only = label_room(200, 0, 90, text)
    assert (detail_only.label, detail_only.detail, detail_only.gap) == (0, 90, 0)


@pytest.mark.parametrize(("label", "detail"), [
    ("Pixel 8", "84% · Charging"),
    ("iPhone", "100% · Not charging"),
    ("Samsung Galaxy S24 Ultra Enterprise Edition", "57% · Discharging"),
    ("Generic Mass Storage Device With An Extremely Long Product Name", "1.5 TB"),
    ("Google Pixel 8 connected", None),
    ("W" * 120, "100% · Not charging"),
])
def test_long_names_give_way_before_the_reading_and_nothing_overlaps(shell, label, detail):
    content = NotchContent(NotchSymbol.PHONE, label, detail)
    row = shell.notch.content_row

    layout = shell.notch.content_layout(content)

    if detail:
        assert layout.detail == detail  # the reading stays whole
        assert layout.detail_rect.left() >= layout.label_rect.right()
    else:
        assert layout.detail == ""
    assert layout.label  # a name is always there
    assert layout.label == label or layout.label.endswith("…")
    assert layout.label_rect.left() >= layout.symbol.right()
    right = max(layout.label_rect.right(), layout.detail_rect.right() if detail else 0.0)
    assert layout.symbol.left() >= row.area.x - 0.5 and right <= row.area.x + row.area.width + 0.5


def test_unknown_information_is_not_drawn(shell):
    layout = shell.notch.content_layout(NotchContent(NotchSymbol.USB, "USB device connected"))

    assert layout.detail == "" and layout.detail_rect.width() == 0
