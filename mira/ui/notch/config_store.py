"""Qt-free persistence of the notch's tuned settings.

Three kinds of settings are kept apart:

- production defaults: `NotchSettings()`, defined in code and never changed
  by saving;
- saved settings: the user's file, by default ``$XDG_CONFIG_HOME/mira/notch.json``
  (``~/.config/mira/notch.json``), or the path in ``MIRA_NOTCH_CONFIG``;
- runtime settings: what a running notch uses, which tools may change
  without saving.

Loading never fails and never writes. A missing file yields the defaults. A
file that cannot be read, is not valid JSON, does not match the schema, or
describes a configuration whose eye motion would not fit the notch yields
the defaults plus a diagnostic, and is left untouched for the user to fix or
overwrite deliberately with Save.

Saving validates first and writes atomically: the settings go to a temporary
file in the same directory, are flushed to disk, and replace the old file in
one rename, so a crash leaves either the old file or the new one.

Schema, version 4 (every key required, no others allowed, except as noted)::

    {
      "version": 4,
      "compact":   {"width": 200.0, "height": 32.0, "radius": 14.0},
      "expanded":  {"width": 400.0, "height": 112.0, "radius": 26.0},
      "placement": {"offset_x": 0.0, "top_margin": 0.0},
      "eyes": {"height_share": 0.28, "top_offset": 0.0, "center_offset": 0.0,
               "spacing": 300.0, "motion_gain": 0.5},
      "animation": {"duration_ms": 200},
      "display": {"monitor": null},
      "behaviour": {"expand_on_hover": true,
                    "automatic_expansion": {"alert": true, "device": true, "task": false,
                                            "connectivity": true},
                    "notice_duration_ms": 4000},
      "content": {"symbol_px": 28.0, "text_px": 20.0, "spacing_px": 12.0,
                  "eye_gap_px": 22.0}
    }

``content`` is the expanded content's `NotchContentStyle`: symbol side,
label size, symbol–text spacing and the gap below the eyes' motion envelope,
all in pixels.

``behaviour`` holds the interaction preferences (`NotchBehaviour`).
``automatic_expansion`` has one switch per `NoticeCategory`: whether a notice
of that category expands the notch by itself. Categories, not individual
events, so a new kind of event normally needs no new key. A category
missing from the file takes its production default, so adding a category
later needs no new schema version; an unknown category is still an error.

Version 4 is version 3 with the readable floor (symbol ≥ 14 px, text ≥
12 px). Older versions still load, migrated in memory, reported as
``migrated_from``, and left untouched on disk until Save rewrites them as
version 4. Each is made readable without tuning: its content is lifted to
the floor (version 3) or gets the production content style (versions 1 and
2, which had none), and its *expanded* notch grows just enough to fit it
(`fit_expanded_to_content`); the compact notch and the eyes never change.
Version 1 also lacks ``behaviour``, which gets the production defaults.
``display.monitor`` is null for automatic selection, or the preferred
monitor's identity: ``{"name": ..., "manufacturer": ..., "model": ...,
"serial": ...}``.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from mira.domain.motion_envelope import PoseEnvelope
from mira.ui.notch.eye_layout import NotchEyeStyle, production_envelope
from mira.ui.notch.expanded_layout import NotchContentStyle, lifted_to_readable
from mira.domain.notice import NoticeCategory
from mira.ui.notch.presence import NotchGeometry, NotchSize
from mira.ui.notch.presentation import NotchBehaviour
from mira.ui.notch.screen_selection import MonitorIdentity, MonitorPreference
from mira.ui.notch.visual_config import (
    NotchVisualConfig,
    fit_expanded_to_content,
    validate_visual_config,
    visual_config_problems,
)


CONFIG_PATH_ENV = "MIRA_NOTCH_CONFIG"
SCHEMA_VERSION = 4
# Older versions this store still reads, migrating them in memory.
MIGRATABLE_VERSIONS = frozenset({1, 2, 3})


@dataclass(frozen=True, slots=True)
class NotchSettings:
    """Everything persisted about the notch; the defaults are production."""

    visual: NotchVisualConfig = field(default_factory=NotchVisualConfig)
    monitor: MonitorPreference = field(default_factory=MonitorPreference)
    behaviour: NotchBehaviour = field(default_factory=NotchBehaviour)


class ConfigSource(StrEnum):
    """Where a loaded configuration came from."""

    SAVED = "saved"
    DEFAULTS = "defaults"
    INVALID_FALLBACK = "invalid fallback"


@dataclass(frozen=True, slots=True)
class LoadedSettings:
    settings: NotchSettings
    source: ConfigSource
    path: Path
    diagnostic: str | None = None
    # The schema version the file was written in, when older than the
    # current one and migrated on load; None otherwise.
    migrated_from: int | None = None


class ConfigError(ValueError):
    """A configuration file that does not match the schema."""


def default_config_path() -> Path:
    """``MIRA_NOTCH_CONFIG``, else ``$XDG_CONFIG_HOME/mira/notch.json``."""

    return config_path_from(
        explicit=os.getenv(CONFIG_PATH_ENV),
        xdg_config_home=os.getenv("XDG_CONFIG_HOME"),
        home=Path.home(),
    )


def config_path_from(*, explicit: str | None, xdg_config_home: str | None, home: Path) -> Path:
    """Resolve the configuration path from raw environment values.

    A non-blank ``explicit`` path wins (``~`` is expanded). Otherwise the XDG
    base directory is used, ignoring a relative value as the specification
    requires, with ``~/.config`` as its default.
    """

    if explicit and explicit.strip():
        return Path(explicit.strip()).expanduser()
    base = (xdg_config_home or "").strip()
    root = Path(base) if base and Path(base).is_absolute() else home / ".config"
    return root / "mira" / "notch.json"


# --- Serialization -------------------------------------------------------------


def settings_to_dict(settings: NotchSettings) -> dict[str, Any]:
    geometry, eyes = settings.visual.geometry, settings.visual.eye_style
    content = settings.visual.content_style
    monitor = settings.monitor.monitor
    return {
        "version": SCHEMA_VERSION,
        "compact": _size_to_dict(geometry.compact),
        "expanded": _size_to_dict(geometry.expanded),
        "placement": {"offset_x": geometry.offset_x, "top_margin": geometry.top_margin},
        "eyes": {
            "height_share": eyes.height_share,
            "top_offset": eyes.top_offset,
            "center_offset": eyes.center_offset,
            "spacing": eyes.spacing,
            "motion_gain": eyes.motion_gain,
        },
        "animation": {"duration_ms": settings.visual.transition_duration_ms},
        "display": {
            "monitor": None
            if monitor is None
            else {
                "name": monitor.name,
                "manufacturer": monitor.manufacturer,
                "model": monitor.model,
                "serial": monitor.serial,
            }
        },
        "behaviour": {
            "expand_on_hover": settings.behaviour.expand_on_hover,
            "automatic_expansion": {
                category.value: settings.behaviour.expands_for(category) for category in NoticeCategory
            },
            "notice_duration_ms": settings.behaviour.notice_duration_ms,
        },
        "content": {
            "symbol_px": content.symbol_px,
            "text_px": content.text_px,
            "spacing_px": content.spacing_px,
            "eye_gap_px": content.eye_gap_px,
        },
    }


def _size_to_dict(size: NotchSize) -> dict[str, float]:
    return {"width": size.width, "height": size.height, "radius": size.corner_radius}


def schema_version(data: Any) -> int:
    """The file's declared schema version; `ConfigError` if unsupported."""

    if not isinstance(data, dict):
        raise ConfigError("configuration must be an object")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or (
        version != SCHEMA_VERSION and version not in MIGRATABLE_VERSIONS
    ):
        raise ConfigError(f"unsupported version {version!r}; expected {SCHEMA_VERSION}")
    return version


