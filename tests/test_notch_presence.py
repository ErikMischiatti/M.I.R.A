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
    remaining_fraction,
    size_distance,
    window_bounds,
    window_shape,
    window_shape_for_size,
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


# --- Window placement --------------------------------------------------------


def test_the_window_is_the_expanded_notch_hung_top_centre_from_the_area():
    assert window_bounds(AVAILABLE, GEOMETRY) == Rect(780, 32, 360, 96)


def test_the_window_follows_an_area_with_a_negative_origin():
    left_monitor = Rect(x=-1280.0, y=-200.0, width=1280.0, height=1024.0)

    assert window_bounds(left_monitor, GEOMETRY) == Rect(-820, -200, 360, 96)


def test_the_window_size_rounds_up_so_the_expanded_notch_always_fits():
    fractional = NotchGeometry(
        compact=NotchSize(200.0, 32.0, 14.0), expanded=NotchSize(360.4, 96.2, 24.0)
    )

    bounds = window_bounds(AVAILABLE, fractional)

    assert (bounds.width, bounds.height) == (361, 97)


@pytest.mark.parametrize(
    "area_x,area_width,expected_x",
    [
        # An odd leftover floors: half a pixel left of centre, never right.
        (0.0, 1001.0, 320),
        # Towards -inf on a negative origin too, not towards zero.
        (-1281.0, 1001.0, -961),
    ],
)
def test_the_window_origin_rounds_down(area_x, area_width, expected_x):
    area = Rect(x=area_x, y=0.0, width=area_width, height=800.0)

    assert window_bounds(area, GEOMETRY).x == expected_x


def test_a_fractional_top_margin_rounds_the_window_up_the_screen():
    geometry = NotchGeometry(top_margin=6.5)

    assert window_bounds(AVAILABLE, geometry).y == 38


def test_window_bounds_are_whole_pixels():
    geometry = NotchGeometry(
        compact=NotchSize(200.3, 32.0, 14.0), expanded=NotchSize(360.7, 96.5, 24.0), top_margin=1.25
    )
    bounds = window_bounds(Rect(-3.0, 7.0, 1917.0, 1000.0), geometry)

    assert all(float(value).is_integer() for value in (bounds.x, bounds.y, bounds.width, bounds.height))


def test_the_window_shape_hangs_centred_from_the_window_top():
    window = window_bounds(AVAILABLE, GEOMETRY)

    compact = window_shape(window, GEOMETRY, NotchPresence.COMPACT)
    expanded = window_shape(window, GEOMETRY, NotchPresence.EXPANDED)
    hidden = window_shape(window, GEOMETRY, NotchPresence.HIDDEN)

    assert compact == NotchShape(bounds=Rect(80.0, 0.0, 200.0, 32.0), corner_radius=14.0)
    assert expanded == NotchShape(bounds=Rect(0.0, 0.0, 360.0, 96.0), corner_radius=24.0)
    assert hidden.bounds.height == 0.0


def test_the_window_shape_ignores_the_margin_the_window_origin_already_carries():
    geometry = NotchGeometry(top_margin=10.0)
    window = window_bounds(AVAILABLE, geometry)

    assert window_shape(window, geometry, NotchPresence.EXPANDED).bounds.y == 0.0


def test_a_rounded_up_window_contains_the_fractional_expanded_notch():
    fractional = NotchGeometry(
        compact=NotchSize(200.0, 32.0, 14.0), expanded=NotchSize(360.4, 96.2, 24.0)
    )
    window = window_bounds(AVAILABLE, fractional)

    bounds = window_shape(window, fractional, NotchPresence.EXPANDED).bounds

    assert bounds.x >= 0 and bounds.right <= window.width
    assert bounds.y >= 0 and bounds.bottom <= window.height
    assert bounds.center_x == window.width / 2.0


# --- Transition helpers ------------------------------------------------------


def test_any_size_is_placed_like_a_presence_size():
    window = window_bounds(AVAILABLE, GEOMETRY)
    between = GEOMETRY.size_between(NotchPresence.COMPACT, NotchPresence.EXPANDED, 0.5)

    shape = window_shape_for_size(window, GEOMETRY, between)

    assert shape.bounds == Rect(40.0, 0.0, 280.0, 64.0)
    assert window_shape_for_size(window, GEOMETRY, GEOMETRY.compact) == window_shape(
        window, GEOMETRY, NotchPresence.COMPACT
    )


def test_size_distance_measures_width_and_height():
    assert size_distance(NotchSize(0.0, 0.0), NotchSize(3.0, 4.0)) == 5.0
    assert size_distance(GEOMETRY.compact, GEOMETRY.compact) == 0.0


def test_a_transition_from_rest_has_its_whole_path_ahead():
    assert remaining_fraction(GEOMETRY.compact, GEOMETRY.compact, GEOMETRY.expanded) == 1.0


@pytest.mark.parametrize("progress", [0.25, 0.4, 0.5, 0.9])
def test_a_reversed_transition_has_only_the_travelled_share_left(progress):
    current = GEOMETRY.compact.lerp(GEOMETRY.expanded, progress)

    # Interrupted on the way to expanded and sent back to compact.
    assert remaining_fraction(current, GEOMETRY.expanded, GEOMETRY.compact) == pytest.approx(progress)


def test_the_remaining_fraction_is_clamped_and_empty_paths_are_done():
    beyond = NotchSize(width=900.0, height=300.0)

    assert remaining_fraction(beyond, GEOMETRY.compact, GEOMETRY.expanded) == 1.0
    assert remaining_fraction(GEOMETRY.expanded, GEOMETRY.compact, GEOMETRY.compact) == 0.0


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

    assert imported <= {"__future__", "dataclasses", "enum", "math"}


def test_the_presence_model_loads_with_qt_unavailable():
    code = (
        "import sys; sys.modules['PySide6'] = None; sys.modules['shiboken6'] = None\n"
        "import mira.ui.notch.presence\n"
        "assert not any(name.startswith(('PySide6', 'shiboken6')) and sys.modules[name]"
        " for name in sys.modules)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=PRESENCE_SOURCE.parents[3])
