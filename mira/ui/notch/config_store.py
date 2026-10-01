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

Schema, version 1 (every key required, no others allowed)::

    {
      "version": 1,
      "compact":   {"width": 200.0, "height": 32.0, "radius": 14.0},
      "expanded":  {"width": 360.0, "height": 96.0, "radius": 24.0},
      "placement": {"offset_x": 0.0, "top_margin": 0.0},
      "eyes": {"height_share": 0.28, "top_offset": 0.0, "center_offset": 0.0,
               "spacing": 300.0, "motion_gain": 0.5},
      "animation": {"duration_ms": 200},
      "display": {"monitor": null}
    }

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
from mira.ui.notch.eye_layout import NotchEyeStyle
from mira.ui.notch.presence import NotchGeometry, NotchSize
from mira.ui.notch.screen_selection import MonitorIdentity, MonitorPreference
from mira.ui.notch.visual_config import NotchVisualConfig, validate_visual_config, visual_config_problems


CONFIG_PATH_ENV = "MIRA_NOTCH_CONFIG"
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class NotchSettings:
    """Everything persisted about the notch; the defaults are production."""

    visual: NotchVisualConfig = field(default_factory=NotchVisualConfig)
    monitor: MonitorPreference = field(default_factory=MonitorPreference)


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
    }


def _size_to_dict(size: NotchSize) -> dict[str, float]:
    return {"width": size.width, "height": size.height, "radius": size.corner_radius}


def settings_from_dict(data: Any) -> NotchSettings:
    """Parse schema version 1; raise `ConfigError` for anything else.

    Value constraints (positive sizes, a radius that fits, the expanded notch
    containing the compact one, …) are reported as `ConfigError` too; the eye
    fit rule is checked separately, by `visual_config_problems`.
    """

    root = _section(data, "configuration", {
        "version", "compact", "expanded", "placement", "eyes", "animation", "display",
    })
    version = root["version"]
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ConfigError(f"unsupported version {version!r}; expected {SCHEMA_VERSION}")

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
    return NotchSettings(visual=visual, monitor=_monitor(display["monitor"]))


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
        settings = settings_from_dict(data)
    except ConfigError as error:
        return fallback(f"does not match the notch configuration schema ({error})")
    problems = visual_config_problems(settings.visual, envelope)
    if problems:
        return fallback(f"describes an invalid visual configuration ({'; '.join(problems)})")
    return LoadedSettings(settings, ConfigSource.SAVED, path)


def save_settings(settings: NotchSettings, path: Path | None = None, envelope: PoseEnvelope | None = None) -> Path:
    """Validate ``settings`` and replace the file at ``path`` atomically.

    Raises `InvalidVisualConfig` without touching the file if the eyes would
    not fit, and `OSError` if the file cannot be written; on any failure the
    previous file is left as it was.
    """

    path = default_config_path() if path is None else path
    validate_visual_config(settings.visual, envelope)
    text = json.dumps(settings_to_dict(settings), indent=2) + "\n"

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
    return path


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
