"""Experimental V2 pseudo-speech engine, opt-in alongside the V1 default.

Selected with ``synthesize(..., engine=VoiceEngine.V2)`` (or ``MIRA_VOICE_ENGINE=v2``).
It keeps V1's contract — same `VocalizationRequest`, `VoiceIdentity` and
`PcmClip`, the same sample rate and ``MIN_DURATION_S..MAX_DURATION_S`` bound,
pure Python and the standard library, the same bytes for the same seed — and
changes only the sound design, ported from the "voice V2 lab" prototype:

- **Richer voiced source.** Up to ~28 harmonics under a gentle spectral tilt
  (≈ −1/k), shaped by three broad Gaussian pseudo-formants, instead of V1's
  narrow resonances over a steep tilt. The result is a soft buzzy vowel rather
  than a near-sine tone.
- **Vowel morphs inside every syllable.** Each syllable glides from one
  invented vowel colour to another (``oo → eh``), so a single syllable already
  moves the way babble does.
- **Curved pitch gestures.** Every syllable follows the intent's three-point
  contour with cosine-smoothed segments, plus a delayed, shallow vibrato and a
  small seeded offset; the utterance never steps between discrete notes.
- **Soft onsets.** Breath, glide, muted-plosive and nasal textures: a few
  milliseconds of windowed, smoothed noise mixed under the voicing, never a
  hard click.
- **Scripted rhythm.** Each intent is a fixed sequence of syllables and pauses,
  so it stays recognizable; the seed only nudges durations, gaps and pitch.

As in V1, the clip is peak-normalized below full scale, quieter intents stay
quieter, and both ends are faded.

**Presets** (experimental, for listening tests). A `PresetV2` holds every
sound-design constant: formant sharpness, spectral tilt, morph timing, onset
strength, rhythm jitter, pitch movement and a few optional articulations
(syllable splitting, phrase declination, accents). ``baseline`` is V2 exactly as
above; ``soft``, ``babble`` and ``expressive`` are candidate personalities,
selected with ``synthesize_v2(..., preset="babble")``. They are not engines and
production never selects one: `MIRA_VOICE_ENGINE=v2` always plays ``baseline``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from mira.domain.vocal_synthesis import (
    MAX_DURATION_S,
    MIN_DURATION_S,
    PEAK_HEADROOM,
    SAMPLE_RATE,
    _to_pcm16,
)
from mira.domain.vocalization import (
    PcmClip,
    VocalIntent,
    VocalizationRequest,
    VoiceIdentity,
)

# Silence before the first and after the last syllable, inside the duration bound.
TAIL_S = 0.025

_MIN_F0_HZ = 100.0
_MAX_F0_HZ = 900.0
_MAX_PARTIALS = 28
_BLOCK = 64
# Where, inside a syllable, the middle point of the intent's contour falls.
_CONTOUR_MID = 0.46
# A fixed phase lead per harmonic (sin(k·(θ + φ))), as in the lab prototype;
# it softens the waveform's initial crest without changing the spectrum.
_PHASE_LEAD = 0.025


# --- vowel colours ------------------------------------------------------

# Invented pseudo-vowels, three formant centres each (Hz). Broad Gaussian
# resonances keep the colours audible on small speakers.
VOWELS_V2: dict[str, tuple[float, float, float]] = {
    "uu": (430.0, 1020.0, 2240.0),
    "oo": (540.0, 1120.0, 2350.0),
    "ah": (860.0, 1480.0, 2660.0),
    "eh": (625.0, 1940.0, 2730.0),
    "ii": (380.0, 2320.0, 2940.0),
    "uh": (570.0, 1500.0, 2450.0),
}


class OnsetV2:
    """Soft consonant-like textures mixed under a syllable's first milliseconds."""

    BREATH = "h"        # airy hiss
    GLIDE = "y"         # a brief, lighter hiss
    SOFT_PLOSIVE = "b"  # muted, low-passed puff
    NASAL = "n"         # barely-there murmur
    HUM = "m"           # murmur, and a softer, rounder syllable


