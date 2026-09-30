"""Choosing the geometrically central screen, without Qt and through its adapter."""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

from mira.ui.notch.presence import Rect
from mira.ui.notch.screen_selection import (
    central_screens,
    desktop_bounds,
    distance_squared_to_desktop_centre,
    select_central_screen,
)


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "mira/ui/notch/screen_selection.py"


def chosen(screens: list[Rect]) -> Rect:
    return screens[select_central_screen(screens)]


def every_order(screens: list[Rect]):
    return [list(order) for order in itertools.permutations(screens)]


# --- Geometry -----------------------------------------------------------------


def test_the_desktop_bounds_contain_every_screen():
    screens = [Rect(-1280, -200, 1280, 1024), Rect(0, 0, 1920, 1080), Rect(1920, 300, 1280, 1024)]

    assert desktop_bounds(screens) == Rect(-1280, -200, 4480, 1524)


def test_distance_is_squared_between_centres():
    desktop = Rect(0, 0, 1000, 1000)

    assert distance_squared_to_desktop_centre(Rect(0, 0, 200, 200), desktop) == 400.0**2 * 2


def test_no_screens_is_an_error():
    with pytest.raises(ValueError):
        select_central_screen([])


# --- Layouts -------------------------------------------------------------------


def test_a_single_screen_is_selected():
    assert select_central_screen([Rect(0, 0, 1920, 1080)]) == 0


def test_the_middle_of_three_screens_in_a_row():
    left, middle, right = Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080), Rect(3840, 0, 1920, 1080)

    assert chosen([left, middle, right]) == middle


def test_the_middle_of_a_row_of_mixed_resolutions_negative_origins_and_offsets():
    left = Rect(-2560, -200, 2560, 1440)
    middle = Rect(0, 0, 1920, 1080)
    right = Rect(1920, 300, 1280, 1024)

    assert chosen([left, middle, right]) == middle


def test_the_middle_of_three_stacked_screens():
    top, middle, bottom = Rect(0, 0, 1920, 1080), Rect(0, 1080, 2560, 1440), Rect(0, 2520, 1920, 1080)

    assert chosen([top, middle, bottom]) == middle


def test_an_irregular_layout_is_decided_by_geometry():
    #             ┌───────┐
    #             │   C   │
    # ┌───────┐   └───────┘
    # │   A   │┌───────┐
    # └───────┘│   B   │
    #          └───────┘
    a, b, c = Rect(0, 100, 1000, 600), Rect(1000, 300, 1000, 600), Rect(1300, 0, 1000, 500)

    assert chosen([a, b, c]) == b


def test_a_laptop_a_landscape_and_a_portrait_monitor_select_the_landscape_between_them():
    laptop = Rect(0, 273, 1920, 1200)
    landscape = Rect(1920, 273, 2560, 1080)
    portrait = Rect(4480, 0, 1080, 1920)

    assert chosen([laptop, landscape, portrait]) == landscape


def test_of_two_unequal_screens_the_one_nearer_the_centre_wins():
    small, large = Rect(0, 0, 1920, 1080), Rect(1920, 0, 2560, 1440)

    assert chosen([small, large]) == large


@pytest.mark.parametrize(
    "screens",
    [
        [Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080), Rect(3840, 0, 1920, 1080)],
        [Rect(-2560, -200, 2560, 1440), Rect(0, 0, 1920, 1080), Rect(1920, 300, 1280, 1024)],
        [Rect(0, 100, 1000, 600), Rect(1000, 300, 1000, 600), Rect(1300, 0, 1000, 500)],
        [Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080)],
        [Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080)],
        [Rect(0, 0, 1920, 1080), Rect(960, 0, 1920, 1080)],
    ],
    ids=["row", "mixed", "irregular", "equal-pair", "mirrored", "overlapping"],
)
def test_the_listing_order_never_changes_the_selected_geometry(screens):
    selections = {chosen(order) for order in every_order(screens)}

    assert len(selections) == 1


# --- Ties ----------------------------------------------------------------------


def test_two_equal_screens_side_by_side_tie():
    left, right = Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080)

    assert sorted(central_screens([right, left])) == [0, 1]


def test_a_tie_falls_back_to_the_leftmost_then_topmost_screen():
    left, right = Rect(0, 0, 1920, 1080), Rect(1920, 0, 1920, 1080)
    top, bottom = Rect(0, 0, 1920, 1080), Rect(0, 1080, 1920, 1080)

    assert chosen([right, left]) == left
    assert chosen([bottom, top]) == top


def test_mirrored_screens_select_one_of_the_identical_geometries():
    mirror = Rect(0, 0, 1920, 1080)
    screens = [Rect(1920, 0, 1920, 1080), mirror, Rect(0, 0, 1920, 1080)]

    assert chosen(screens) == mirror
    assert central_screens(screens)[:2] == [1, 2]


def test_overlapping_screens_are_decided_by_geometry():
    first, shifted = Rect(0, 0, 1920, 1080), Rect(960, 0, 1920, 1080)

    assert chosen([shifted, first]) == first


# --- Boundary ------------------------------------------------------------------


def test_the_selection_policy_imports_no_qt():
    tree = ast.parse(SOURCE.read_text())
    imported = {
        (node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert "PySide6" not in imported and "shiboken6" not in imported


# --- Qt adapter ----------------------------------------------------------------

QtCore = pytest.importorskip("PySide6.QtCore")

from mira.ui.notch.screens import central_screen_of  # noqa: E402


class StubScreen:
    """Only what the adapter reads from a QScreen."""

    def __init__(self, name: str, x: int, y: int, width: int, height: int):
        self._name = name
        self._geometry = QtCore.QRect(x, y, width, height)

    def geometry(self):
        return self._geometry

    def name(self) -> str:
        return self._name

    def __repr__(self):
        return f"StubScreen({self._name})"


def test_the_adapter_selects_the_central_screen_regardless_of_primary():
    laptop = StubScreen("a", 0, 273, 1920, 1200)
    landscape = StubScreen("b", 1920, 273, 2560, 1080)
    portrait = StubScreen("c", 4480, 0, 1080, 1920)

    for order in itertools.permutations([laptop, landscape, portrait]):
        assert central_screen_of(list(order), primary=laptop) is landscape


def test_a_tie_prefers_the_primary_screen():
    left, right = StubScreen("a", 0, 0, 1920, 1080), StubScreen("b", 1920, 0, 1920, 1080)

    assert central_screen_of([left, right], primary=right) is right
    assert central_screen_of([right, left], primary=left) is left


def test_a_tie_without_the_primary_falls_back_to_geometric_order():
    left, right = StubScreen("z", 0, 0, 1920, 1080), StubScreen("a", 1920, 0, 1920, 1080)
    elsewhere = StubScreen("p", 0, 5000, 10, 10)

    assert central_screen_of([right, left], primary=None) is left
    assert central_screen_of([right, left], primary=elsewhere) is left


def test_identical_geometries_are_separated_by_name_in_any_order():
    first, second = StubScreen("HDMI-2", 0, 0, 1920, 1080), StubScreen("HDMI-1", 0, 0, 1920, 1080)

    for order in ([first, second], [second, first]):
        assert central_screen_of(order, primary=None) is second


def test_no_screens_select_nothing():
    assert central_screen_of([], primary=None) is None
