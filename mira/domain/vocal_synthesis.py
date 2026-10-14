"""Procedural pseudo-speech: from a `VocalizationRequest` to a `PcmClip`.

Pure Python and the standard library only, so it runs wherever MIRA's domain
runs — including a future embedded board with no numpy. Nothing here touches a
clock, a device or a thread; the same request, identity and seed always yield
the same bytes.

Two stages, kept apart so each can be tested on its own terms:

1. **Planning** (`plan_utterance`). An intent's `Prosody` — syllable count,
   contour, rhythm, vowel palette, final inflection — is turned into a concrete
   `UtterancePlan`: a short list of `Syllable` values with start times, pitch
   glides, vowel colours and consonant-like onsets. All randomness that shapes
   *what* is said lives here, and the total length is clamped to
   ``MIN_DURATION_S..MAX_DURATION_S``.
2. **Rendering** (`render_plan`). Each syllable is a harmonic voiced source
   whose partials are weighted by vowel formant resonances and a brightness-
   dependent spectral tilt, with vibrato and slow pitch jitter, shaped by a
   raised-cosine envelope and preceded, sometimes, by a filtered noise burst
   (plosive-like) or a soft hiss (fricative-like). The mix is gently low-passed,
   DC-blocked, peak-normalized below full scale and faded at both ends.

The result is babble, not words: vowels are formant colours, consonants are
noise textures, and nothing maps onto a real phoneme sequence. The sound
identity is original — no samples, no recorded voices.
"""

from __future__ import annotations

import io
import math
import random
import sys
import wave
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from mira.domain.vocalization import (
    DEFAULT_ENGINE,
    PcmClip,
    VocalIntent,
    VocalizationRequest,
    VoiceEngine,
    VoiceIdentity,
)

SAMPLE_RATE = 22050
MIN_DURATION_S = 0.2
MAX_DURATION_S = 1.2
# Silence after the last syllable, so the release and filters ring out inside
# the clip. Counted inside MAX_DURATION_S.
TAIL_S = 0.03
# Normalization target as a fraction of full scale at volume 1.0. Below 1.0 so
# int16 conversion can never clip.
PEAK_HEADROOM = 0.89

_MIN_F0_HZ = 80.0
_MAX_F0_HZ = 1100.0
_MAX_PARTIAL_HZ = 5200.0
_MAX_PARTIALS = 14
# Harmonic weights are recomputed every block rather than every sample; at
# 22.05 kHz a 64-sample block is ~3 ms, far below any audible formant motion.
_BLOCK = 64


# --- vowel colours ------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Vowel:
    formants_hz: tuple[float, float, float]
    gains: tuple[float, float, float]


# Formants are a small character's, roughly 10% above an adult's. "m" is a
# closed-mouth hum: one strong low resonance and very little above it.
_VOWELS: dict[str, _Vowel] = {
    "a": _Vowel((900.0, 1450.0, 2950.0), (1.0, 0.75, 0.30)),
    "e": _Vowel((580.0, 2250.0, 3050.0), (1.0, 0.65, 0.30)),
    "i": _Vowel((380.0, 2700.0, 3400.0), (1.0, 0.50, 0.30)),
    "o": _Vowel((580.0, 1000.0, 2750.0), (1.0, 0.80, 0.20)),
    "u": _Vowel((400.0, 900.0, 2500.0), (1.0, 0.55, 0.15)),
    "m": _Vowel((290.0, 1150.0, 2450.0), (1.0, 0.15, 0.06)),
}
_BANDWIDTHS_HZ = (110.0, 150.0, 230.0)


class Onset:
    """Consonant-like textures that may precede a syllable's voicing."""

    NONE = ""
    PLOSIVE = "plosive"      # a few ms of low-passed noise: "t", "p", "k" feel
    FRICATIVE = "fricative"  # a soft high-passed hiss: "s", "h" feel


# --- prosody --------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Prosody:
    """How one intent moves. Semitones are relative to the identity's pitch."""

    syllables: tuple[int, int]        # inclusive count range
    pitch_offset_st: float            # where the utterance sits
    contour_st: tuple[float, ...]     # pitch targets across the utterance
    syllable_s: float                 # mean syllable length at rate 1.0
    gap_s: float                      # mean pause between syllables
    energy: float                     # loudness relative to the identity's volume
    vowels: str                       # palette drawn from `_VOWELS`
    consonant_chance: float           # probability a syllable gets an onset
    final_glide_st: float             # inflection inside the last syllable
    final_stretch: float              # last syllable length multiplier
    bounce_st: float = 0.0            # alternating per-syllable offset
    vibrato_st: float = 0.25          # vibrato depth