# (length s, strength relative to the syllable peak, raw-noise share)
_ONSETS: dict[str, tuple[float, float, float]] = {
    OnsetV2.BREATH: (0.030, 0.060, 0.4),
    OnsetV2.GLIDE: (0.014, 0.030, 0.4),
    OnsetV2.SOFT_PLOSIVE: (0.019, 0.060, 0.0),
    OnsetV2.NASAL: (0.023, 0.015, 0.0),
    OnsetV2.HUM: (0.018, 0.015, 0.0),
}
_HUM_LEVEL = 0.78
# Onsets a split syllable's second half may take: light, never a hard stop.
_SPLIT_ONSETS = (OnsetV2.SOFT_PLOSIVE, OnsetV2.NASAL, OnsetV2.GLIDE)


# --- presets ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PresetV2:
    """Every V2 sound-design constant. The defaults are the ``baseline`` voice.

    Optional articulations are off at their defaults and then draw nothing from
    the seeded generator, so ``baseline`` renders exactly what V2 always has.
    """

    name: str = "baseline"
    # Timbre.
    formant_widths_hz: tuple[float, float, float] = (340.0, 430.0, 570.0)
    formant_gains: tuple[float, float, float] = (0.90, 0.70, 0.32)
    resonance_floor: float = 0.07     # spectrum between formants; lower = clearer vowels
    tilt_base: float = 1.20           # spectral tilt is tilt_base − tilt_brightness·brightness
    tilt_brightness: float = 0.45
    taper_start_hz: float = 5800.0    # partials fade out between these, then stop
    taper_end_hz: float = 7000.0
    # Articulation.
    morph_start: float = 0.12         # where in the syllable the vowel glide begins
    morph_span: float = 0.76          # and how much of it the glide takes
    attack_s: float = 0.013
    release_s: float = 0.032
    onset_gain: float = 1.0           # multiplier on every onset texture's strength
    split_min_s: float = 0.0          # split syllables at least this long (0 = never)
    split_chance: float = 0.0         # probability a long-enough syllable is split
    split_gap_s: float = 0.018        # the short closure between the two halves
    # Rhythm.
    rate_scale: float = 1.0
    duration_jitter: tuple[float, float] = (0.94, 1.07)
    gap_jitter: tuple[float, float] = (0.87, 1.14)
    final_stretch: float = 1.0        # last syllable length multiplier
    # Pitch.
    movement_scale: float = 1.0       # multiplier on contour and offset semitones
    offset_jitter_st: float = 0.09    # seeded per-syllable pitch offset (std dev)
    declination_st: float = 0.0       # gentle fall across the phrase, per syllable position
    max_excursion_st: float = math.inf  # clamp on any pitch point, from the base
    vibrato_st: float = 0.085
    # Dynamics.
    accent_depth: float = 0.0         # inner syllables this much quieter than first and last


BASELINE = PresetV2()

PRESETS_V2: dict[str, PresetV2] = {
    "baseline": BASELINE,
    # Warm and rounded: darker tilt, broader formants, an earlier high-cut,
    # slower vowel glides, gentle envelopes, quiet onsets, smaller movement.
    "soft": PresetV2(
        name="soft",
        formant_widths_hz=(400.0, 500.0, 650.0), formant_gains=(0.95, 0.62, 0.22),
        tilt_base=1.42, taper_start_hz=4300.0, taper_end_hz=5400.0,
        morph_start=0.18, morph_span=0.70, attack_s=0.022, release_s=0.048,
        onset_gain=0.55, rate_scale=0.94, duration_jitter=(0.96, 1.06),
        gap_jitter=(0.9, 1.15), final_stretch=1.12,
        movement_scale=0.75, offset_jitter_st=0.07, declination_st=-0.6,
        max_excursion_st=7.0, vibrato_st=0.06,
    ),
    # Chattering pseudo-speech: sharper formants and a lower floor so vowels are
    # recognizable, quicker glides, firmer soft onsets, long syllables split in
    # two around a brief closure, and loose, varied rhythm and pitch.
    "babble": PresetV2(
        name="babble",
        formant_widths_hz=(250.0, 320.0, 440.0), formant_gains=(1.0, 0.85, 0.34),
        resonance_floor=0.045, tilt_base=1.24, taper_end_hz=6600.0, taper_start_hz=5400.0,
        morph_start=0.10, morph_span=0.58, attack_s=0.010, release_s=0.026,
        onset_gain=1.5, split_min_s=0.12, split_chance=0.75, split_gap_s=0.016,
        rate_scale=1.08, duration_jitter=(0.80, 1.22), gap_jitter=(0.6, 1.5),
        offset_jitter_st=0.4, declination_st=-1.0, max_excursion_st=8.0, vibrato_st=0.04,
        accent_depth=0.08,
    ),
    # Animated: wider, emphasized gestures under a hard excursion limit, a
    # stretched final syllable, livelier vibrato, accented phrase edges.
    "expressive": PresetV2(
        name="expressive",
        formant_widths_hz=(300.0, 380.0, 520.0), formant_gains=(0.95, 0.75, 0.30),
        resonance_floor=0.06, tilt_base=1.22, taper_end_hz=6600.0, taper_start_hz=5400.0,
        morph_start=0.10, morph_span=0.70, onset_gain=1.15,
        duration_jitter=(0.88, 1.14), gap_jitter=(0.75, 1.3), final_stretch=1.2,
        movement_scale=1.45, offset_jitter_st=0.15, declination_st=-0.8,
        max_excursion_st=9.0, vibrato_st=0.11, accent_depth=0.14,
    ),
}


