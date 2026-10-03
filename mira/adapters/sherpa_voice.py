"""Local wake-word spotting and voice activity detection through sherpa-onnx.

Why sherpa-onnx (the optional ``voice`` extra): it runs entirely offline,
is Apache-2.0 with Apache-2.0 models, ships its own ONNX runtime (no torch,
no scipy), has Linux x86-64 and ARM wheels, and provides both pieces MIRA
needs from one engine:

- an **open-vocabulary keyword spotter** (a small streaming zipformer; the
  English gigaspeech model has 3.3 M parameters): the wake phrase is
  *text*, turned into the model's BPE tokens with sentencepiece at start,
  so any name works without training;
- the **Silero voice activity detector** (ONNX), deciding per 512-sample
  window (32 ms at 16 kHz) whether speech is present.

The models are not on PyPI. They live in ``$XDG_DATA_HOME/mira/models``
(``~/.local/share/mira/models``): the keyword spotter's directory (with
``tokens.txt``, ``bpe.model`` and the encoder, decoder and joiner ONNX
files) and ``silero_vad.onnx``. `load_voice_engines` reports, never raises,
when the package or a model is missing; MIRA then uses keyboard activation
and its built-in energy detector.

Both adapters are used from `VoiceProcessor`'s worker, one call at a time,
and keep their streaming state between calls. Audio stays in memory.
"""

from __future__ import annotations

import os
from array import array
from dataclasses import dataclass
from pathlib import Path

from mira.domain.audio import FULL_SCALE, AudioFrame
from mira.domain.voice import SpeechWindow, WakeWordConfig, WakeWordDetection

SAMPLE_RATE = 16000
VAD_WINDOW = 512
KWS_PREFIX = "sherpa-onnx-kws-zipformer"


def models_directory(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    base = (environ.get("XDG_DATA_HOME") or "").strip()
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local" / "share"
    return root / "mira" / "models"


@dataclass(frozen=True, slots=True)
class KeywordSpotterModel:
    directory: Path
    tokens: Path
    bpe: Path
    encoder: Path
    decoder: Path
    joiner: Path


def _pick(directory: Path, part: str) -> Path | None:
    candidates = sorted(directory.glob(f"{part}*.onnx"))
    # The int8 models are smaller and faster on CPU; use them when present.
    int8 = [c for c in candidates if ".int8." in c.name]
    return (int8 or candidates or [None])[0]


def find_keyword_spotter(models: Path) -> KeywordSpotterModel | None:
    for directory in sorted(models.glob(f"{KWS_PREFIX}*")):
        if not directory.is_dir():
            continue
        parts = {part: _pick(directory, part) for part in ("encoder", "decoder", "joiner")}
        tokens, bpe = directory / "tokens.txt", directory / "bpe.model"
        if all(parts.values()) and tokens.is_file() and bpe.is_file():
            return KeywordSpotterModel(directory, tokens, bpe, parts["encoder"], parts["decoder"], parts["joiner"])
    return None


def to_floats(frame: AudioFrame) -> list[float]:
    samples = array("h")
    samples.frombytes(frame.pcm)
    return [value / FULL_SCALE for value in samples]


def keyword_line(phrase: str, model: KeywordSpotterModel, sensitivity: float) -> str:
    """The spotter's keyword entry for ``phrase``: BPE tokens, boost, threshold, label.

    Higher sensitivity lowers the trigger threshold and boosts the phrase.
    ValueError if the phrase cannot be spelled in the model's tokens.
    """

    import sentencepiece

    processor = sentencepiece.SentencePieceProcessor()
    processor.load(str(model.bpe))
    pieces = processor.encode(phrase.upper(), out_type=str)
    known = {line.split()[0] for line in model.tokens.read_text(encoding="utf-8").splitlines() if line.strip()}
    missing = [piece for piece in pieces if piece not in known]
    if not pieces or missing:
        raise ValueError(f"{phrase!r} cannot be spelled with this model's tokens ({missing})")
    threshold = round(0.45 - 0.35 * sensitivity, 3)
    boost = round(1.0 + 2.0 * sensitivity, 3)
    label = phrase.replace(" ", "_")
    return f"{' '.join(pieces)} :{boost} #{threshold} @{label}"


class SherpaWakeWordDetector:
    """A `WakeWordDetector` for one configured phrase; see the module."""

    def __init__(self, config: WakeWordConfig, model: KeywordSpotterModel, *, threads: int = 1):
        import sherpa_onnx

        self.config = config
        self.model = model
        self.name = f"sherpa-onnx keyword spotter ({model.directory.name})"
        self.keywords = keyword_line(config.phrase, model, config.sensitivity)
        keywords_file = Path(os.environ.get("TMPDIR", "/tmp")) / f"mira-keywords-{os.getpid()}.txt"
        keywords_file.write_text(self.keywords + "\n", encoding="utf-8")
        try:
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(model.tokens), encoder=str(model.encoder), decoder=str(model.decoder),
                joiner=str(model.joiner), keywords_file=str(keywords_file), num_threads=threads,
                sample_rate=SAMPLE_RATE, max_active_paths=4, num_trailing_blanks=1,
            )
        finally:
            keywords_file.unlink(missing_ok=True)
        self._stream = self._spotter.create_stream()

    def reset(self) -> None:
        self._stream = self._spotter.create_stream()

    def process(self, frame: AudioFrame) -> list[WakeWordDetection]:
        self._stream.accept_waveform(SAMPLE_RATE, to_floats(frame))
        detections = []
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            if self._spotter.get_result(self._stream):
                detections.append(WakeWordDetection(self.config.phrase))
                # Required after a hit: the same audio must not fire again.
                self._spotter.reset_stream(self._stream)
        return detections


