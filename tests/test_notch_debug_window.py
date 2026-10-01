"""The notch tuning window drives the real notch through its public API."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication

from mira.domain.embodiment import ExpressionKey
from mira.ui.notch.__main__ import build_tuning_environment
from mira.ui.notch.debug_window import NotchDebugWindow
from mira.ui.notch.eye_layout import NotchEyeStyle
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize


H, C, E = NotchPresence.HIDDEN, NotchPresence.COMPACT, NotchPresence.EXPANDED


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env():
    environment = build_tuning_environment()
    # Settle the opening transition: these tests check values, not timing.
    finish(environment.notch)
    yield environment
    environment.animator.stop()
    environment.notch.transition.stop()
    environment.notch.hide()
    for widget in (environment.debug, environment.notch, environment.animator):
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def finish(notch: NotchWindow) -> None:
    transition = notch.transition
    if transition.state() is not QAbstractAnimation.State.Stopped:
        transition.setCurrentTime(transition.duration())


def run_event_loop(milliseconds: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def control_values(debug: NotchDebugWindow) -> dict:
    return {
        "compact": (debug.compact_width.value(), debug.compact_height.value(), debug.compact_radius.value()),
        "expanded": (debug.expanded_width.value(), debug.expanded_height.value(), debug.expanded_radius.value()),
        "placement": (debug.offset_x.value(), debug.top_margin.value()),
        "duration": debug.duration.value(),
        "eyes": (
            debug.eye_height_share.value(),
            debug.eye_top_offset.value(),
            debug.eye_center_offset.value(),
            debug.eye_spacing.value(),
        ),
        "expression": debug.selected_expression,
    }


def production_values() -> dict:
    geometry, style = NotchGeometry(), NotchEyeStyle()
    return {
        "compact": (geometry.compact.width, geometry.compact.height, geometry.compact.corner_radius),
        "expanded": (geometry.expanded.width, geometry.expanded.height, geometry.expanded.corner_radius),
        "placement": (geometry.offset_x, geometry.top_margin),
        "duration": NotchWindow.TRANSITION_DURATION_MS,
        "eyes": (style.height_share, style.top_offset, style.center_offset, style.spacing),
        "expression": ExpressionKey.IDLE,
    }


# --- Start-up --------------------------------------------------------------------


def test_the_environment_starts_compact_with_the_idle_expression(env):
    assert env.notch.presence is C
    assert env.notch.isVisible()
    assert env.controller.expression_key is ExpressionKey.IDLE
    assert env.notch.animator is env.animator and env.animator.controller is env.controller


def test_the_controls_show_the_production_defaults(env):
    assert control_values(env.debug) == production_values()


def test_the_controls_read_the_values_the_notch_really_uses():
    notch = NotchWindow(
        NotchGeometry(compact=NotchSize(180.0, 30.0, 10.0), expanded=NotchSize(300.0, 80.0, 20.0), offset_x=7.0),
        eye_style=NotchEyeStyle(height_share=0.4, spacing=320.0),
    )
    notch.transition_duration_ms = 350
    debug = NotchDebugWindow(notch)

    values = control_values(debug)

    assert values["compact"] == (180.0, 30.0, 10.0)
    assert values["placement"] == (7.0, 0.0)
    assert values["duration"] == 350
    assert values["eyes"][0] == 0.4 and values["eyes"][3] == 320.0
    for widget in (debug, notch):
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_nothing_changes_on_its_own(env):
    env.animator.start()
    geometry, style, size = env.notch.notch_geometry, env.notch.eye_style, env.notch.size()

    run_event_loop(400)

    assert env.notch.presence is C
    assert env.controller.expression_key is ExpressionKey.IDLE
    assert (env.notch.notch_geometry, env.notch.eye_style, env.notch.size()) == (geometry, style, size)
    assert env.notch.transition.state() is QAbstractAnimation.State.Stopped


def test_the_screen_is_still_chosen_by_the_selection_policy(env):
    env.debug.expanded_width.setValue(500.0)
    env.debug.offset_x.setValue(120.0)

    assert env.notch.tracked_screen is QGuiApplication.screens()[0]


# --- Geometry --------------------------------------------------------------------


def test_compact_controls_update_the_compact_notch_live(env):
    env.debug.compact_width.setValue(240.0)
    env.debug.compact_height.setValue(40.0)

    assert env.notch.notch_geometry.compact == NotchSize(240.0, 40.0, 14.0)
    assert env.notch.visual_size == NotchSize(240.0, 40.0, 14.0)
    assert env.notch.shape.bounds.width == 240.0


def test_expanded_controls_update_the_expanded_notch_live(env):
    env.debug.show_expanded()
    finish(env.notch)

    env.debug.expanded_width.setValue(420.0)
    env.debug.expanded_height.setValue(110.0)

    assert env.notch.visual_size == NotchSize(420.0, 110.0, 24.0)
    assert (env.notch.width(), env.notch.height()) == (420, 110)


def test_placement_offsets_move_the_notch_after_centring(env):
    x, y = env.notch.x(), env.notch.y()

    env.debug.offset_x.setValue(-60.0)
    env.debug.top_margin.setValue(15.0)

    assert (env.notch.x(), env.notch.y()) == (x - 60, y + 15)


def test_the_corner_radius_and_duration_are_live(env):
    env.debug.compact_radius.setValue(8.0)
    env.debug.duration.setValue(450)

    assert env.notch.shape.corner_radius == 8.0
    assert env.notch.transition_duration_ms == 450
    env.debug.show_expanded()
    assert env.notch.transition.duration() == 450


def test_an_impossible_geometry_is_reported_and_not_applied(env):
    before = env.notch.notch_geometry

    env.debug.compact_width.setValue(900.0)  # wider than the expanded notch

    assert env.notch.notch_geometry == before
    assert "Not applied" in env.debug.error_label.text()
    env.debug.expanded_width.setValue(1000.0)
    assert env.notch.notch_geometry.compact.width == 900.0
    assert env.debug.error_label.text() == ""


def test_a_geometry_change_mid_transition_keeps_one_running_transition(env):
    env.debug.show_expanded()
    env.notch.transition.pause()
    env.notch.transition.setCurrentTime(60)
    drawn = env.notch.visual_size

    env.debug.expanded_height.setValue(120.0)

    assert env.notch.visual_size == drawn
    assert env.notch.transition.state() is QAbstractAnimation.State.Running
    finish(env.notch)
    assert env.notch.visual_size.height == 120.0


# --- Eyes ------------------------------------------------------------------------


def test_the_eye_scale_control_changes_the_eye_scale(env):
    scale = env.notch.eye_layout.scale

    env.debug.eye_height_share.setValue(0.5)

    assert env.notch.eye_style.height_share == 0.5
    assert env.notch.eye_layout.scale == pytest.approx(scale * 0.5 / 0.375)


def test_the_eye_top_control_moves_the_eyes_vertically(env):
    top = env.notch.eye_layout.top

    env.debug.eye_top_offset.setValue(5.0)

    assert env.notch.eye_layout.top == top + 5.0


def test_the_eye_centre_and_spacing_controls_move_the_eyes(env):
    centre = env.notch.eye_layout.center_x

    env.debug.eye_center_offset.setValue(-12.0)
    env.debug.eye_spacing.setValue(400.0)

    layout = env.notch.eye_layout
    assert layout.center_x == centre - 12.0
    assert layout.resting_eye("right").center_x - layout.resting_eye("left").center_x == pytest.approx(
        400.0 * layout.scale
    )


def test_the_derived_layout_is_shown(env):
    env.debug.eye_height_share.setValue(0.5)

    assert f"{env.notch.eye_layout.scale:.4f}" in env.debug.eye_layout_label.text()


# --- Presence and expression ------------------------------------------------------


def test_presence_buttons_use_the_production_presence_api(env, monkeypatch):
    calls = []
    for name in ("show_notch", "hide_notch", "expand_notch", "collapse_notch"):
        original = getattr(env.notch, name)
        monkeypatch.setattr(env.notch, name, lambda o=original, n=name: (calls.append(n), o())[1])

    env.debug.expanded_button.click()
    finish(env.notch)
    env.debug.compact_button.click()
    finish(env.notch)
    env.debug.hidden_button.click()
    finish(env.notch)
    env.debug.compact_button.click()

    assert calls == ["expand_notch", "collapse_notch", "hide_notch", "show_notch"]
    assert env.notch.presence is C


def test_presence_buttons_reach_each_state(env):
    env.debug.expanded_button.click()
    assert env.notch.presence is E
    env.debug.hidden_button.click()
    finish(env.notch)
    assert env.notch.presence is H and not env.notch.isVisible()
    env.debug.compact_button.click()
    assert env.notch.presence is C and env.notch.isVisible()


@pytest.mark.parametrize("key", list(ExpressionKey))
def test_the_expression_selector_goes_through_the_controller(env, key):
    other = ExpressionKey.CONFUSED if key is not ExpressionKey.CONFUSED else ExpressionKey.HAPPY
    env.debug.expression.setCurrentIndex(env.debug.expression.findData(other.value))
    env.debug.expression.setCurrentIndex(env.debug.expression.findData(key.value))

    assert env.controller.expression_key is key
    assert isinstance(env.controller.intent.expression, ExpressionKey)


def test_the_selector_offers_exactly_the_existing_expressions(env):
    offered = [ExpressionKey(env.debug.expression.itemData(i)) for i in range(env.debug.expression.count())]

    assert offered == list(ExpressionKey)


# --- Reset and close ----------------------------------------------------------------


def test_reset_restores_the_production_defaults(env):
    env.debug.compact_width.setValue(260.0)
    env.debug.expanded_height.setValue(130.0)
    env.debug.offset_x.setValue(40.0)
    env.debug.duration.setValue(900)
    env.debug.eye_height_share.setValue(0.6)
    env.debug.eye_spacing.setValue(500.0)
    env.debug.compact_width.setValue(5000.0)  # leaves an error showing

    env.debug.reset_button.click()

    assert env.notch.notch_geometry == NotchGeometry()
    assert env.notch.eye_style == NotchEyeStyle()
    assert env.notch.transition_duration_ms == NotchWindow.TRANSITION_DURATION_MS
    assert control_values(env.debug) == production_values()
    assert env.debug.error_label.text() == ""


def test_closing_the_tuning_window_signals_it(env):
    closed = []
    env.debug.closed.connect(lambda: closed.append(True))
    env.debug.show()

    env.debug.close()

    assert closed == [True]
