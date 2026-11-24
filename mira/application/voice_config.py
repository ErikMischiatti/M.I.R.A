"""Voice configuration read from the environment at composition time."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from mira.domain.vocalization import DEFAULT_ENGINE, VoiceEngine, VoiceIdentity


VOICE_ENABLED_ENV = "MIRA_VOICE"
VOICE_VOLUME_ENV = "MIRA_VOICE_VOLUME"
VOICE_PITCH_ENV = "MIRA_VOICE_PITCH"
VOICE_RATE_ENV = "MIRA_VOICE_RATE"
VOICE_BRIGHTNESS_ENV = "MIRA_VOICE_BRIGHTNESS"
VOICE_ENGINE_ENV = "MIRA_VOICE_ENGINE"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class VoiceConfiguration:
    """Whether MIRA vocalizes, with what character and which synthesizer."""

    enabled: bool = False
    identity: VoiceIdentity = VoiceIdentity()
    engine: VoiceEngine = DEFAULT_ENGINE


def voice_configuration_from_environment() -> VoiceConfiguration:
    """Read the `MIRA_VOICE*` variables once, at composition time."""

    return parse_voice_configuration(
        enabled=os.getenv(VOICE_ENABLED_ENV),
        volume=os.getenv(VOICE_VOLUME_ENV),
        pitch=os.getenv(VOICE_PITCH_ENV),
        rate=os.getenv(VOICE_RATE_ENV),
        brightness=os.getenv(VOICE_BRIGHTNESS_ENV),
        engine=os.getenv(VOICE_ENGINE_ENV),
    )


def parse_voice_configuration(
    *,
    enabled: str | None = None,
    volume: str | None = None,
    pitch: str | None = None,
    rate: str | None = None,
    brightness: str | None = None,
    engine: str | None = None,
) -> VoiceConfiguration:
    """Parse raw values; absent or invalid ones fall back to their defaults.

    The voice is opt-in: it is used only when ``enabled`` is 1, true, yes or on
    (case-insensitive). Numbers outside the identity's ranges are clamped by
    `VoiceIdentity.bounded`. ``engine`` is ``v1`` (the default) or ``v2``,
    case-insensitive; anything else keeps V1.
    """

    default = VoiceIdentity()
    identity = VoiceIdentity(
        base_pitch_hz=_float_or(pitch, default.base_pitch_hz),
        speaking_rate=_float_or(rate, default.speaking_rate),
        brightness=_float_or(brightness, default.brightness),
        volume=_float_or(volume, default.volume),
    ).bounded()
    return VoiceConfiguration(
        enabled=(enabled or "").strip().lower() in _TRUE_VALUES,
        identity=identity,
        engine=_engine_or_default(engine),
    )


def _engine_or_default(raw: str | None) -> VoiceEngine:
    try:
        return VoiceEngine((raw or "").strip().lower())
    except ValueError:
        return DEFAULT_ENGINE


def _float_or(raw: str | None, default: float) -> float:
    try:
        value = float(raw.strip()) if raw is not None else default
    except ValueError:
        return default
    return value if math.isfinite(value) else default