def preset_v2(preset: str | PresetV2) -> PresetV2:
    """The preset named ``preset`` (or ``preset`` itself). Raises on an unknown name."""
    if isinstance(preset, PresetV2):
        return preset
    try:
        return PRESETS_V2[preset.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown V2 preset {preset!r}; one of {sorted(PRESETS_V2)}") from None


# --- per-intent scripts ---------------------------------------------------

@dataclass(frozen=True, slots=True)
class SyllableSpec:
    vowel_from: str
    vowel_to: str
    pitch_st: float     # offset from the base pitch
    duration_s: float   # at rate 1.0
    onset: str


@dataclass(frozen=True, slots=True)
class ScriptV2:
    """One intent's fixed shape. Semitones are relative to the identity's pitch."""

    syllables: tuple[SyllableSpec, ...]
    gaps_s: tuple[float, ...]             # one pause between each pair of syllables
    lead_s: float                         # silence before the first syllable
    contour_st: tuple[float, float, float]  # gesture inside each syllable
    gain: float                           # loudness relative to the identity's volume
    final_contour_st: tuple[float, float, float] | None = None  # last syllable's gesture


def _s(vowel_from: str, vowel_to: str, pitch_st: float, duration_s: float, onset: str) -> SyllableSpec:
    return SyllableSpec(vowel_from, vowel_to, pitch_st, duration_s, onset)


SCRIPTS_V2: dict[VocalIntent, ScriptV2] = {
    VocalIntent.GREETING: ScriptV2(
        syllables=(_s("oo", "eh", 0.0, 0.118, "b"), _s("eh", "ii", 2.2, 0.108, "h"),
                   _s("ah", "ii", 0.3, 0.148, "n")),
        gaps_s=(0.037, 0.026), lead_s=0.018, contour_st=(-1.0, 1.7, 0.5), gain=0.92,
    ),
    VocalIntent.ACKNOWLEDGEMENT: ScriptV2(
        syllables=(_s("uh", "oo", -2.4, 0.152, "m"), _s("oo", "uh", -1.0, 0.116, "m")),
        gaps_s=(0.051,), lead_s=0.012, contour_st=(-0.5, 0.3, -0.7), gain=0.68,
    ),
    VocalIntent.THINKING: ScriptV2(
        syllables=(_s("uh", "oo", -3.6, 0.241, "m"), _s("oo", "eh", -2.8, 0.169, "h")),
        gaps_s=(0.127,), lead_s=0.024, contour_st=(-0.5, 0.6, -1.4), gain=0.56,
    ),
    VocalIntent.CURIOUS: ScriptV2(
        syllables=(_s("uu", "eh", -0.7, 0.156, "h"), _s("eh", "ii", 2.1, 0.196, "y")),
        gaps_s=(0.046,), lead_s=0.023, contour_st=(-1.3, 1.0, 3.1), gain=0.80,
    ),
    VocalIntent.HAPPY: ScriptV2(
        syllables=(_s("eh", "ii", 1.2, 0.102, "y"), _s("oo", "eh", 3.0, 0.102, "b"),
                   _s("eh", "ah", 2.0, 0.116, "y"), _s("ah", "ii", 4.2, 0.132, "h")),
        gaps_s=(0.025, 0.032, 0.021), lead_s=0.014, contour_st=(-0.7, 1.2, 0.7), gain=0.85,
    ),
    # The lab's falling gesture, but the last syllable lifts: confusion stays an
    # unresolved question, as in V1.
    VocalIntent.CONFUSED: ScriptV2(
        syllables=(_s("oo", "uh", -1.8, 0.166, "m"), _s("eh", "uu", 1.0, 0.131, "h"),
                   _s("uh", "oo", -3.0, 0.172, "n")),
        gaps_s=(0.074, 0.063), lead_s=0.021, contour_st=(0.7, -0.3, -1.7), gain=0.68,
        final_contour_st=(-0.6, -1.0, 2.2),
    ),
    VocalIntent.ERROR: ScriptV2(
        syllables=(_s("ah", "oo", -2.0, 0.135, "h"), _s("oo", "uh", -4.6, 0.210, "m")),
        gaps_s=(0.069,), lead_s=0.029, contour_st=(0.5, -1.6, -2.7), gain=0.66,
    ),
    VocalIntent.FAREWELL: ScriptV2(
        syllables=(_s("eh", "oo", 0.0, 0.144, "y"), _s("oo", "uu", -2.2, 0.164, "h"),
                   _s("uh", "oo", -4.0, 0.205, "m")),
        gaps_s=(0.057, 0.062), lead_s=0.019, contour_st=(1.3, -0.4, -2.0), gain=0.70,
    ),
}


# --- planning -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SyllableV2:
    start_s: float
    duration_s: float
    pitch_hz: tuple[float, float, float]  # start, middle, end of the gesture
    vowel_from: str
    vowel_to: str
    onset: str
    level: float
    vibrato_rate_hz: float


@dataclass(frozen=True, slots=True)
class PlanV2:
    syllables: tuple[SyllableV2, ...]
    duration_s: float
    loudness: float
    brightness: float
    volume: float
    vibrato_st: float
    preset: PresetV2 = BASELINE


def _hz(base_hz: float, st: float) -> float:
    return min(_MAX_F0_HZ, max(_MIN_F0_HZ, base_hz * 2.0 ** (st / 12.0)))


def plan_utterance_v2(
    request: VocalizationRequest,
    identity: VoiceIdentity,
    rng: random.Random,
    preset: str | PresetV2 = BASELINE,
) -> PlanV2:
    """Place the intent's scripted syllables in time and pitch for one request."""
    identity = identity.bounded()
    preset = preset_v2(preset)
    script = SCRIPTS_V2[request.intent]
    intensity = request.intensity

    # Same intensity mapping as V1: wider movement, more energy, a touch faster.
    movement = identity.pitch_variation * (0.6 + 0.8 * intensity) * preset.movement_scale
    energy = script.gain * (0.7 + 0.3 * intensity)
    rate = identity.speaking_rate * (0.92 + 0.16 * intensity) * preset.rate_scale
    limit = preset.max_excursion_st

    def pitch_of(st: float) -> float:
        return _hz(identity.base_pitch_hz, min(limit, max(-limit, st)))

    drafts: list[list] = []
    cursor = script.lead_s / rate
    count = len(script.syllables)
    for index, spec in enumerate(script.syllables):
        is_last = index == count - 1
        duration = spec.duration_s * rng.uniform(*preset.duration_jitter) / rate
        if is_last:
            duration *= preset.final_stretch
        contour = script.contour_st
        if is_last and script.final_contour_st is not None:
            contour = script.final_contour_st
        offset = spec.pitch_st + rng.gauss(0.0, preset.offset_jitter_st)
        if preset.declination_st:
            offset += preset.declination_st * index / max(1, count - 1)
        points = tuple((c + offset) * movement for c in contour)
        level = (_HUM_LEVEL if spec.onset == OnsetV2.HUM else 1.0) * rng.uniform(0.92, 1.0)
        if preset.accent_depth and 0 < index < count - 1:
            level *= 1.0 - preset.accent_depth
        vibrato_rate = 5.1 + rng.uniform(-0.3, 0.3)

        split = (preset.split_chance > 0.0
                 and spec.duration_s >= preset.split_min_s
                 and rng.random() < preset.split_chance)
        if split:
            # Two quicker syllables continuing one gesture and one vowel path:
            # from → mid, then a brief closure, then mid → to, with a soft onset.
            mid_vowel = rng.choice([v for v in VOWELS_V2 if v not in (spec.vowel_from, spec.vowel_to)])
            onset = rng.choice(_SPLIT_ONSETS)
            share = rng.uniform(0.4, 0.55)
            closure = preset.split_gap_s / rate
            first = max(0.03, duration * share - closure / 2)
            second = max(0.03, duration * (1.0 - share) - closure / 2)
            middle = 0.5 * (points[0] + points[1]), points[1], 0.5 * (points[1] + points[2])
            halves = (
                (first, (points[0], middle[0], points[1]), spec.vowel_from, mid_vowel, spec.onset),
                (second, (points[1], middle[2], points[2]), mid_vowel, spec.vowel_to, onset),
            )
            for half, (length, half_points, vowel_from, vowel_to, half_onset) in enumerate(halves):
                drafts.append([cursor, length, tuple(pitch_of(p) for p in half_points),
                               vowel_from, vowel_to, half_onset, level, vibrato_rate])
                cursor += length + (closure if half == 0 else 0.0)
        else:
            drafts.append([cursor, duration, tuple(pitch_of(p) for p in points),
                           spec.vowel_from, spec.vowel_to, spec.onset, level, vibrato_rate])
            cursor += duration
        if index < len(script.gaps_s):
            cursor += script.gaps_s[index] * rng.uniform(*preset.gap_jitter) / rate

    # Bound the length, tail included, by scaling the whole timeline.
    spoken = cursor
    target = min(MAX_DURATION_S - TAIL_S, max(MIN_DURATION_S - TAIL_S, spoken))
    scale = target / spoken if spoken > 0 else 1.0
    syllables = tuple(
        SyllableV2(
            start_s=d[0] * scale, duration_s=d[1] * scale, pitch_hz=d[2],
            vowel_from=d[3], vowel_to=d[4], onset=d[5], level=d[6], vibrato_rate_hz=d[7],
        )
        for d in drafts
    )
    return PlanV2(
        syllables=syllables,
        duration_s=target + TAIL_S,
        loudness=min(1.0, energy),
        brightness=identity.brightness,
        volume=identity.volume,
        vibrato_st=preset.vibrato_st * identity.pitch_variation,
        preset=preset,
    )


# --- rendering ------------------------------------------------------------

def _smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def _gesture(points: tuple[float, float, float], t: float) -> float:
    """Cosine-smoothed interpolation through start, middle and end at ``t``."""
    if t < _CONTOUR_MID:
        a, b, x = points[0], points[1], t / _CONTOUR_MID
    else:
        a, b, x = points[1], points[2], (t - _CONTOUR_MID) / (1.0 - _CONTOUR_MID)
    return a + (b - a) * (0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, x))))