# The sound design, one row per intent. Shared character comes from the
# identity (pitch, timbre, rate); intents differ in contour, rhythm and colour.
PROSODY: dict[VocalIntent, Prosody] = {
    # "hi-ya-hey!": bright, quickish, climbing to a lifted ending.
    VocalIntent.GREETING: Prosody(
        syllables=(3, 4), pitch_offset_st=2.0, contour_st=(-1.0, 2.0, 0.5, 3.0),
        syllable_s=0.13, gap_s=0.03, energy=0.9, vowels="aei",
        consonant_chance=0.6, final_glide_st=4.0, final_stretch=1.5, bounce_st=1.0,
    ),
    # "mm-hm": two short closed syllables, low then settled.
    VocalIntent.ACKNOWLEDGEMENT: Prosody(
        syllables=(2, 2), pitch_offset_st=0.0, contour_st=(-1.5, 1.0),
        syllable_s=0.11, gap_s=0.05, energy=0.7, vowels="mmu",
        consonant_chance=0.15, final_glide_st=-1.5, final_stretch=1.3,
    ),
    # "hmm... mo...": slow, low, hummed, with long pauses and a small wander.
    VocalIntent.THINKING: Prosody(
        syllables=(2, 3), pitch_offset_st=-2.0, contour_st=(0.0, -0.7, 0.5),
        syllable_s=0.17, gap_s=0.09, energy=0.5, vowels="mou",
        consonant_chance=0.1, final_glide_st=1.0, final_stretch=1.6,
        bounce_st=0.6, vibrato_st=0.15,
    ),
    # "oo-weh?": mid-paced, ending in a wide question-like rise.
    VocalIntent.CURIOUS: Prosody(
        syllables=(2, 3), pitch_offset_st=1.0, contour_st=(-1.0, 0.0, 1.0),
        syllable_s=0.12, gap_s=0.04, energy=0.75, vowels="oue",
        consonant_chance=0.4, final_glide_st=7.0, final_stretch=1.7, bounce_st=0.5,
    ),
    # "ya-di-ba-dee!": quick, high, bouncy, bright vowels.
    VocalIntent.HAPPY: Prosody(
        syllables=(4, 5), pitch_offset_st=4.0, contour_st=(0.0, 2.0, 1.0, 3.0, 2.0),
        syllable_s=0.09, gap_s=0.025, energy=0.95, vowels="aie",
        consonant_chance=0.55, final_glide_st=3.0, final_stretch=1.4,
        bounce_st=2.5, vibrato_st=0.3,
    ),
    # "wo-bu-eh?": unsure — a wobbling contour, slower, unresolved rise.
    VocalIntent.CONFUSED: Prosody(
        syllables=(3, 3), pitch_offset_st=0.0, contour_st=(0.0, 2.0, -1.5, 1.0),
        syllable_s=0.13, gap_s=0.06, energy=0.65, vowels="oue",
        consonant_chance=0.3, final_glide_st=5.0, final_stretch=1.6,
        bounce_st=2.0, vibrato_st=0.5,
    ),
    # "uh-oh": two darker syllables, the second clearly lower and falling.
    VocalIntent.ERROR: Prosody(
        syllables=(2, 2), pitch_offset_st=-1.0, contour_st=(2.0, -4.0),
        syllable_s=0.14, gap_s=0.07, energy=0.6, vowels="uo",
        consonant_chance=0.35, final_glide_st=-4.0, final_stretch=1.6,
    ),
    # "ba-ye-bo...": gentle descending phrase with a long soft ending.
    VocalIntent.FAREWELL: Prosody(
        syllables=(3, 3), pitch_offset_st=1.0, contour_st=(2.0, 0.0, -3.0),
        syllable_s=0.14, gap_s=0.05, energy=0.7, vowels="aeo",
        consonant_chance=0.45, final_glide_st=-3.0, final_stretch=1.9, bounce_st=0.5,
    ),
}


def prosody_for(intent: VocalIntent) -> Prosody:
    """The prosody row for ``intent``. Every `VocalIntent` has one."""
    return PROSODY[intent]


