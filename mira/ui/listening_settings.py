"""Persisted listening preferences: how listening starts and when speech ends.

Kept apart from the notch's file because they belong to the runtime, not to
one surface: a runtime started by `MIRA` reads them without ever building a
notch. ``$XDG_CONFIG_HOME/mira/listening.json`` (``~/.config/mira/``),
version 2::

    {"version": 2, "activation": "keyboard",
     "in_window_key": "M", "global_shortcut": "Ctrl+Alt+M",
     "wake_word": {"phrase": "mira", "sensitivity": 0.5},
     "speech_end_s": 0.8, "wait_timeout_s": 8.0}

- ``activation``: ``"keyboard"`` or ``"wake word"`` (a local keyword
  spotter; the keys still cancel). Wake word needs the optional voice
  engine and its model; where they are missing the runtime says so and
  keyboard activation keeps working.
- ``in_window_key``: one plain key, never typed into text fields;
  ``global_shortcut``: needs Ctrl, Alt or Super, or ``null`` for none.
- ``wake_word``: the phrase (text, so a new name needs no new code) and a
  0–1 sensitivity: higher wakes more readily, and falsely more often.
- ``speech_end_s`` (0.2–3): silence that ends an utterance; natural pauses
  between words are shorter. ``wait_timeout_s`` (2–30): how long a session
  waits for speech before ending quietly.

Version 1 (keys only) still loads, with these defaults for the rest; the
file is not rewritten until Save. Like the notch configuration, loading
never fails and never writes, and saving is atomic.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from mira.domain.listening import IMPLEMENTED_MODES, ListeningActivationMode
from mira.domain.voice import SegmentationPolicy, WakeWordConfig
from mira.ui.desktop.global_shortcut import ShortcutError, parse_shortcut
from mira.ui.notch.config_store import write_atomically

VERSION = 2
DEFAULT_IN_WINDOW_KEY = "M"
DEFAULT_GLOBAL_SHORTCUT = "Ctrl+Alt+M"
_DEFAULT_POLICY = SegmentationPolicy()


class ListeningConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ListeningSettings:
    activation: ListeningActivationMode = ListeningActivationMode.KEYBOARD
    in_window_key: str = DEFAULT_IN_WINDOW_KEY
    global_shortcut: str | None = DEFAULT_GLOBAL_SHORTCUT
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    speech_end_s: float = _DEFAULT_POLICY.end_silence_s
    wait_timeout_s: float = _DEFAULT_POLICY.wait_timeout_s

    def __post_init__(self):
        if self.activation not in IMPLEMENTED_MODES:
            raise ListeningConfigError(f"{self.activation.value} activation is not available")
        key = self.in_window_key
        if not (isinstance(key, str) and len(key) == 1 and key.isalnum()):
            raise ListeningConfigError(f"the in-window key must be one letter or digit, got {key!r}")
        if self.global_shortcut is not None:
            try:
                parse_shortcut(self.global_shortcut)
            except ShortcutError as error:
                raise ListeningConfigError(str(error)) from error
        try:
            self.policy
        except ValueError as error:
            raise ListeningConfigError(str(error)) from error

    @property
    def policy(self) -> SegmentationPolicy:
        return SegmentationPolicy(end_silence_s=self.speech_end_s, wait_timeout_s=self.wait_timeout_s,
                                  tail_s=min(_DEFAULT_POLICY.tail_s, self.speech_end_s))


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
        "wake_word": {"phrase": settings.wake_word.phrase, "sensitivity": settings.wake_word.sensitivity},
        "speech_end_s": settings.speech_end_s,
        "wait_timeout_s": settings.wait_timeout_s,
    }


_V1_KEYS = {"version", "activation", "in_window_key", "global_shortcut"}
_V2_KEYS = _V1_KEYS | {"wake_word", "speech_end_s", "wait_timeout_s"}


def _number(data: dict, key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ListeningConfigError(f"{key} must be a number, got {value!r}")
    return float(value)


def from_dict(data) -> tuple[ListeningSettings, str | None, int]:
    """The settings in ``data``, a diagnostic (or None), and the file's version."""

    if not isinstance(data, dict) or data.get("version") not in (1, VERSION):
        raise ListeningConfigError("not a version 1 or 2 listening configuration")
    version = data["version"]
    unknown = set(data) - (_V1_KEYS if version == 1 else _V2_KEYS)
    if unknown:
        raise ListeningConfigError(f"unknown {', '.join(sorted(unknown))}")
    try:
        mode = ListeningActivationMode(data.get("activation", ListeningActivationMode.KEYBOARD.value))
    except ValueError as error:
        raise ListeningConfigError(str(error)) from error
    shortcut = data.get("global_shortcut", DEFAULT_GLOBAL_SHORTCUT)
    if shortcut is not None and not isinstance(shortcut, str):
        raise ListeningConfigError("global_shortcut must be a string or null")
    wake = data.get("wake_word", {})
    if not isinstance(wake, dict) or set(wake) - {"phrase", "sensitivity"}:
        raise ListeningConfigError("wake_word must be {\"phrase\", \"sensitivity\"}")
    try:
        wake_word = WakeWordConfig(wake.get("phrase", WakeWordConfig.__dataclass_fields__["phrase"].default),
                                   _number(wake, "sensitivity", 0.5))
        settings = ListeningSettings(
            mode, data.get("in_window_key", DEFAULT_IN_WINDOW_KEY), shortcut, wake_word,
            _number(data, "speech_end_s", _DEFAULT_POLICY.end_silence_s),
            _number(data, "wait_timeout_s", _DEFAULT_POLICY.wait_timeout_s),
        )
    except ListeningConfigError:
        raise
    except (TypeError, ValueError) as error:
        raise ListeningConfigError(str(error)) from error
    return settings, None, version


class ListeningSession:
    """The file behind the listening settings, as `ConfigSession` is for the notch."""

    def __init__(self, path: Path | None = None):
        self.path = default_path() if path is None else path
        self.settings = ListeningSettings()
        self.diagnostic: str | None = None
        self.migrated_from: int | None = None
        self.reload()

    def reload(self) -> ListeningSettings:
        self.diagnostic = None
        self.migrated_from = None
        if not self.path.exists():
            self.settings = ListeningSettings()
            return self.settings
        try:
            self.settings, self.diagnostic, version = from_dict(json.loads(self.path.read_text(encoding="utf-8")))
            self.migrated_from = version if version != VERSION else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ListeningConfigError) as error:
            self.settings = ListeningSettings()
            self.diagnostic = f"{self.path}: ignored, using the defaults ({error})"
        return self.settings

    def save(self, settings: ListeningSettings) -> Path:
        write_atomically(self.path, json.dumps(to_dict(settings), indent=2) + "\n")
        self.settings = settings
        self.migrated_from = None
        return self.path