def _harmonic_weights(f0: float, formants: tuple[float, ...], tilt: float, preset: PresetV2) -> list[float]:
    end, start = preset.taper_end_hz, preset.taper_start_hz
    count = max(1, min(_MAX_PARTIALS, int(end / f0)))
    weights = []
    for k in range(1, count + 1):
        frequency = k * f0
        resonance = preset.resonance_floor
        for centre, gain, width in zip(formants, preset.formant_gains, preset.formant_widths_hz):
            ratio = (frequency - centre) / width
            resonance += gain * math.exp(-0.5 * ratio * ratio)
        taper = min(1.0, max(0.0, (end - frequency) / (end - start)))
        weights.append(resonance * taper * k ** -tilt)
    return weights


def _voiced(syllable: SyllableV2, plan: PlanV2) -> list[float]:
    """The syllable's voiced body, enveloped and normalized to a peak of 1."""
    sr = SAMPLE_RATE
    length = max(64, int(round(syllable.duration_s * sr)))
    duration = length / sr
    vowel_a = VOWELS_V2[syllable.vowel_from]
    vowel_b = VOWELS_V2[syllable.vowel_to]
    preset = plan.preset
    # Baseline is the lab's 1.12 − 0.27·b, widened so brightness is clearly audible.
    tilt = preset.tilt_base - preset.tilt_brightness * plan.brightness
    log_points = tuple(math.log2(p) for p in syllable.pitch_hz)
    attack_s, release_s = preset.attack_s, preset.release_s

    body = [0.0] * length
    phase = 0.0
    weights: list[float] = []
    for n in range(length):
        t = n / length
        if n % _BLOCK == 0:
            morph = _smoothstep((t - preset.morph_start) / preset.morph_span)
            formants = tuple(a + (b - a) * morph for a, b in zip(vowel_a, vowel_b))
            weights = _harmonic_weights(2.0 ** _gesture(log_points, t), formants, tilt, preset)

        vibrato = (plan.vibrato_st
                   * math.sin(2.0 * math.pi * syllable.vibrato_rate_hz * n / sr + 1.3)
                   * min(1.0, max(0.0, (t - 0.12) * 3.0)))
        f0 = min(_MAX_F0_HZ, max(_MIN_F0_HZ, 2.0 ** (_gesture(log_points, t) + vibrato / 12.0)))
        phase += 2.0 * math.pi * f0 / sr
        if phase > 2.0 * math.pi:
            phase -= 2.0 * math.pi

        # sin(k·θ) for every partial by Chebyshev recurrence, as in V1.
        theta = phase + _PHASE_LEAD
        c2 = 2.0 * math.cos(theta)
        s_prev, s_cur = 0.0, math.sin(theta)
        value = 0.0
        for weight in weights:
            value += weight * s_cur
            s_prev, s_cur = s_cur, c2 * s_cur - s_prev

        seconds = n / sr
        env = (_smoothstep(seconds / attack_s)
               * _smoothstep((duration - seconds) / release_s))
        body[n] = value * env

    peak = max((abs(x) for x in body), default=0.0)
    if peak > 1e-12:
        body = [x / peak for x in body]
    return body