class SherpaSileroVad:
    """A `VoiceActivityDetector` on Silero, 512-sample windows; see the module.

    Uses the bare `VadModel` (one decision per window), not sherpa's
    `VoiceActivityDetector`, which keeps every sample of the current speech
    segment in a buffer that doubles as speech goes on: MIRA's session
    already keeps the one bounded copy it needs.
    """

    def __init__(self, model: Path, *, threshold: float = 0.5, threads: int = 1):
        import sherpa_onnx

        self.name = "Silero VAD (sherpa-onnx)"
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model)
        config.silero_vad.threshold = threshold
        # Its own segmentation is not used: MIRA's session decides onset and
        # end; these only keep the per-window decision responsive.
        config.silero_vad.min_silence_duration = 0.1
        config.silero_vad.min_speech_duration = 0.05
        config.silero_vad.window_size = VAD_WINDOW
        config.sample_rate = SAMPLE_RATE
        config.num_threads = threads
        self._model = sherpa_onnx.VadModel.create(config)
        self._pending: list[float] = []
        self._samples = 0

    def reset(self) -> None:
        self._model.reset()
        self._pending = []
        self._samples = 0

    def process(self, frame: AudioFrame) -> list[SpeechWindow]:
        self._pending += to_floats(frame)
        windows = []
        while len(self._pending) >= VAD_WINDOW:
            chunk, self._pending = self._pending[:VAD_WINDOW], self._pending[VAD_WINDOW:]
            self._samples += VAD_WINDOW
            windows.append(SpeechWindow(bool(self._model.is_speech(chunk)), self._samples, VAD_WINDOW))
        return windows


@dataclass(frozen=True, slots=True)
class VoiceEngines:
    detector: object | None
    vad: object | None
    detector_problem: str | None
    vad_problem: str | None
    models: Path


def load_voice_engines(config: WakeWordConfig, *, models: Path | None = None, want_detector: bool = True) -> VoiceEngines:
    """The engines that can run here, and why any cannot. Never raises."""

    models = models_directory() if models is None else models
    try:
        import sherpa_onnx  # noqa: F401
        import sentencepiece  # noqa: F401
    except ImportError as error:
        reason = f"the voice extra is not installed ({error.name})"
        return VoiceEngines(None, None, reason, reason, models)

    vad = vad_problem = None
    silero = models / "silero_vad.onnx"
    if silero.is_file():
        try:
            vad = SherpaSileroVad(silero)
        except Exception as error:
            vad_problem = f"Silero VAD could not load: {error}"
    else:
        vad_problem = f"no {silero}"

    detector = detector_problem = None
    if want_detector:
        model = find_keyword_spotter(models)
        if model is None:
            detector_problem = f"no keyword-spotter model in {models}"
        else:
            try:
                detector = SherpaWakeWordDetector(config, model)
            except Exception as error:
                detector_problem = f"the wake-word detector could not load: {error}"
    return VoiceEngines(detector, vad, detector_problem, vad_problem, models)
