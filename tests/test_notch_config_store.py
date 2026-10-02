"""Persisting the notch's tuned settings: Qt-free, against real files."""

from __future__ import annotations

import ast
import json
import os
import pathlib

import pytest

import mira.ui.notch.config_store as config_store
from mira.ui.notch.config_store import (
    SCHEMA_VERSION,
    ConfigSession,
    ConfigSource,
    NotchSettings,
    config_path_from,
    default_config_path,
    load_settings,
    save_settings,
    settings_from_dict,
    settings_to_dict,
)
from mira.domain.notice import NoticeCategory
from mira.ui.notch.expanded_layout import MIN_SYMBOL_PX, MIN_TEXT_PX, NotchContentStyle
from mira.ui.notch.eye_layout import NotchEyeStyle, production_envelope
from mira.ui.notch.presentation import NotchBehaviour
from mira.ui.notch.presence import NotchGeometry, NotchSize
from mira.ui.notch.screen_selection import MonitorIdentity, MonitorPreference
from mira.ui.notch.visual_config import (
    InvalidVisualConfig,
    NotchVisualConfig,
    content_row_for,
    eye_layout_for,
    eye_motion_bottom,
)


TUNED = NotchSettings(
    visual=NotchVisualConfig(
        geometry=NotchGeometry(
            compact=NotchSize(220.0, 36.0, 16.0),
            expanded=NotchSize(400.0, 110.0, 26.0),
            top_margin=4.0,
            offset_x=-35.5,
        ),
        eye_style=NotchEyeStyle(height_share=0.25, top_offset=0.5, center_offset=-6.0, spacing=320.0, motion_gain=0.6),
        transition_duration_ms=260,
        content_style=NotchContentStyle(symbol_px=30.0, text_px=20.0, spacing_px=12.0, eye_gap_px=14.0),
    ),
    monitor=MonitorPreference(MonitorIdentity("HDMI-1", "Philips Consumer Electronics Company", "Philips 298P4", "1844")),
    behaviour=NotchBehaviour(
        expand_on_hover=False,
        automatic_expansion=frozenset({NoticeCategory.TASK}),
        notice_duration_ms=6500,
    ),
)


@pytest.fixture
def path(tmp_path) -> pathlib.Path:
    return tmp_path / "config" / "mira" / "notch.json"