def settings_from_dict(data: Any, envelope: PoseEnvelope | None = None) -> NotchSettings:
    """Parse schema version 3, or migrate 1 or 2; `ConfigError` otherwise.

    ``envelope`` places a migrated file's legacy content below the eyes it
    describes, by default the packaged profiles' envelope.

    Value constraints (positive sizes, a radius that fits, the expanded notch
    containing the compact one, …) are reported as `ConfigError` too; the eye
    fit rule is checked separately, by `visual_config_problems`.
    """

    version = schema_version(data)
    keys = {"version", "compact", "expanded", "placement", "eyes", "animation", "display"}
    if version >= 2:
        keys.add("behaviour")
    if version >= 3:
        keys.add("content")
    root = _section(data, "configuration", keys)

    compact = _section(root["compact"], "compact", {"width", "height", "radius"})
    expanded = _section(root["expanded"], "expanded", {"width", "height", "radius"})
    placement = _section(root["placement"], "placement", {"offset_x", "top_margin"})
    eyes = _section(root["eyes"], "eyes", {"height_share", "top_offset", "center_offset", "spacing", "motion_gain"})
    animation = _section(root["animation"], "animation", {"duration_ms"})
    display = _section(root["display"], "display", {"monitor"})

    try:
        visual = NotchVisualConfig(
            geometry=NotchGeometry(
                compact=_size(compact, "compact"),
                expanded=_size(expanded, "expanded"),
                offset_x=_number(placement, "offset_x", "placement"),
                top_margin=_number(placement, "top_margin", "placement"),
            ),
            eye_style=NotchEyeStyle(
                height_share=_number(eyes, "height_share", "eyes"),
                top_offset=_number(eyes, "top_offset", "eyes"),
                center_offset=_number(eyes, "center_offset", "eyes"),
                spacing=_number(eyes, "spacing", "eyes"),
                motion_gain=_number(eyes, "motion_gain", "eyes"),
            ),
            transition_duration_ms=_integer(animation, "duration_ms", "animation"),
        )
    except ConfigError:
        raise
    except ValueError as error:
        raise ConfigError(str(error)) from error
    if version >= 4:
        content = _content(root["content"])
    elif version == 3:
        values = _section(root["content"], "content", {"symbol_px", "text_px", "spacing_px", "eye_gap_px"})
        try:
            content = lifted_to_readable(**{key: _number(values, key, "content") for key in values})
        except ValueError as error:
            raise ConfigError(str(error)) from error
    else:
        content = NotchContentStyle()
    visual = NotchVisualConfig(visual.geometry, visual.eye_style, visual.transition_duration_ms, content)
    if version < 4:
        # Readable without tuning: an older file's content is lifted to the
        # readable floor and its expanded notch grows to fit it.
        visual = fit_expanded_to_content(visual, envelope)
    behaviour = NotchBehaviour() if version < 2 else _behaviour(root["behaviour"])
    return NotchSettings(visual=visual, monitor=_monitor(display["monitor"]), behaviour=behaviour)


