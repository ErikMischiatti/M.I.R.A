"""The notch's presence and geometry, described without Qt or a window."""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

from mira.ui.notch.presence import (
    NotchGeometry,
    NotchPresence,
    NotchShape,
    NotchSize,
    Rect,
)


PRESENCE_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "mira/ui/notch/presence.py"

# A 1920x1080 monitor whose top 32 px are a desktop panel.
AVAILABLE = Rect(x=0.0, y=32.0, width=1920.0, height=1048.0)
GEOMETRY = NotchGeometry(
    compact=NotchSize(width=200.0, height=32.0, corner_radius=14.0),
    expanded=NotchSize(width=360.0, height=96.0, corner_radius=24.0),
)


# --- Presence ----------------------------------------------------------------


def test_presence_has_exactly_the_three_visual_states():
    assert [presence.value for presence in NotchPresence] == ["hidden", "compact", "expanded"]


def test_only_hidden_is_invisible():
    assert not NotchPresence.HIDDEN.is_visible
    assert NotchPresence.COMPACT.is_visible
    assert NotchPresence.EXPANDED.is_visible


# --- Sizes per presence ------------------------------------------------------


def test_compact_and_expanded_use_their_configured_sizes():
    assert GEOMETRY.size_for(NotchPresence.COMPACT) == GEOMETRY.compact
    assert GEOMETRY.size_for(NotchPresence.EXPANDED) == GEOMETRY.expanded


def test_hidden_is_the_compact_notch_retracted_into_the_top_edge():
    assert GEOMETRY.size_for(NotchPresence.HIDDEN) == NotchSize(width=200.0, height=0.0)


def test_the_defaults_form_a_valid_geometry():
    geometry = NotchGeometry()
    assert geometry.expanded.width >= geometry.compact.width
    assert geometry.expanded.height >= geometry.compact.height
    assert geometry.top_margin == 0.0


# --- Placement ---------------------------------------------------------------


def test_compact_bounds_hang_centred_from_the_top_of_the_available_area():
    shape = GEOMETRY.shape_for(AVAILABLE, NotchPresence.COMPACT)

    assert shape == NotchShape(bounds=Rect(860.0, 32.0, 200.0, 32.0), corner_radius=14.0)


def test_expanded_bounds_hang_centred_from_the_top_of_the_available_area():
    shape = GEOMETRY.shape_for(AVAILABLE, NotchPresence.EXPANDED)

    assert shape == NotchShape(bounds=Rect(780.0, 32.0, 360.0, 96.0), corner_radius=24.0)


@pytest.mark.parametrize("presence", list(NotchPresence))
def test_every_presence_shares_the_top_centre_anchor(presence):
    bounds = GEOMETRY.shape_for(AVAILABLE, presence).bounds

    assert bounds.center_x == AVAILABLE.center_x
    assert bounds.y == AVAILABLE.y


@pytest.mark.parametrize("presence", list(NotchPresence))
def test_the_expanded_bounds_contain_every_presence(presence):
    envelope = GEOMETRY.shape_for(AVAILABLE, NotchPresence.EXPANDED).bounds
    bounds = GEOMETRY.shape_for(AVAILABLE, presence).bounds

    assert envelope.x <= bounds.x and bounds.right <= envelope.right
    assert envelope.y <= bounds.y and bounds.bottom <= envelope.bottom


def test_placement_follows_an_offset_screen_in_a_multi_monitor_layout():
    # A second monitor to the left of the primary one has negative x.
    left_monitor = Rect(x=-1280.0, y=0.0, width=1280.0, height=1024.0)

    bounds = GEOMETRY.shape_for(left_monitor, NotchPresence.COMPACT).bounds

    assert bounds == Rect(-740.0, 0.0, 200.0, 32.0)


def test_the_top_margin_lowers_the_notch_below_the_area_top():
    geometry = NotchGeometry(compact=GEOMETRY.compact, expanded=GEOMETRY.expanded, top_margin=6.0)

    assert geometry.shape_for(AVAILABLE, NotchPresence.COMPACT).bounds.y == 38.0


def test_centring_an_odd_leftover_keeps_fractional_bounds_for_the_window_to_round():
    area = Rect(x=0.0, y=0.0, width=1001.0, height=800.0)

    assert GEOMETRY.shape_for(area, NotchPresence.COMPACT).bounds.x == 400.5


# --- Edge cases --------------------------------------------------------------


def test_a_notch_wider_than_the_area_shrinks_to_it_with_its_radius():
    narrow = Rect(x=100.0, y=0.0, width=150.0, height=600.0)
    geometry = NotchGeometry(
        compact=NotchSize(width=200.0, height=200.0, corner_radius=100.0),
        expanded=NotchSize(width=200.0, height=200.0, corner_radius=100.0),
    )

    shape = geometry.shape_for(narrow, NotchPresence.COMPACT)

    assert shape.bounds == Rect(100.0, 0.0, 150.0, 200.0)
    assert shape.corner_radius == 75.0


