"""Persisted listening preferences: how listening is activated.

Kept apart from the notch's file because they belong to the runtime, not to
one surface: a runtime started by `MIRA` reads them without ever building a
notch. ``$XDG_CONFIG_HOME/mira/listening.json`` (``~/.config/mira/``)::

    {"version": 1, "activation": "keyboard",
     "in_window_key": "M", "global_shortcut": "Ctrl+Alt+M"}

``activation`` is a `ListeningActivationMode`. Only ``"keyboard"`` works
today; a file asking for ``"wake word"`` loads with keyboard activation and
a diagnostic, so nothing silently does nothing. ``global_shortcut`` may be
``null`` to turn the desktop-wide shortcut off; when set it needs Ctrl, Alt
or Super. ``in_window_key`` is one plain key, never typed into text fields.

Like the notch configuration, loading never fails and never writes, and
saving is atomic (`mira.ui.notch.config_store.write_atomically`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from mira.domain.listening import IMPLEMENTED_MODES, ListeningActivationMode
from mira.ui.desktop.global_shortcut import ShortcutError, parse_shortcut
from mira.ui.notch.config_store import write_atomically

VERSION = 1
DEFAULT_IN_WINDOW_KEY = "M"
DEFAULT_GLOBAL_SHORTCUT = "Ctrl+Alt+M"


class ListeningConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ListeningSettings:
    activation: ListeningActivationMode = ListeningActivationMode.KEYBOARD
    in_window_key: str = DEFAULT_IN_WINDOW_KEY
    global_shortcut: str | None = DEFAULT_GLOBAL_SHORTCUT

    def __post_init__(self):
        if self.activation not in IMPLEMENTED_MODES:
            raise ListeningConfigError(f"{self.activation.value} activation is not available yet")
        key = self.in_window_key
        if not (isinstance(key, str) and len(key) == 1 and key.isalnum()):
            raise ListeningConfigError(f"the in-window key must be one letter or digit, got {key!r}")
        if self.global_shortcut is not None:
            try:
                parse_shortcut(self.global_shortcut)
            except ShortcutError as error:
                raise ListeningConfigError(str(error)) from error


def default_path() -> Path:
    base = (os.getenv("XDG_CONFIG_HOME") or "").strip()
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return root / "mira" / "listening.json"


def to_dict(settings: ListeningSettings) -> dict:
    return {
        "version": VERSION,
        "activation": settings.activation.value,
        "in_window_key": settings.in_window_key,
        "global_shortcut": settings.global_shortcut,
    }


def from_dict(data) -> tuple[ListeningSettings, str | None]:
    """The settings in ``data`` and a diagnostic for anything not honoured."""

    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ListeningConfigError("not a version 1 listening configuration")
    unknown = set(data) - {"version", "activation", "in_window_key", "global_shortcut"}
    if unknown:
        raise ListeningConfigError(f"unknown {', '.join(sorted(unknown))}")
    try:
        mode = ListeningActivationMode(data.get("activation", ListeningActivationMode.KEYBOARD.value))
    except ValueError as error:
        raise ListeningConfigError(str(error)) from error
    diagnostic = None
    if mode not in IMPLEMENTED_MODES:
        diagnostic = f"{mode.value} activation is not available yet; using keyboard activation"
        mode = ListeningActivationMode.KEYBOARD
    shortcut = data.get("global_shortcut", DEFAULT_GLOBAL_SHORTCUT)
    if shortcut is not None and not isinstance(shortcut, str):
        raise ListeningConfigError("global_shortcut must be a string or null")
    try:
        return ListeningSettings(mode, data.get("in_window_key", DEFAULT_IN_WINDOW_KEY), shortcut), diagnostic
    except ListeningConfigError:
        raise
    except (TypeError, ValueError) as error:
        raise ListeningConfigError(str(error)) from error


class ListeningSession:
    """The file behind the listening settings, as `ConfigSession` is for the notch."""

    def __init__(self, path: Path | None = None):
        self.path = default_path() if path is None else path
        self.settings = ListeningSettings()
        self.diagnostic: str | None = None
        self.reload()

    def reload(self) -> ListeningSettings:
        self.diagnostic = None
        if not self.path.exists():
            self.settings = ListeningSettings()
            return self.settings
        try:
            self.settings, self.diagnostic = from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ListeningConfigError) as error:
            self.settings = ListeningSettings()
            self.diagnostic = f"{self.path}: ignored, using the defaults ({error})"
        return self.settings

    def save(self, settings: ListeningSettings) -> Path:
        write_atomically(self.path, json.dumps(to_dict(settings), indent=2) + "\n")
        self.settings = settings
        return self.path