def _onset_noise(syllable: SyllableV2, length: int, rng: random.Random, gain: float) -> list[float]:
    """A windowed, smoothed noise texture for the syllable's first milliseconds."""
    seconds, strength, raw_share = _ONSETS[syllable.onset]
    strength *= gain
    count = min(length, max(1, int(seconds * SAMPLE_RATE)))
    noise = [rng.gauss(0.0, 1.0) for _ in range(count)]
    # Six-tap moving average, centred: a gentle low-pass with no extra dependency.
    smooth = []
    for i in range(count):
        window = noise[max(0, i - 3):i + 3]
        smooth.append(sum(window) / 6.0)
    out = []
    for i in range(count):
        window = math.sin(math.pi * i / max(1, count - 1)) ** 2
        colour = raw_share * noise[i] + (1.0 - raw_share) * smooth[i]
        out.append(colour * window * strength)
    return out


def render_plan_v2(plan: PlanV2, rng: random.Random) -> PcmClip:
    """Render a V2 plan to PCM. Deterministic for a given ``rng`` state."""
    sr = SAMPLE_RATE
    total = max(1, int(round(plan.duration_s * sr)))
    buffer = [0.0] * total
    for syllable in plan.syllables:
        body = _voiced(syllable, plan)
        for i, x in enumerate(_onset_noise(syllable, len(body), rng, plan.preset.onset_gain)):
            body[i] += x
        start = int(syllable.start_s * sr)
        for i, x in enumerate(body):
            if start + i >= total:
                break
            buffer[start + i] += x * syllable.level
    return PcmClip(sample_rate=sr, samples=_to_pcm16(_master(buffer, plan)))