def test_a_notch_taller_than_the_area_shrinks_to_it():
    short = Rect(x=0.0, y=0.0, width=1920.0, height=50.0)

    shape = GEOMETRY.shape_for(short, NotchPresence.EXPANDED)

    assert shape.bounds.height == 50.0
    assert shape.corner_radius == 24.0


def test_an_empty_area_yields_empty_bounds_at_its_top_centre():
    empty = Rect(x=10.0, y=20.0, width=0.0, height=0.0)

    shape = GEOMETRY.shape_for(empty, NotchPresence.EXPANDED)

    assert shape == NotchShape(bounds=Rect(10.0, 20.0, 0.0, 0.0), corner_radius=0.0)


def test_a_top_margin_beyond_the_area_leaves_a_zero_height_notch_inside_it():
    geometry = NotchGeometry(top_margin=500.0)
    area = Rect(x=0.0, y=0.0, width=800.0, height=100.0)

    bounds = geometry.shape_for(area, NotchPresence.COMPACT).bounds

    assert bounds.y == 100.0 and bounds.height == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"width": -1.0, "height": 10.0},
        {"width": 10.0, "height": -1.0},
        {"width": 40.0, "height": 20.0, "corner_radius": 11.0},
        {"width": 40.0, "height": 20.0, "corner_radius": -1.0},
    ],
)
def test_impossible_sizes_are_rejected(kwargs):
    with pytest.raises(ValueError):
        NotchSize(**kwargs)


def test_a_negative_rect_is_rejected():
    with pytest.raises(ValueError):
        Rect(0.0, 0.0, -1.0, 10.0)


@pytest.mark.parametrize(
    "compact,expanded",
    [
        (NotchSize(300.0, 32.0), NotchSize(200.0, 96.0)),
        (NotchSize(200.0, 100.0), NotchSize(360.0, 96.0)),
    ],
)
def test_an_expanded_notch_smaller_than_the_compact_one_is_rejected(compact, expanded):
    with pytest.raises(ValueError):
        NotchGeometry(compact=compact, expanded=expanded)


def test_a_negative_top_margin_is_rejected():
    with pytest.raises(ValueError):
        NotchGeometry(top_margin=-1.0)


# --- Interpolation -----------------------------------------------------------


def test_progress_blends_every_dimension_linearly():
    halfway = GEOMETRY.size_between(NotchPresence.COMPACT, NotchPresence.EXPANDED, 0.5)

    assert halfway == NotchSize(width=280.0, height=64.0, corner_radius=19.0)


def test_the_endpoints_of_a_transition_are_its_presences():
    compact, expanded = NotchPresence.COMPACT, NotchPresence.EXPANDED

    assert GEOMETRY.size_between(compact, expanded, 0.0) == GEOMETRY.compact
    assert GEOMETRY.size_between(compact, expanded, 1.0) == GEOMETRY.expanded
    assert GEOMETRY.size_between(expanded, compact, 1.0) == GEOMETRY.compact


@pytest.mark.parametrize("progress,expected", [(-0.5, 0.0), (1.5, 1.0)])
def test_progress_outside_the_transition_is_clamped(progress, expected):
    assert GEOMETRY.size_between(
        NotchPresence.COMPACT, NotchPresence.EXPANDED, progress
    ) == GEOMETRY.size_between(NotchPresence.COMPACT, NotchPresence.EXPANDED, expected)


def test_showing_grows_the_notch_down_from_the_top_edge():
    heights = [
        GEOMETRY.shape_between(AVAILABLE, NotchPresence.HIDDEN, NotchPresence.COMPACT, p)
        for p in (0.0, 0.25, 0.5, 1.0)
    ]

    assert [shape.bounds.height for shape in heights] == [0.0, 8.0, 16.0, 32.0]
    assert {shape.bounds.y for shape in heights} == {AVAILABLE.y}
    assert {shape.bounds.width for shape in heights} == {200.0}


def test_every_interpolated_size_keeps_a_fitting_radius():
    for step in range(101):
        size = GEOMETRY.size_between(NotchPresence.HIDDEN, NotchPresence.EXPANDED, step / 100)
        assert size.corner_radius <= min(size.width, size.height) / 2.0


# --- Boundary ----------------------------------------------------------------


def test_the_presence_model_imports_only_the_standard_library():
    tree = ast.parse(PRESENCE_SOURCE.read_text())
    imported = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert imported <= {"__future__", "dataclasses", "enum"}


def test_the_presence_model_loads_with_qt_unavailable():
    code = (
        "import sys; sys.modules['PySide6'] = None; sys.modules['shiboken6'] = None\n"
        "import mira.ui.notch.presence\n"
        "assert not any(name.startswith(('PySide6', 'shiboken6')) and sys.modules[name]"
        " for name in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=PRESENCE_SOURCE.parents[3])