# --- planning -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Syllable:
    start_s: float
    duration_s: float
    pitch_start_hz: float
    pitch_end_hz: float
    vowel_from: str
    vowel_to: str
    onset: str
    level: float


@dataclass(frozen=True, slots=True)
class UtterancePlan:
    syllables: tuple[Syllable, ...]
    duration_s: float               # end of the last syllable plus the tail
    loudness: float                 # 0..1, the intent's energy at this intensity
    brightness: float
    volume: float
    vibrato_st: float


_FRICATIVE_S = 0.025


def _semitones(base_hz: float, st: float) -> float:
    return min(_MAX_F0_HZ, max(_MIN_F0_HZ, base_hz * 2.0 ** (st / 12.0)))


def _contour_at(contour: tuple[float, ...], position: float) -> float:
    """Piecewise-linear interpolation of ``contour`` over ``0..1``."""
    if len(contour) == 1:
        return contour[0]
    scaled = min(1.0, max(0.0, position)) * (len(contour) - 1)
    index = min(int(scaled), len(contour) - 2)
    fraction = scaled - index
    return contour[index] + (contour[index + 1] - contour[index]) * fraction


def plan_utterance(
    request: VocalizationRequest,
    identity: VoiceIdentity,
    rng: random.Random,
) -> UtterancePlan:
    """Decide syllables, timing and pitch for one request."""
    identity = identity.bounded()
    prosody = prosody_for(request.intent)
    intensity = request.intensity

    # Intensity widens movement and lifts energy; it never changes the shape.
    movement = identity.pitch_variation * (0.6 + 0.8 * intensity)
    energy = prosody.energy * (0.7 + 0.3 * intensity)
    rate = identity.speaking_rate * (0.92 + 0.16 * intensity)

    low, high = prosody.syllables
    count = rng.randint(low, high)
    center_hz = _semitones(identity.base_pitch_hz, prosody.pitch_offset_st)

    drafts: list[list] = []
    cursor = 0.0
    previous_vowel = ""
    for index in range(count):
        is_last = index == count - 1
        duration = prosody.syllable_s * rng.uniform(0.8, 1.2) / rate
        if is_last:
            duration *= prosody.final_stretch

        onset = Onset.NONE
        if rng.random() < prosody.consonant_chance:
            onset = Onset.PLOSIVE if rng.random() < 0.6 else Onset.FRICATIVE

        choices = [v for v in prosody.vowels if v != previous_vowel] or list(prosody.vowels)
        vowel_from = rng.choice(choices)
        vowel_to = vowel_from
        if rng.random() < 0.35:
            vowel_to = rng.choice(prosody.vowels)
        previous_vowel = vowel_from

        position_start = index / count
        position_end = (index + 1) / count
        bounce = prosody.bounce_st * (1.0 if index % 2 else -1.0) * 0.5
        drift = rng.uniform(-0.4, 0.4)
        st_start = (_contour_at(prosody.contour_st, position_start) + bounce + drift) * movement
        st_end = (_contour_at(prosody.contour_st, position_end) + bounce + drift) * movement
        if is_last:
            st_end = st_start + prosody.final_glide_st * movement
        else:
            # A slight fall inside each syllable reads as speech, not notes.
            st_end = 0.5 * (st_start + st_end) - 0.6

        level = rng.uniform(0.85, 1.0) * (0.9 if index else 1.0)
        if onset == Onset.FRICATIVE:
            cursor += _FRICATIVE_S / rate

        drafts.append(
            [cursor, duration, _semitones(center_hz, st_start), _semitones(center_hz, st_end),
             vowel_from, vowel_to, onset, level]
        )
        cursor += duration
        if not is_last:
            gap = prosody.gap_s * rng.uniform(0.6, 1.4) / rate
            cursor += gap

    # Bound the spoken length; the tail is added on top and counted in the max.
    spoken = cursor
    target = min(MAX_DURATION_S - TAIL_S, max(MIN_DURATION_S - TAIL_S, spoken))
    scale = target / spoken if spoken > 0 else 1.0
    syllables = tuple(
        Syllable(
            start_s=d[0] * scale, duration_s=d[1] * scale,
            pitch_start_hz=d[2], pitch_end_hz=d[3],
            vowel_from=d[4], vowel_to=d[5], onset=d[6], level=d[7],
        )
        for d in drafts
    )
    return UtterancePlan(
        syllables=syllables,
        duration_s=target + TAIL_S,
        loudness=min(1.0, energy),
        brightness=identity.brightness,
        volume=identity.volume,
        vibrato_st=prosody.vibrato_st * identity.pitch_variation,
    )