def _content(value: Any) -> NotchContentStyle:
    section = _section(value, "content", {"symbol_px", "text_px", "spacing_px", "eye_gap_px"})
    try:
        return NotchContentStyle(**{key: _number(section, key, "content") for key in section})
    except ValueError as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(str(error)) from error


def _behaviour(value: Any) -> NotchBehaviour:
    section = _section(value, "behaviour", {"expand_on_hover", "automatic_expansion", "notice_duration_ms"})
    switches = section["automatic_expansion"]
    if not isinstance(switches, dict):
        raise ConfigError("behaviour.automatic_expansion must be an object")
    unknown = switches.keys() - {c.value for c in NoticeCategory}
    if unknown:
        raise ConfigError(f"behaviour.automatic_expansion has unknown {', '.join(sorted(unknown))}")
    # A category the file predates takes its production default.
    defaults = NotchBehaviour().automatic_expansion
    switches = {c.value: switches.get(c.value, c in defaults) for c in NoticeCategory}
    for key, switch in [("expand_on_hover", section["expand_on_hover"]), *switches.items()]:
        if not isinstance(switch, bool):
            raise ConfigError(f"behaviour {key} must be true or false, got {switch!r}")
    try:
        return NotchBehaviour(
            expand_on_hover=section["expand_on_hover"],
            automatic_expansion=frozenset(NoticeCategory(key) for key, on in switches.items() if on),
            notice_duration_ms=_integer(section, "notice_duration_ms", "behaviour"),
        )
    except ValueError as error:
        raise ConfigError(str(error)) from error


