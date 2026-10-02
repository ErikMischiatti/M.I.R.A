"""Monitor preference and the 1 / 2 / 3+ automatic screen policy.

The policy itself is Qt-free and tested on plain values. The window tests run
the real default selector over fake screens (offscreen Qt has one screen) and
emit the application's screen signals by hand to simulate hot-plugging.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

from mira.ui.notch.presence import Rect
from mira.ui.notch.screen_selection import (
    MonitorIdentity,
    MonitorPreference,
    ScreenInfo,
    SelectionMode,
    automatic_screen,
    select_screen,
)


LAPTOP = MonitorIdentity("eDP-1", "LG Display")
PHILIPS = MonitorIdentity("HDMI-1", "Philips Consumer Electronics Company", "Philips 298P4", "1844")
DELL = MonitorIdentity("DP-1", "Dell Inc.", "DELL P2425H", "712ZK34")

# The reported desktop: laptop, landscape HDMI in the middle, portrait DP.
LAPTOP_RECT = Rect(0, 313, 1920, 1200)
PHILIPS_RECT = Rect(1920, 313, 2560, 1080)
DELL_RECT = Rect(4480, 0, 1080, 1920)


def info(identity: MonitorIdentity, geometry: Rect, primary: bool = False) -> ScreenInfo:
    return ScreenInfo(identity=identity, geometry=geometry, primary=primary)


THREE = [info(LAPTOP, LAPTOP_RECT, primary=True), info(PHILIPS, PHILIPS_RECT), info(DELL, DELL_RECT)]


def chosen(screens, preference=MonitorPreference()) -> MonitorIdentity | None:
    selection = select_screen(screens, preference)
    return None if selection.index is None else screens[selection.index].identity


# --- Identity ------------------------------------------------------------------


def test_a_monitor_with_a_serial_is_recognised_on_another_connector():
    assert PHILIPS.matches(MonitorIdentity("DP-2", PHILIPS.manufacturer, PHILIPS.model, PHILIPS.serial))


def test_two_monitors_of_one_model_are_told_apart_by_serial():
    twin = MonitorIdentity("HDMI-1", PHILIPS.manufacturer, PHILIPS.model, "9999")
    assert not PHILIPS.matches(twin)


def test_without_a_serial_the_connector_and_edid_text_must_agree():
    assert LAPTOP.matches(MonitorIdentity("eDP-1", "LG Display"))
    assert not LAPTOP.matches(MonitorIdentity("eDP-2", "LG Display"))
    assert not LAPTOP.matches(MonitorIdentity("eDP-1", "BOE"))


def test_the_label_names_connector_and_model():
    assert PHILIPS.label == "HDMI-1 (Philips 298P4)"
    assert LAPTOP.label == "eDP-1 (LG Display)"
    assert MonitorIdentity("VIRTUAL1").label == "VIRTUAL1"


# --- Automatic policy ------------------------------------------------------------


def test_no_screens_select_nothing():
    selection = select_screen([])
    assert selection.index is None and selection.mode is SelectionMode.AUTOMATIC


def test_one_screen_is_used_whatever_it_is():
    assert chosen([info(DELL, DELL_RECT)]) == DELL


def test_two_screens_prefer_the_primary_over_the_central_one():
    # Geometrically the larger HDMI screen would be nearer the centre.
    screens = [info(LAPTOP, LAPTOP_RECT, primary=True), info(PHILIPS, PHILIPS_RECT)]
    assert chosen(screens) == LAPTOP


def test_two_screens_follow_the_primary_when_an_external_one_is_primary():
    screens = [info(LAPTOP, LAPTOP_RECT), info(PHILIPS, PHILIPS_RECT, primary=True)]
    assert chosen(screens) == PHILIPS


def test_two_screens_without_a_primary_prefer_the_built_in_panel():
    screens = [info(PHILIPS, Rect(0, 0, 2560, 1080)), info(LAPTOP, Rect(2560, 0, 1920, 1200))]
    assert chosen(screens) == LAPTOP


def test_two_external_screens_without_a_primary_use_geometric_order():
    screens = [info(DELL, Rect(2560, 0, 1080, 1920)), info(PHILIPS, Rect(0, 0, 2560, 1080))]
    assert chosen(screens) == PHILIPS


def test_three_screens_select_the_central_one_not_the_primary():
    assert chosen(THREE) == PHILIPS


def test_more_than_three_screens_select_the_central_one():
    left = MonitorIdentity("DP-3")
    screens = THREE + [info(left, Rect(-1920, 313, 1920, 1080))]
    # Desktop spans -1920..5560: centre x 1820, nearest the laptop's 960.
    assert chosen(screens) == LAPTOP


def test_a_three_screen_tie_prefers_the_primary():
    a, b, c = MonitorIdentity("A"), MonitorIdentity("B"), MonitorIdentity("C")
    # A 2x2 grid minus one corner has two screens equally near the centre.
    screens = [
        info(a, Rect(0, 0, 1000, 1000)),
        info(b, Rect(1000, 0, 1000, 1000), primary=True),
        info(c, Rect(0, 1000, 1000, 1000)),
    ]
    assert automatic_screen(screens) is not None
    assert chosen(screens) == b


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_negative_and_non_zero_origins_are_handled(count):
    rects = [Rect(-3000, -500, 1000, 800), Rect(-2000, -500, 1500, 900), Rect(-500, -420, 800, 600), Rect(300, 0, 900, 700)]
    names = [MonitorIdentity(f"S{i}") for i in range(4)]
    screens = [info(names[i], rects[i], primary=i == 0) for i in range(count)]
    selection = select_screen(screens)
    assert selection.index is not None


@pytest.mark.parametrize("layout", [THREE[:1], THREE[:2], THREE, THREE + [info(MonitorIdentity("X"), Rect(5560, 0, 800, 600))]])
def test_the_choice_never_depends_on_the_listing_order(layout):
    results = {chosen(list(order)) for order in itertools.permutations(layout)}
    assert len(results) == 1


# --- Preference -------------------------------------------------------------------


def test_a_connected_preferred_monitor_is_used():
    selection = select_screen(THREE, MonitorPreference(DELL))
    assert THREE[selection.index].identity == DELL
    assert selection.mode is SelectionMode.PREFERRED


def test_a_preferred_monitor_is_found_in_any_listing_order():
    for order in itertools.permutations(THREE):
        assert chosen(list(order), MonitorPreference(LAPTOP)) == LAPTOP


def test_an_unavailable_preference_falls_back_to_automatic():
    screens = THREE[:1]
    selection = select_screen(screens, MonitorPreference(DELL))
    assert screens[selection.index].identity == LAPTOP
    assert selection.mode is SelectionMode.FALLBACK


def test_an_automatic_preference_reports_automatic():
    assert select_screen(THREE).mode is SelectionMode.AUTOMATIC


def test_the_policy_imports_no_qt():
    source = pathlib.Path(__file__).resolve().parents[1] / "mira/ui/notch/screen_selection.py"
    tree = ast.parse(source.read_text())
    modules = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(module.split(".")[0] in {"PySide6", "PyQt5", "PyQt6", "shiboken6"} for module in modules)


# --- Through the window: hot-plugging --------------------------------------------


qt = pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import mira.ui.notch.notch_window as notch_window_module  # noqa: E402
from mira.ui.notch.notch_window import NotchWindow  # noqa: E402
from mira.ui.notch.screens import monitor_identity  # noqa: E402
from mira.ui.notch.presence import NotchGeometry

# The window is as wide as the expanded notch, so placement follows its width.
EXPANDED_WIDTH = int(NotchGeometry().expanded.width)


class FakeScreen(QObject):
    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, identity: MonitorIdentity, geometry: Rect):
        super().__init__()
        self._identity = identity
        self._geometry = QRect(int(geometry.x), int(geometry.y), int(geometry.width), int(geometry.height))

    def name(self):
        return self._identity.name

    def manufacturer(self):
        return self._identity.manufacturer

    def model(self):
        return self._identity.model

    def serialNumber(self):
        return self._identity.serial

    def geometry(self):
        return self._geometry

    def availableGeometry(self):
        return self._geometry.adjusted(0, 32, 0, 0)


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


class Desktop:
    def __init__(self, monkeypatch):
        self.screens: list[FakeScreen] = []
        self.primary: FakeScreen | None = None
        monkeypatch.setattr(notch_window_module, "application_screens", lambda: (list(self.screens), self.primary))

    def plug(self, screen: FakeScreen, *, primary: bool = False):
        self.screens.append(screen)
        if primary:
            self.primary = screen
        QGuiApplication.instance().screenAdded.emit(screen)

    def unplug(self, screen: FakeScreen):
        QGuiApplication.instance().screenRemoved.emit(screen)
        self.screens.remove(screen)
        if self.primary is screen:
            self.primary = None
        QCoreApplication.processEvents()


@pytest.fixture
def desktop(monkeypatch):
    return Desktop(monkeypatch)


@pytest.fixture
def made():
    windows = []
    yield windows
    for window in windows:
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def reported_three(desktop: Desktop):
    laptop = FakeScreen(LAPTOP, LAPTOP_RECT)
    philips = FakeScreen(PHILIPS, PHILIPS_RECT)
    dell = FakeScreen(DELL, DELL_RECT)
    desktop.plug(laptop, primary=True)
    desktop.plug(philips)
    desktop.plug(dell)
    return laptop, philips, dell


def test_the_qt_adapter_reads_the_edid_identity():
    assert monitor_identity(FakeScreen(PHILIPS, PHILIPS_RECT)) == PHILIPS


def test_the_reported_three_monitor_setup_selects_the_central_hdmi(desktop, made):
    laptop, philips, dell = reported_three(desktop)
    notch = NotchWindow()
    made.append(notch)

    assert notch.tracked_screen is philips
    assert (notch.x(), notch.y()) == (1920 + (2560 - EXPANDED_WIDTH) // 2, 313)


def test_unplugging_externals_moves_to_the_laptop_and_replugging_returns_flush(desktop, made):
    laptop, philips, dell = reported_three(desktop)
    notch = NotchWindow()
    made.append(notch)

    desktop.unplug(dell)
    assert notch.tracked_screen is laptop  # two screens: the primary
    desktop.unplug(philips)
    assert notch.tracked_screen is laptop
    assert notch.y() == 313

    desktop.plug(philips)
    assert notch.tracked_screen is laptop
    desktop.plug(dell)
    assert notch.tracked_screen is philips
    # No work-area gap after the topology change.
    assert notch.y() == 313


def test_a_preferred_monitor_is_left_when_unplugged_and_returned_to(desktop, made):
    laptop, philips, dell = reported_three(desktop)
    notch = NotchWindow(monitor_preference=MonitorPreference(DELL))
    made.append(notch)
    assert notch.tracked_screen is dell

    desktop.unplug(dell)
    assert notch.tracked_screen is laptop  # automatic over the two left
    assert notch.selection.mode is SelectionMode.FALLBACK
    assert notch.monitor_preference == MonitorPreference(DELL)  # kept

    returning = FakeScreen(DELL, DELL_RECT)  # Qt creates a new QScreen object
    desktop.plug(returning)
    assert notch.tracked_screen is returning
    assert notch.selection.mode is SelectionMode.PREFERRED
    assert (notch.x(), notch.y()) == (4480 + (1080 - EXPANDED_WIDTH) // 2, 0)


def test_changing_the_preference_reselects_at_once(desktop, made):
    laptop, philips, dell = reported_three(desktop)
    notch = NotchWindow()
    made.append(notch)
    selections = []
    notch.screen_selection_changed.connect(lambda: selections.append(notch.tracked_screen))

    notch.set_monitor_preference(MonitorPreference(LAPTOP))
    assert notch.tracked_screen is laptop
    notch.set_monitor_preference(MonitorPreference())
    assert notch.tracked_screen is philips
    assert selections == [laptop, philips]


def test_every_topology_change_is_announced(desktop, made):
    laptop, philips, dell = reported_three(desktop)
    notch = NotchWindow()
    made.append(notch)
    announcements = []
    notch.screen_selection_changed.connect(lambda: announcements.append(len(desktop.screens)))

    desktop.unplug(dell)
    desktop.plug(dell)

    assert announcements[-1] == 3
    assert 2 in announcements