# --- rendering ------------------------------------------------------------

def _formant_gain(frequency: float, f: tuple[float, float, float], g: tuple[float, float, float]) -> float:
    total = 0.12
    for centre, gain, width in zip(f, g, _BANDWIDTHS_HZ):
        ratio = (frequency - centre) / width
        total += gain / (1.0 + ratio * ratio)
    return total


def _partial_weights(f0: float, vowel_a: _Vowel, vowel_b: _Vowel, mix: float, tilt: float) -> list[float]:
    formants = tuple(a + (b - a) * mix for a, b in zip(vowel_a.formants_hz, vowel_b.formants_hz))
    gains = tuple(a + (b - a) * mix for a, b in zip(vowel_a.gains, vowel_b.gains))
    count = max(1, min(_MAX_PARTIALS, int(_MAX_PARTIAL_HZ / f0)))
    return [
        _formant_gain(k * f0, formants, gains) * k ** -tilt
        for k in range(1, count + 1)
    ]


def _raised_cosine(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, x)))


def _render_syllable(out: list[float], syllable: Syllable, plan: UtterancePlan, rng: random.Random) -> None:
    sr = SAMPLE_RATE
    start = int(syllable.start_s * sr)
    length = max(1, int(syllable.duration_s * sr))
    vowel_a = _VOWELS[syllable.vowel_from]
    vowel_b = _VOWELS[syllable.vowel_to]
    tilt = 1.7 - 0.9 * plan.brightness

    attack = max(1, int(min(0.015, 0.25 * syllable.duration_s) * sr))
    release = max(1, int(min(0.06, 0.45 * syllable.duration_s) * sr))
    vibrato_rate = rng.uniform(5.0, 6.5)
    vibrato_phase = rng.uniform(0.0, 2.0 * math.pi)
    jitter = 0.0
    ratio = syllable.pitch_end_hz / syllable.pitch_start_hz

    phase = 0.0
    weights: list[float] = []
    norm = 1.0
    end = min(len(out), start + length)
    for i in range(start, end):
        n = i - start
        t = n / length
        if n % _BLOCK == 0:
            # Slow random-walk jitter, so no two syllables glide identically.
            jitter = max(-0.25, min(0.25, jitter + rng.uniform(-0.06, 0.06)))
            f0_block = syllable.pitch_start_hz * ratio ** (t * t * (3.0 - 2.0 * t))
            weights = _partial_weights(f0_block, vowel_a, vowel_b, _raised_cosine(t), tilt)
            norm = 1.0 / sum(weights)

        glide = ratio ** (t * t * (3.0 - 2.0 * t))
        vibrato = plan.vibrato_st * math.sin(vibrato_phase + 2.0 * math.pi * vibrato_rate * n / sr)
        f0 = syllable.pitch_start_hz * glide * 2.0 ** ((vibrato + jitter) / 12.0)
        phase += 2.0 * math.pi * f0 / sr
        if phase > 2.0 * math.pi:
            phase -= 2.0 * math.pi

        # sin(k*phase) by Chebyshev recurrence: one sin and one cos per sample.
        s1 = math.sin(phase)
        c2 = 2.0 * math.cos(phase)
        s_prev, s_cur = 0.0, s1
        value = 0.0
        for weight in weights:
            value += weight * s_cur
            s_prev, s_cur = s_cur, c2 * s_cur - s_prev

        if n < attack:
            env = _raised_cosine(n / attack)
        elif n > length - release:
            env = _raised_cosine((length - n) / release)
        else:
            env = 1.0
        env *= 1.0 - 0.25 * t
        out[i] += value * norm * env * syllable.level

    if syllable.onset:
        _render_onset(out, syllable, plan, rng)