def write(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


# --- Location -------------------------------------------------------------------


def test_the_default_location_follows_xdg(tmp_path):
    home = tmp_path / "home"
    assert config_path_from(explicit=None, xdg_config_home=None, home=home) == home / ".config/mira/notch.json"
    assert config_path_from(explicit=None, xdg_config_home="/xdg", home=home) == pathlib.Path("/xdg/mira/notch.json")
    # A relative XDG_CONFIG_HOME is ignored, as the specification requires.
    assert config_path_from(explicit=None, xdg_config_home="rel", home=home) == home / ".config/mira/notch.json"


def test_an_explicit_path_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_NOTCH_CONFIG", str(tmp_path / "elsewhere.json"))
    assert default_config_path() == tmp_path / "elsewhere.json"
    assert config_path_from(explicit="  ", xdg_config_home="/xdg", home=tmp_path) == pathlib.Path("/xdg/mira/notch.json")


# --- Round trip ---------------------------------------------------------------------


def test_the_defaults_serialize_to_the_documented_schema():
    data = settings_to_dict(NotchSettings())

    assert data == {
        "version": SCHEMA_VERSION,
        "compact": {"width": 200.0, "height": 32.0, "radius": 14.0},
        "expanded": {"width": 400.0, "height": 112.0, "radius": 26.0},
        "placement": {"offset_x": 0.0, "top_margin": 0.0},
        "eyes": {"height_share": 0.28, "top_offset": 0.0, "center_offset": 0.0, "spacing": 300.0, "motion_gain": 0.5},
        "animation": {"duration_ms": 200},
        "display": {"monitor": None},
        "behaviour": {
            "expand_on_hover": True,
            "automatic_expansion": {"alert": True, "device": True, "task": False, "connectivity": True},
            "notice_duration_ms": 4000,
        },
        "content": {"symbol_px": 28.0, "text_px": 20.0, "spacing_px": 12.0, "eye_gap_px": 22.0},
    }


def test_settings_round_trip_through_a_dict():
    assert settings_from_dict(settings_to_dict(TUNED)) == TUNED
    assert settings_from_dict(settings_to_dict(NotchSettings())) == NotchSettings()


def test_save_then_reload_round_trips(path):
    save_settings(TUNED, path)

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.SAVED
    assert loaded.settings == TUNED
    assert loaded.diagnostic is None


def test_saving_twice_replaces_the_file(path):
    save_settings(TUNED, path)
    save_settings(NotchSettings(), path)

    assert load_settings(path).settings == NotchSettings()


def test_integers_in_the_file_are_accepted_as_numbers(path):
    data = settings_to_dict(NotchSettings())
    data["compact"] = {"width": 200, "height": 32, "radius": 14}
    write(path, data)

    assert load_settings(path).settings == NotchSettings()


# --- Missing, malformed, invalid ---------------------------------------------------


def test_a_missing_file_yields_the_defaults_without_a_diagnostic(path):
    loaded = load_settings(path)

    assert loaded.source is ConfigSource.DEFAULTS
    assert loaded.settings == NotchSettings()
    assert loaded.diagnostic is None
    assert not path.exists()


@pytest.mark.parametrize(
    "content,reason",
    [
        ("{not json", "not valid JSON"),
        ("", "not valid JSON"),
        (b"\xff\xfe\x00".decode("latin-1"), "not valid JSON"),
    ],
)
def test_a_malformed_file_falls_back_and_is_left_untouched(path, content, reason):
    write(path, content)
    before = path.read_bytes()

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.INVALID_FALLBACK
    assert loaded.settings == NotchSettings()
    assert reason in loaded.diagnostic and str(path) in loaded.diagnostic
    assert path.read_bytes() == before


def structurally_broken():
    good = settings_to_dict(NotchSettings())

    def changed(edit):
        data = json.loads(json.dumps(good))
        edit(data)
        return data

    return {
        "not an object": [1, 2, 3],
        "future version": changed(lambda d: d.update(version=SCHEMA_VERSION + 1)),
        "version zero": changed(lambda d: d.update(version=0)),
        "behaviour in version 1": changed(lambda d: d.update(version=1)),
        "missing behaviour": changed(lambda d: d.pop("behaviour")),
        "unknown behaviour key": changed(lambda d: d["behaviour"].update(sticky=True)),
        "unknown category": changed(lambda d: d["behaviour"]["automatic_expansion"].update(perception=True)),
        "content in version 2": changed(lambda d: d.update(version=2)),
        "text below the readable floor": changed(lambda d: d["content"].update(text_px=8.0)),
        "missing content": changed(lambda d: d.pop("content")),
        "unknown content key": changed(lambda d: d["content"].update(offset_x=2.0)),
        "missing content key": changed(lambda d: d["content"].pop("text_px")),
        "symbol too small": changed(lambda d: d["content"].update(symbol_px=10.0)),
        "negative eye gap": changed(lambda d: d["content"].update(eye_gap_px=-1.0)),
        "string text size": changed(lambda d: d["content"].update(text_px="18")),
        "automatic expansion not an object": changed(lambda d: d["behaviour"].update(automatic_expansion=[])),
        "non-boolean switch": changed(lambda d: d["behaviour"].update(expand_on_hover=1)),
        "non-boolean category": changed(lambda d: d["behaviour"]["automatic_expansion"].update(alert="yes")),
        "notice too short": changed(lambda d: d["behaviour"].update(notice_duration_ms=10)),
        "fractional notice": changed(lambda d: d["behaviour"].update(notice_duration_ms=4000.5)),
        "boolean version": changed(lambda d: d.update(version=True)),
        "missing section": changed(lambda d: d.pop("eyes")),
        "unknown section": changed(lambda d: d.update(extra={})),
        "missing key": changed(lambda d: d["compact"].pop("radius")),
        "unknown key": changed(lambda d: d["placement"].update(offset_y=3)),
        "string number": changed(lambda d: d["compact"].update(width="200")),
        "boolean number": changed(lambda d: d["eyes"].update(spacing=True)),
        "fractional duration": changed(lambda d: d["animation"].update(duration_ms=20.5)),
        "negative duration": changed(lambda d: d["animation"].update(duration_ms=-1)),
        "radius too large": changed(lambda d: d["compact"].update(radius=40.0)),
        "expanded smaller than compact": changed(lambda d: d["expanded"].update(width=100.0)),
        "negative top margin": changed(lambda d: d["placement"].update(top_margin=-2.0)),
        "zero motion gain": changed(lambda d: d["eyes"].update(motion_gain=0.0)),
        "monitor not an object": changed(lambda d: d["display"].update(monitor="HDMI-1")),
        "monitor without a name": changed(
            lambda d: d["display"].update(monitor={"name": "", "manufacturer": "", "model": "", "serial": ""})
        ),
    }


@pytest.mark.parametrize("label", list(structurally_broken()))
def test_a_structurally_invalid_file_falls_back_and_is_left_untouched(path, label):
    write(path, structurally_broken()[label])
    before = path.read_bytes()

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.INVALID_FALLBACK, label
    assert loaded.settings == NotchSettings()
    assert "schema" in loaded.diagnostic
    assert path.read_bytes() == before


def test_a_non_finite_number_is_structurally_invalid(path):
    write(path, json.dumps(settings_to_dict(NotchSettings())).replace('"spacing": 300.0', '"spacing": NaN'))

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.INVALID_FALLBACK and "finite" in loaded.diagnostic


def test_a_visually_invalid_file_falls_back_with_the_reason(path):
    data = settings_to_dict(NotchSettings())
    data["eyes"]["height_share"] = 0.375
    data["eyes"]["motion_gain"] = 1.0
    write(path, data)
    before = path.read_bytes()

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.INVALID_FALLBACK
    assert loaded.settings == NotchSettings()
    assert "invalid visual configuration" in loaded.diagnostic and "COMPACT" in loaded.diagnostic
    assert path.read_bytes() == before


def test_an_unreadable_file_falls_back(path):
    path.mkdir(parents=True)  # a directory where the file should be

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.INVALID_FALLBACK and "cannot be read" in loaded.diagnostic


# --- Saving -------------------------------------------------------------------------


def test_an_invalid_configuration_is_never_saved(path):
    save_settings(TUNED, path)
    before = path.read_bytes()
    clipping = NotchSettings(visual=NotchVisualConfig(eye_style=NotchEyeStyle(height_share=0.4)))

    with pytest.raises(InvalidVisualConfig):
        save_settings(clipping, path)

    assert path.read_bytes() == before


def test_saving_creates_missing_directories(path):
    assert not path.parent.exists()
    save_settings(NotchSettings(), path)
    assert path.is_file()


def test_saving_leaves_no_temporary_files(path):
    save_settings(TUNED, path)
    save_settings(NotchSettings(), path)

    assert sorted(child.name for child in path.parent.iterdir()) == ["notch.json"]


def test_a_failed_write_keeps_the_previous_file_and_cleans_up(path, monkeypatch):
    save_settings(TUNED, path)
    before = path.read_bytes()

    def broken_replace(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr(config_store.os, "replace", broken_replace)
    with pytest.raises(OSError):
        save_settings(NotchSettings(), path)

    assert path.read_bytes() == before
    assert sorted(child.name for child in path.parent.iterdir()) == ["notch.json"]


def test_the_write_is_an_atomic_replace_of_a_fully_written_file(path, monkeypatch):
    replaced = []
    real_replace = os.replace

    def recording_replace(source, destination):
        # At the moment of the rename the temporary file is complete.
        replaced.append((pathlib.Path(source).parent, json.loads(pathlib.Path(source).read_text())))
        real_replace(source, destination)

    monkeypatch.setattr(config_store.os, "replace", recording_replace)
    save_settings(TUNED, path)

    assert replaced == [(path.parent, settings_to_dict(TUNED))]


def test_the_saved_file_is_readable_json_with_the_schema_version(path):
    save_settings(TUNED, path)

    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["version"] == SCHEMA_VERSION
    assert data["display"]["monitor"]["serial"] == "1844"


def test_production_defaults_never_change_by_saving(path):
    save_settings(TUNED, path)

    assert NotchSettings() == NotchSettings(visual=NotchVisualConfig(), monitor=MonitorPreference())
    assert NotchSettings() != TUNED


def test_the_store_imports_no_qt():
    source = pathlib.Path(config_store.__file__)
    tree = ast.parse(source.read_text())
    imported = {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "PySide6" not in imported


# --- Tuning session -------------------------------------------------------------------


def test_a_session_without_a_file_starts_from_the_saved_nothing(path):
    session = config_store.ConfigSession(path)

    assert session.source is ConfigSource.DEFAULTS
    assert session.startup_settings == NotchSettings()
    assert not session.has_unsaved_changes(NotchSettings())
    assert session.has_unsaved_changes(TUNED)


def test_saving_makes_the_runtime_the_baseline(path):
    session = config_store.ConfigSession(path)

    session.save(TUNED)

    assert session.source is ConfigSource.SAVED
    assert not session.has_unsaved_changes(TUNED)
    assert session.has_unsaved_changes(NotchSettings())  # reset is a runtime change


def test_reload_picks_up_a_file_written_elsewhere(path):
    session = config_store.ConfigSession(path)
    save_settings(TUNED, path)

    loaded = session.reload()

    assert loaded.settings == TUNED and session.baseline == TUNED


def test_an_invalid_file_has_no_baseline_and_reload_keeps_the_last_good_one(path):
    save_settings(TUNED, path)
    session = config_store.ConfigSession(path)
    write(path, "{broken")

    loaded = session.reload()

    assert loaded.source is ConfigSource.INVALID_FALLBACK and session.source is ConfigSource.INVALID_FALLBACK
    assert session.baseline == TUNED  # the runtime is still what was loaded
    assert config_store.ConfigSession(path).baseline is None
    assert config_store.ConfigSession(path).has_unsaved_changes(NotchSettings())


# --- Older versions ---------------------------------------------------------------


def version_3(settings: NotchSettings) -> dict:
    """``settings`` as the version-3 store wrote them: same keys, no floor."""
    data = settings_to_dict(settings)
    data["version"] = 3
    return data


def version_2(settings: NotchSettings) -> dict:
    """``settings`` exactly as the version-2 store wrote them: no content."""
    data = version_3(settings)
    data.pop("content")
    data["version"] = 2
    return data


def version_1(settings: NotchSettings) -> dict:
    """``settings`` exactly as the version-1 store wrote them."""
    data = version_2(settings)
    data.pop("behaviour")
    data["version"] = 1
    return data


def same_except_expanded_and_content(loaded: NotchVisualConfig, expected: NotchVisualConfig) -> bool:
    return (loaded.geometry.compact, loaded.geometry.top_margin, loaded.geometry.offset_x, loaded.eye_style,
            loaded.transition_duration_ms) == (
        expected.geometry.compact, expected.geometry.top_margin, expected.geometry.offset_x, expected.eye_style,
        expected.transition_duration_ms)


def test_a_missing_category_switch_takes_its_production_default(path):
    data = settings_to_dict(TUNED)
    data["behaviour"]["automatic_expansion"] = {"task": True}
    write(path, data)

    behaviour = load_settings(path).settings.behaviour

    assert behaviour.expands_for(NoticeCategory.TASK)
    for category in NoticeCategory:
        if category is not NoticeCategory.TASK:
            assert behaviour.expands_for(category) == NotchBehaviour().expands_for(category), category


OLDER = {1: version_1, 2: version_2, 3: version_3}

# What the user's own tall-compact file looked like: only 25 px revealed
# below the eyes, which made the old content tiny.
TALL_COMPACT = NotchSettings(visual=NotchVisualConfig(
    geometry=NotchGeometry(NotchSize(240, 75, 25), NotchSize(500, 100, 31), top_margin=2.0, offset_x=2.0),
    eye_style=NotchEyeStyle(height_share=0.25, spacing=290.0, motion_gain=0.8),
    content_style=NotchContentStyle(symbol_px=14.0, text_px=12.0, spacing_px=6.0, eye_gap_px=0.0),
))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_older_file_loads_readable_and_untouched(path, version):
    write(path, OLDER[version](TUNED))
    before = path.read_bytes()

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.SAVED and loaded.migrated_from == version
    assert loaded.diagnostic is None
    assert same_except_expanded_and_content(loaded.settings.visual, TUNED.visual)
    content = loaded.settings.visual.content_style
    assert content.symbol_px >= MIN_SYMBOL_PX and content.text_px >= MIN_TEXT_PX
    assert config_store.visual_config_problems(loaded.settings.visual) == []
    # Loading never writes: the file stays as it was until Save.
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", [1, 2, 3])
def test_a_cramped_older_file_grows_only_its_expanded_notch(version):
    data = OLDER[version](TALL_COMPACT)
    if version == 3:
        data["content"].update(symbol_px=8.5, text_px=6.12, spacing_px=4.25, eye_gap_px=9.6)

    visual = settings_from_dict(data).visual

    assert visual.geometry.compact == TALL_COMPACT.visual.geometry.compact
    assert visual.geometry.expanded.width == 500.0
    if version < 3:
        # The production content does not fit 25 px: only the expanded notch grows.
        assert visual.geometry.expanded.height > 100.0
    else:
        # Lifted to the floor it fits as it is; nothing grows needlessly.
        assert visual.geometry.expanded.height >= 100.0
    assert visual.content_style.symbol_px >= MIN_SYMBOL_PX and visual.content_style.text_px >= MIN_TEXT_PX
    assert config_store.visual_config_problems(visual) == []
    row = content_row_for(visual)
    assert row.area.height >= row.label_px  # a full line of readable text, not 25 px of squeeze


def test_versions_1_and_2_get_the_production_content(path):
    for version in (1, 2):
        assert settings_from_dict(OLDER[version](TUNED)).visual.content_style == NotchContentStyle()


def test_a_version_3_style_is_lifted_but_never_shrunk():
    data = version_3(TUNED)
    data["content"].update(symbol_px=9.0, text_px=30.0, spacing_px=5.0, eye_gap_px=3.0)

    content = settings_from_dict(data).visual.content_style

    assert (content.symbol_px, content.text_px, content.spacing_px, content.eye_gap_px) == (MIN_SYMBOL_PX, 30.0, 5.0, 3.0)


def test_a_version_1_file_loads_with_the_production_behaviour(path):
    write(path, version_1(TUNED))

    loaded = load_settings(path)

    assert loaded.settings.behaviour == NotchBehaviour()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_older_file_is_upgraded_only_by_saving(path, version):
    write(path, OLDER[version](TUNED))
    session = ConfigSession(path)

    assert not session.has_unsaved_changes(session.startup_settings)
    session.save(session.startup_settings)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == SCHEMA_VERSION
    if version == 1:
        assert data["behaviour"] == settings_to_dict(NotchSettings())["behaviour"]
    reloaded = load_settings(path)
    assert reloaded.migrated_from is None
    assert reloaded.settings == session.startup_settings


def test_a_current_file_reports_no_migration(path):
    save_settings(TUNED, path)

    assert load_settings(path).migrated_from is None


def test_an_invalid_version_1_file_still_falls_back(path):
    data = version_1(NotchSettings())
    data["eyes"]["height_share"] = 0.5
    write(path, data)

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.INVALID_FALLBACK
    assert loaded.migrated_from is None


def test_content_that_does_not_fit_is_a_visually_invalid_file(path):
    data = settings_to_dict(NotchSettings())
    data["content"]["eye_gap_px"] = 150.0
    write(path, data)

    loaded = load_settings(path)

    assert loaded.source is ConfigSource.INVALID_FALLBACK
    assert "content" in loaded.diagnostic
