"""Which voice engines the desktop runtime uses, chosen from the settings.

The engines are adapters (`mira.adapters.sherpa_voice`, the optional
``voice`` extra); this module picks what to load for the current settings
and hands it to the runtime's one `ListeningController`:

- the keyword spotter only in wake-word mode — it costs memory and a
  moment to load, and keyboard mode never needs it;
- the Silero detector when its model is installed, else the built-in
  energy detector, so speech is always segmented.

What could not be loaded is reported in `VoiceSetup`, for logs and the
settings; nothing here raises.
"""

from __future__ import annotations

from dataclasses import dataclass

from mira.core.listening import ListeningController
from mira.domain.listening import ListeningActivationMode
from mira.domain.voice import EnergyVoiceActivityDetector, SegmentationPolicy, WakeWordConfig


@dataclass(frozen=True, slots=True)
class VoiceSetup:
    mode: ListeningActivationMode
    detector: str | None
    vad: str
    detector_problem: str | None
    vad_problem: str | None


def wake_word_available() -> tuple[bool, str | None]:
    """Whether the engine and a keyword-spotter model are installed (cheap)."""

    try:
        from mira.adapters.sherpa_voice import find_keyword_spotter, models_directory
        import sherpa_onnx  # noqa: F401
        import sentencepiece  # noqa: F401
    except ImportError as error:
        return False, f"the voice extra is not installed ({error.name})"
    models = models_directory()
    if find_keyword_spotter(models) is None:
        return False, f"no keyword-spotter model in {models}"
    return True, None


def configure_listening(listening: ListeningController, mode: ListeningActivationMode, wake_word: WakeWordConfig,
                        policy: SegmentationPolicy, *, loader=None) -> VoiceSetup:
    if loader is None:
        from mira.adapters.sherpa_voice import load_voice_engines as loader
    engines = loader(wake_word, want_detector=mode is ListeningActivationMode.WAKE_WORD)
    vad = engines.vad if engines.vad is not None else EnergyVoiceActivityDetector()
    if type(vad) is type(listening.vad) and engines.vad is None:
        vad = None  # keep the running built-in detector; nothing changed
    listening.configure(mode, policy=policy, detector=engines.detector, vad=vad, replace_detector=True)
    return VoiceSetup(mode, getattr(engines.detector, "name", None), listening.vad.name,
                      engines.detector_problem if mode is ListeningActivationMode.WAKE_WORD else None,
                      engines.vad_problem)