def _render_onset(out: list[float], syllable: Syllable, plan: UtterancePlan, rng: random.Random) -> None:
    sr = SAMPLE_RATE
    voiced_start = int(syllable.start_s * sr)
    if syllable.onset == Onset.PLOSIVE:
        length = int(0.009 * sr)
        begin = voiced_start
        level = 0.35 * syllable.level
        smoothing = 0.35 + 0.3 * (1.0 - plan.brightness)  # low-pass
        highpass = False
    else:
        length = int(_FRICATIVE_S * sr)
        begin = max(0, voiced_start - length)
        level = 0.12 * syllable.level
        smoothing = 0.5
        highpass = True

    lowpassed = 0.0
    for n in range(length):
        i = begin + n
        if i >= len(out):
            break
        noise = rng.uniform(-1.0, 1.0)
        lowpassed += (1.0 - smoothing) * (noise - lowpassed)
        sample = noise - lowpassed if highpass else lowpassed
        x = n / length
        env = _raised_cosine(x * 4.0) * (1.0 - x) if highpass else (1.0 - x) ** 2
        out[i] += sample * env * level


def _finish(buffer: list[float], plan: UtterancePlan) -> list[float]:
    """Gentle low-pass, DC block, normalization and end fades."""
    sr = SAMPLE_RATE
    cutoff = 2600.0 + 3400.0 * plan.brightness
    lp_coef = 1.0 - math.exp(-2.0 * math.pi * cutoff / sr)
    hp_coef = math.exp(-2.0 * math.pi * 60.0 / sr)

    lowpassed = 0.0
    hp_prev_in = 0.0
    hp_prev_out = 0.0
    filtered = [0.0] * len(buffer)
    for i, x in enumerate(buffer):
        lowpassed += lp_coef * (x - lowpassed)
        hp_out = hp_coef * (hp_prev_out + lowpassed - hp_prev_in)
        hp_prev_in, hp_prev_out = lowpassed, hp_out
        filtered[i] = hp_out

    peak = max((abs(x) for x in filtered), default=0.0)
    if peak < 1e-9 or plan.volume <= 0.0:
        return [0.0] * len(filtered)
    # Peak, not RMS: the clip is short and the concern is the loudest instant.
    # Quieter intents (thinking, error) stay quieter than lively ones.
    gain = PEAK_HEADROOM * plan.volume * (0.55 + 0.45 * plan.loudness) / peak

    fade = max(1, int(0.004 * sr))
    total = len(filtered)
    for i in range(total):
        edge = min(i, total - 1 - i)
        env = _raised_cosine(edge / fade) if edge < fade else 1.0
        filtered[i] = max(-1.0, min(1.0, filtered[i] * gain * env))
    return filtered


def _to_pcm16(samples: list[float]) -> bytes:
    pcm = array("h", (int(round(x * 32767.0)) for x in samples))
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm.tobytes()


def render_plan(plan: UtterancePlan, rng: random.Random) -> PcmClip:
    """Render a plan to PCM. Deterministic for a given ``rng`` state."""
    total = max(1, int(round(plan.duration_s * SAMPLE_RATE)))
    buffer = [0.0] * total
    for syllable in plan.syllables:
        _render_syllable(buffer, syllable, plan, rng)
    return PcmClip(sample_rate=SAMPLE_RATE, samples=_to_pcm16(_finish(buffer, plan)))


def synthesize(
    request: VocalizationRequest,
    identity: VoiceIdentity | None = None,
    engine: VoiceEngine = DEFAULT_ENGINE,
) -> PcmClip:
    """Plan and render one vocalization. Safe to call from any thread.

    ``engine`` picks the sound design; the default, V1, is the one below. V2
    lives in `mira.domain.vocal_synthesis_v2`, which builds on this module's
    constants, hence the deferred import.
    """
    if engine is VoiceEngine.V2:
        from mira.domain.vocal_synthesis_v2 import synthesize_v2

        return synthesize_v2(request, identity)
    rng = random.Random(request.seed) if request.seed is not None else random.Random()
    plan = plan_utterance(request, identity or VoiceIdentity(), rng)
    return render_plan(plan, rng)


def synthesizer_for(
    engine: VoiceEngine,
) -> Callable[[VocalizationRequest, VoiceIdentity], PcmClip]:
    """`synthesize` bound to ``engine``, in the shape `VoiceController` takes."""
    return partial(synthesize, engine=VoiceEngine(engine))


def clip_to_wav_bytes(clip: PcmClip) -> bytes:
    """Encode a clip as a RIFF/WAVE file in memory."""
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(clip.CHANNELS)
        wav.setsampwidth(clip.SAMPLE_WIDTH_BYTES)
        wav.setframerate(clip.sample_rate)
        wav.writeframes(clip.samples)
    return stream.getvalue()