def _master(buffer: list[float], plan: PlanV2) -> list[float]:
    """Peak-normalize below full scale, keep intent loudness, fade both ends."""
    peak = max((abs(x) for x in buffer), default=0.0)
    if peak < 1e-9 or plan.volume <= 0.0:
        return [0.0] * len(buffer)
    gain = PEAK_HEADROOM * plan.volume * (0.55 + 0.45 * plan.loudness) / peak
    fade = max(1, int(0.004 * SAMPLE_RATE))
    total = len(buffer)
    out = [0.0] * total
    for i, x in enumerate(buffer):
        edge = min(i, total - 1 - i)
        env = _smoothstep(edge / fade) if edge < fade else 1.0
        out[i] = max(-1.0, min(1.0, x * gain * env))
    return out


def synthesize_v2(
    request: VocalizationRequest,
    identity: VoiceIdentity | None = None,
    preset: str | PresetV2 = BASELINE,
) -> PcmClip:
    """Plan and render one V2 vocalization. Safe to call from any thread.

    ``preset`` is a name from `PRESETS_V2` or a `PresetV2`; the default is the
    baseline V2 voice, the only one the application uses.
    """
    rng = random.Random(request.seed) if request.seed is not None else random.Random()
    plan = plan_utterance_v2(request, identity or VoiceIdentity(), rng, preset)
    return render_plan_v2(plan, rng)