def _section(value: Any, name: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be an object")
    missing, unknown = keys - value.keys(), value.keys() - keys
    if missing:
        raise ConfigError(f"{name} is missing {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigError(f"{name} has unknown {', '.join(sorted(unknown))}")
    return value


def _number(section: dict[str, Any], key: str, name: str) -> float:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigError(f"{name}.{key} must be a finite number, got {value!r}")
    return float(value)


def _integer(section: dict[str, Any], key: str, name: str) -> int:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name}.{key} must be a whole number, got {value!r}")
    return value


def _size(section: dict[str, Any], name: str) -> NotchSize:
    return NotchSize(
        width=_number(section, "width", name),
        height=_number(section, "height", name),
        corner_radius=_number(section, "radius", name),
    )


def _monitor(value: Any) -> MonitorPreference:
    if value is None:
        return MonitorPreference()
    monitor = _section(value, "display.monitor", {"name", "manufacturer", "model", "serial"})
    fields = {}
    for key in ("name", "manufacturer", "model", "serial"):
        if not isinstance(monitor[key], str):
            raise ConfigError(f"display.monitor.{key} must be a string")
        fields[key] = monitor[key]
    if not fields["name"]:
        raise ConfigError("display.monitor.name must not be empty")
    return MonitorPreference(MonitorIdentity(**fields))


# --- Files -------------------------------------------------------------------------


def load_settings(path: Path | None = None, envelope: PoseEnvelope | None = None) -> LoadedSettings:
    """The saved settings, or the defaults with a reason; never raises.

    ``envelope`` is the motion envelope the eye fit is checked against, by
    default that of the packaged expression profiles.
    """

    path = default_config_path() if path is None else path
    if not path.exists():
        return LoadedSettings(NotchSettings(), ConfigSource.DEFAULTS, path)

    def fallback(reason: str) -> LoadedSettings:
        return LoadedSettings(NotchSettings(), ConfigSource.INVALID_FALLBACK, path, f"{path}: {reason}")

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return fallback(f"cannot be read ({error})")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return fallback(f"is not valid JSON ({error})")
    try:
        settings = settings_from_dict(data, envelope)
    except ConfigError as error:
        return fallback(f"does not match the notch configuration schema ({error})")
    problems = visual_config_problems(settings.visual, envelope)
    if problems:
        return fallback(f"describes an invalid visual configuration ({'; '.join(problems)})")
    version = data["version"]
    return LoadedSettings(settings, ConfigSource.SAVED, path, migrated_from=None if version == SCHEMA_VERSION else version)


def save_settings(settings: NotchSettings, path: Path | None = None, envelope: PoseEnvelope | None = None) -> Path:
    """Validate ``settings`` and replace the file at ``path`` atomically.

    Raises `InvalidVisualConfig` without touching the file if the eyes would
    not fit, and `OSError` if the file cannot be written; on any failure the
    previous file is left as it was.
    """

    path = default_config_path() if path is None else path
    validate_visual_config(settings.visual, envelope)
    write_atomically(path, json.dumps(settings_to_dict(settings), indent=2) + "\n")
    return path


def write_atomically(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` in one rename, or leave it as it was."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    _sync_directory(path.parent)


def _sync_directory(directory: Path) -> None:
    # Makes the rename itself durable on POSIX file systems; best effort.
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


# --- Tuning session -----------------------------------------------------------------


class ConfigSession:
    """What a tuning tool needs to know about the file behind its settings.

    The session tracks the last configuration known to be on disk — loaded
    or saved — as the baseline that runtime changes are compared with. An
    invalid file has no valid content, so it has no baseline and every
    runtime state counts as unsaved until Save replaces the file.

    - `reload` reads the file again. Saved or missing contents become the new
      baseline; an invalid file is reported and changes nothing else, so the
      caller can keep its runtime settings.
    - `save` validates and writes; the written settings become the baseline.
    - Resetting to defaults is a runtime change only: nothing is written.
    """

    def __init__(self, path: Path | None = None, envelope: PoseEnvelope | None = None):
        self.path = default_config_path() if path is None else path
        self.envelope = envelope
        self.last_load = load_settings(self.path, envelope)
        self._baseline: NotchSettings | None = None
        self._adopt(self.last_load)

    @property
    def startup_settings(self) -> NotchSettings:
        """The settings to start from: saved, or the defaults."""
        return self.last_load.settings

    @property
    def source(self) -> ConfigSource:
        """Where the last load or save found the file."""
        return self.last_load.source

    @property
    def baseline(self) -> NotchSettings | None:
        return self._baseline

    @property
    def diagnostic(self) -> str | None:
        return self.last_load.diagnostic

    def has_unsaved_changes(self, runtime: NotchSettings) -> bool:
        return self._baseline is None or runtime != self._baseline

    def reload(self) -> LoadedSettings:
        loaded = load_settings(self.path, self.envelope)
        self.last_load = loaded
        if loaded.source is not ConfigSource.INVALID_FALLBACK:
            self._adopt(loaded)
        return loaded

    def save(self, settings: NotchSettings) -> Path:
        path = save_settings(settings, self.path, self.envelope)
        self.last_load = LoadedSettings(settings, ConfigSource.SAVED, path)
        self._adopt(self.last_load)
        return path

    def _adopt(self, loaded: LoadedSettings) -> None:
        self._baseline = None if loaded.source is ConfigSource.INVALID_FALLBACK else loaded.settings
