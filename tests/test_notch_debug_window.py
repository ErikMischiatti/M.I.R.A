"""The notch tuning window drives the real notch through its public API."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication

from mira.domain.embodiment import ActivityState, ExpressionKey
from mira.domain.notice import NoticeCategory
from mira.ui.notch.__main__ import build_notch_environment, build_tuning_environment
from mira.ui.notch.config_store import ConfigSession, ConfigSource, NotchSettings, save_settings, settings_to_dict
from mira.ui.notch.debug_window import PREVIEW_NOTICES, NotchDebugWindow
from mira.ui.notch.eye_layout import BASE_EYE_HEIGHT_SHARE, NotchEyeStyle
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize
from mira.ui.notch.presentation import ExpansionReason, NotchBehaviour
from mira.ui.notch.screen_selection import MonitorIdentity, MonitorPreference


H, C, E = NotchPresence.HIDDEN, NotchPresence.COMPACT, NotchPresence.EXPANDED


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_path(tmp_path):
    # Never the developer's own ~/.config: every environment gets its own file.
    return tmp_path / "mira" / "notch.json"


@pytest.fixture
def env(config_path):
    environment = build_tuning_environment(ConfigSession(config_path))
    # Settle the opening transition: these tests check values, not timing.
    finish(environment.notch)
    yield environment
    environment.shutdown()
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
            debug.eye_motion_gain.value(),
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
        "eyes": (style.height_share, style.top_offset, style.center_offset, style.spacing, style.motion_gain),
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

    env.debug.eye_height_share.setValue(0.2)

    assert env.notch.eye_style.height_share == 0.2
    assert env.notch.eye_layout.scale == pytest.approx(scale * 0.2 / BASE_EYE_HEIGHT_SHARE)


def test_the_eye_top_control_moves_the_eyes_vertically(env):
    top = env.notch.eye_layout.top

    env.debug.eye_top_offset.setValue(0.5)

    assert env.notch.eye_layout.top == pytest.approx(top + 0.5)


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
    env.debug.eye_height_share.setValue(0.25)

    assert env.notch.eye_style.height_share == 0.25
    assert f"{env.notch.eye_layout.scale:.4f}" in env.debug.eye_layout_label.text()


# --- Presence and expression ------------------------------------------------------


def test_presence_buttons_go_through_the_presenter(env):
    env.debug.expanded_button.click()
    assert env.presenter.pinned
    assert env.notch.presentation.reasons == {ExpansionReason.PINNED}
    finish(env.notch)
    env.debug.compact_button.click()
    assert not env.presenter.pinned
    finish(env.notch)
    env.debug.hidden_button.click()
    finish(env.notch)
    assert env.presenter.presentation.presence is H
    env.debug.compact_button.click()

    assert env.notch.presence is C
    assert env.notch.presentation == env.presenter.presentation


def test_presence_buttons_use_the_notch_api_without_a_presenter(env, monkeypatch):
    debug = NotchDebugWindow(env.notch, env.controller)
    calls = []
    for name in ("show_notch", "hide_notch", "expand_notch", "collapse_notch"):
        original = getattr(env.notch, name)
        monkeypatch.setattr(env.notch, name, lambda o=original, n=name: (calls.append(n), o())[1])
    try:
        debug.expanded_button.click()
        finish(env.notch)
        debug.compact_button.click()
        finish(env.notch)
        debug.hidden_button.click()
        finish(env.notch)
        debug.compact_button.click()
    finally:
        debug.deleteLater()

    assert calls == ["expand_notch", "collapse_notch", "hide_notch", "show_notch"]


def test_hover_still_expands_while_pinned_and_after(env):
    env.debug.expanded_button.click()
    env.notch.hover_changed.emit(True)
    env.debug.compact_button.click()
    assert env.notch.presence is E

    env.notch.hover_changed.emit(False)
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


# --- Eye safety in the controls ------------------------------------------------------


def test_a_clipping_eye_configuration_is_reported_invalid_and_not_applied(env):
    before = env.notch.visual_config

    env.debug.eye_top_offset.setValue(5.0)

    assert env.notch.visual_config == before
    assert env.debug.visual_label.text().startswith("Invalid")
    assert "clipped" in env.debug.error_label.text() and "COMPACT" in env.debug.error_label.text()
    assert not env.debug.save_button.isEnabled()

    env.debug.eye_top_offset.setValue(0.0)
    assert env.debug.visual_label.text() == "Valid"
    assert env.debug.save_button.isEnabled()


def test_the_motion_gain_control_is_live(env):
    env.debug.eye_motion_gain.setValue(0.4)

    assert env.notch.eye_style.motion_gain == 0.4
    assert env.notch.eye_painter.offset_gain == 0.4


# --- Layout -----------------------------------------------------------------------------


def page_titles(debug: NotchDebugWindow) -> list[str]:
    return [debug.pages.tabText(index) for index in range(debug.pages.count())]


def page_of(debug: NotchDebugWindow, widget) -> str:
    for index in range(debug.pages.count()):
        if debug.pages.widget(index).widget().isAncestorOf(widget):
            return debug.pages.tabText(index)
    return "-"


def test_the_settings_are_organised_in_pages(env):
    debug = env.debug

    assert page_titles(debug) == ["Appearance", "Behaviour", "Display", "Diagnostics"]
    assert page_of(debug, debug.compact_width) == "Appearance"
    assert page_of(debug, debug.expanded_height) == "Appearance"
    assert page_of(debug, debug.eye_height_share) == "Appearance"
    assert page_of(debug, debug.expand_on_hover) == "Behaviour"
    assert page_of(debug, debug.notice_duration) == "Behaviour"
    assert page_of(debug, debug.duration) == "Behaviour"
    assert page_of(debug, debug.monitor) == "Display"
    assert page_of(debug, debug.offset_x) == "Display"
    assert page_of(debug, debug.loaded_label) == "Diagnostics"
    assert page_of(debug, debug.config_path_label) == "Diagnostics"


def test_persistence_and_presence_stay_outside_the_pages(env):
    debug = env.debug
    for widget in (debug.save_button, debug.reload_button, debug.reset_button, debug.expanded_button):
        assert page_of(debug, widget) == "-"
    for index in range(debug.pages.count()):
        assert debug.pages.widget(index).widgetResizable()


def test_the_window_fits_a_laptop_screen(env):
    available = env.debug.screen().availableGeometry()
    assert env.debug.height() <= max(int(available.height() * 0.85), 1)


def test_the_status_shows_the_technical_state(env):
    debug = env.debug
    assert debug.loaded_label.text() == "Defaults"
    assert debug.monitor_mode_label.text() == "Automatic"
    assert debug.selected_monitor_label.text() == QGuiApplication.screens()[0].name()
    assert debug.monitor_count_label.text() == str(len(QGuiApplication.screens()))
    assert debug.visual_label.text() == "Valid"
    assert debug.unsaved_label.text() == "No"
    assert debug.config_path_label.text() == str(env.session.path)


# --- Persistence --------------------------------------------------------------------------


def test_a_change_is_unsaved_until_saved(env, config_path):
    env.debug.compact_width.setValue(240.0)
    assert env.debug.unsaved_label.text() == "Yes"
    assert not config_path.exists()

    env.debug.save_button.click()

    assert config_path.exists()
    assert env.debug.unsaved_label.text() == "No"
    assert env.debug.loaded_label.text() == "Saved"
    assert ConfigSession(config_path).startup_settings == env.debug.runtime_settings


def test_reset_restores_defaults_at_run_time_without_writing(env, config_path):
    env.debug.compact_width.setValue(240.0)
    env.debug.save_button.click()
    saved = config_path.read_bytes()

    env.debug.reset_button.click()

    assert env.debug.runtime_settings == NotchSettings()
    assert config_path.read_bytes() == saved
    assert env.debug.unsaved_label.text() == "Yes"


def test_reload_discards_runtime_changes_for_the_saved_file(env):
    env.debug.compact_width.setValue(240.0)
    env.debug.save_button.click()
    env.debug.compact_width.setValue(260.0)

    env.debug.reload_button.click()

    assert env.notch.notch_geometry.compact.width == 240.0
    assert env.debug.compact_width.value() == 240.0
    assert env.debug.unsaved_label.text() == "No"


def test_reload_without_a_file_returns_to_the_defaults(env):
    env.debug.compact_width.setValue(240.0)

    env.debug.reload_button.click()

    assert env.debug.runtime_settings == NotchSettings()
    assert env.debug.loaded_label.text() == "Defaults"


def test_reload_of_an_invalid_file_keeps_the_runtime_settings(env, config_path):
    env.debug.compact_width.setValue(240.0)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{broken", encoding="utf-8")

    env.debug.reload_button.click()

    assert env.notch.notch_geometry.compact.width == 240.0
    assert env.debug.loaded_label.text() == "Invalid fallback"
    assert "Not reloaded" in env.debug.message_label.text()
    assert env.debug.unsaved_label.text() == "Yes"
    assert config_path.read_text(encoding="utf-8") == "{broken"


def test_a_saved_configuration_is_used_at_startup(config_path):
    tuned = NotchSettings(
        visual=NotchSettings().visual.__class__(
            geometry=NotchGeometry(compact=NotchSize(240.0, 36.0, 14.0), expanded=NotchSize(380.0, 100.0, 24.0), top_margin=3.0),
            eye_style=NotchEyeStyle(height_share=0.26),
            transition_duration_ms=320,
        ),
    )
    save_settings(tuned, config_path)

    environment = build_notch_environment(ConfigSession(config_path), tuning=False)
    try:
        assert environment.debug is None
        assert environment.notch.visual_config == tuned.visual
        assert environment.notch.isVisible()
    finally:
        environment.notch.hide()
        environment.notch.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_an_invalid_saved_configuration_starts_with_the_defaults_and_says_why(config_path):
    data = settings_to_dict(NotchSettings())
    data["eyes"]["height_share"] = 0.5
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(data), encoding="utf-8")

    environment = build_tuning_environment(ConfigSession(config_path))
    try:
        assert environment.notch.visual_config == NotchSettings().visual
        assert environment.notch.isVisible()
        assert environment.debug.loaded_label.text() == "Invalid fallback"
        assert "ignored" in environment.debug.message_label.text()
        assert json.loads(config_path.read_text(encoding="utf-8")) == data
    finally:
        environment.notch.hide()
        for widget in (environment.debug, environment.notch):
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_without_a_session_nothing_can_be_saved():
    environment = build_tuning_environment()
    try:
        assert environment.session is None
        assert not environment.debug.save_button.isEnabled()
        assert not environment.debug.reload_button.isEnabled()
        assert environment.debug.reset_button.isEnabled()
    finally:
        environment.notch.hide()
        for widget in (environment.debug, environment.notch):
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- Display ---------------------------------------------------------------------------------


def monitor_entries(debug: NotchDebugWindow) -> list[str]:
    return [debug.monitor.itemText(index) for index in range(debug.monitor.count())]


def test_the_monitor_list_offers_automatic_and_every_connected_screen(env):
    entries = monitor_entries(env.debug)

    assert entries[0] == "Automatic"
    assert entries[1:] == [screen.name() for screen in QGuiApplication.screens()]
    assert "selected" in env.debug.monitors_label.text() and "primary" in env.debug.monitors_label.text()
    assert env.debug.monitor.currentIndex() == 0


def test_choosing_a_monitor_sets_the_preference_and_is_persisted(env, config_path):
    env.debug.monitor.setCurrentIndex(1)

    assert not env.notch.monitor_preference.is_automatic
    assert env.debug.monitor_mode_label.text() == "Preferred"
    env.debug.save_button.click()
    assert json.loads(config_path.read_text())["display"]["monitor"]["name"] == QGuiApplication.screens()[0].name()

    env.debug.monitor.setCurrentIndex(0)
    assert env.notch.monitor_preference.is_automatic


def test_an_absent_preferred_monitor_is_listed_and_falls_back(env):
    absent = MonitorPreference(MonitorIdentity("DP-9", "Dell Inc.", "DELL P2425H", "1"))

    env.notch.set_monitor_preference(absent)

    assert monitor_entries(env.debug)[-1] == "DP-9 (DELL P2425H) — not connected"
    assert env.debug.monitor.currentIndex() == env.debug.monitor.count() - 1
    assert "not connected" in env.debug.monitor_mode_label.text()
    assert env.notch.tracked_screen is QGuiApplication.screens()[0]


def test_the_monitor_list_follows_topology_changes(env):
    rebuilt = []
    original = env.debug._rebuild_monitor_list
    env.debug._rebuild_monitor_list = lambda: (rebuilt.append(True), original())[1]

    QGuiApplication.instance().screenAdded.emit(QGuiApplication.primaryScreen())

    assert rebuilt


# --- Behaviour ---------------------------------------------------------------------------------


def behaviour_controls(debug: NotchDebugWindow) -> dict:
    return {
        "hover": debug.expand_on_hover.isChecked(),
        "automatic": {category for category, box in debug.automatic.items() if box.isChecked()},
        "duration": debug.notice_duration.value(),
    }


TUNED_BEHAVIOUR = NotchBehaviour(
    expand_on_hover=False, automatic_expansion=frozenset({NoticeCategory.TASK}), notice_duration_ms=7000
)


def test_the_behaviour_controls_show_the_production_defaults(env):
    assert behaviour_controls(env.debug) == {
        "hover": True,
        "automatic": {NoticeCategory.ALERT, NoticeCategory.DEVICE},
        "duration": 4000,
    }
    assert set(env.debug.automatic) == set(NoticeCategory)


def test_saved_behaviour_loads_into_the_controls_and_the_presenter(config_path):
    save_settings(NotchSettings(behaviour=TUNED_BEHAVIOUR), config_path)
    environment = build_tuning_environment(ConfigSession(config_path))
    try:
        assert environment.presenter.behaviour == TUNED_BEHAVIOUR
        assert behaviour_controls(environment.debug) == {
            "hover": False, "automatic": {NoticeCategory.TASK}, "duration": 7000,
        }
    finally:
        environment.shutdown()
        environment.notch.hide()
        for widget in (environment.debug, environment.notch):
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_behaviour_changes_apply_live_and_are_unsaved(env):
    env.debug.expand_on_hover.setChecked(False)
    env.debug.automatic[NoticeCategory.TASK].setChecked(True)
    env.debug.notice_duration.setValue(9000)

    assert env.presenter.behaviour == NotchBehaviour(
        expand_on_hover=False,
        automatic_expansion=frozenset({NoticeCategory.ALERT, NoticeCategory.DEVICE, NoticeCategory.TASK}),
        notice_duration_ms=9000,
    )
    assert env.debug.unsaved_label.text() == "Yes"
    env.notch.hover_changed.emit(True)
    assert env.notch.presence is C


def test_save_persists_the_behaviour(env, config_path):
    env.debug.automatic[NoticeCategory.DEVICE].setChecked(False)
    env.debug.notice_duration.setValue(2500)

    env.debug.save_button.click()

    assert env.debug.unsaved_label.text() == "No"
    saved = json.loads(config_path.read_text())["behaviour"]
    assert saved["automatic_expansion"]["device"] is False
    assert saved["notice_duration_ms"] == 2500
    assert ConfigSession(config_path).startup_settings.behaviour == env.presenter.behaviour


def test_reload_restores_the_saved_behaviour(env):
    env.debug.notice_duration.setValue(2500)
    env.debug.save_button.click()
    env.debug.expand_on_hover.setChecked(False)
    env.debug.notice_duration.setValue(12000)

    env.debug.reload_button.click()

    assert env.presenter.behaviour.notice_duration_ms == 2500
    assert env.presenter.behaviour.expand_on_hover
    assert behaviour_controls(env.debug)["duration"] == 2500
    assert env.debug.unsaved_label.text() == "No"


def test_reset_restores_the_default_behaviour_in_memory_only(env, config_path):
    env.debug.expand_on_hover.setChecked(False)
    env.debug.save_button.click()
    written = config_path.read_bytes()

    env.debug.reset_button.click()

    assert env.presenter.behaviour == NotchBehaviour()
    assert env.debug.expand_on_hover.isChecked()
    assert env.debug.unsaved_label.text() == "Yes"
    assert config_path.read_bytes() == written


def test_a_version_1_file_is_shown_as_migrated_and_unchanged(config_path):
    data = settings_to_dict(NotchSettings())
    data.pop("behaviour")
    data["version"] = 1
    data["compact"]["width"] = 230.0
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(data), encoding="utf-8")

    environment = build_tuning_environment(ConfigSession(config_path))
    try:
        debug = environment.debug
        assert debug.loaded_label.text() == "Saved"
        assert debug.schema_label.text().startswith("Version 1")
        assert debug.unsaved_label.text() == "No"
        assert debug.compact_width.value() == 230.0
        assert environment.presenter.behaviour == NotchBehaviour()

        debug.save_button.click()
        assert json.loads(config_path.read_text())["version"] == 2
        assert debug.schema_label.text() == "Version 2"
    finally:
        environment.shutdown()
        environment.notch.hide()
        for widget in (environment.debug, environment.notch):
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- Previews -------------------------------------------------------------------------------------


def test_previews_show_activity_and_notices_on_this_notch_only(env):
    env.debug.preview_buttons["thinking"].click()
    assert env.presenter.presentation.activity is ActivityState.THINKING
    assert env.controller.intent.activity is ActivityState.THINKING
    assert env.notch.presence is C  # activity never expands

    env.debug.preview_buttons["Warning"].click()
    assert env.notch.presence is E
    assert env.presenter.presentation.notice == PREVIEW_NOTICES["Warning"]
    assert "notice" in env.debug.presentation_label.text()


@pytest.mark.parametrize("name", list(PREVIEW_NOTICES))
def test_every_preview_notice_presents(env, name):
    env.debug.preview_buttons[name].click()

    assert env.notch.presentation.notice == PREVIEW_NOTICES[name]


def test_previews_can_be_left_out(env):
    debug = NotchDebugWindow(env.notch, None, env.session, env.presenter, previews=False)
    try:
        assert debug.preview_buttons == {}
        assert not debug.expression.isEnabled()
    finally:
        debug.deleteLater()


def test_without_a_presenter_behaviour_controls_are_disabled_and_kept(env):
    debug = NotchDebugWindow(env.notch, env.controller, env.session)
    try:
        assert not debug.expand_on_hover.isEnabled()
        assert not debug.notice_duration.isEnabled()
        assert debug.runtime_settings.behaviour == env.session.startup_settings.behaviour
    finally:
        debug.deleteLater()
