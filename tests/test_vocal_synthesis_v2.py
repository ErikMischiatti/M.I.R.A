"""The opt-in V2 synthesizer and engine selection, without any audio device.

V2 shares V1's contract, so most checks mirror `test_vocal_synthesis.py`; the
rest pin what V2 is for — a richer source, vowel morphs inside a syllable,
clean joins — and that selecting it never changes V1.
"""

from __future__ import annotations

import math
import random
import subprocess
import sys
import wave
from array import array
from pathlib import Path

import pytest

from mira.domain.vocal_synthesis import (
    MAX_DURATION_S,
    MIN_DURATION_S,
    PEAK_HEADROOM,
    SAMPLE_RATE,
    synthesize,
    synthesizer_for,
)
from mira.domain.vocal_synthesis_v2 import (
    BASELINE,
    PRESETS_V2,
    SCRIPTS_V2,
    VOWELS_V2,
    OnsetV2,
    PlanV2,
    PresetV2,
    SyllableV2,
    _harmonic_weights,
    plan_utterance_v2,
    preset_v2,
    render_plan_v2,
    synthesize_v2,
)
from mira.domain.vocalization import (
    DEFAULT_ENGINE,
    VocalIntent,
    VocalizationRequest,
    VoiceEngine,
    VoiceIdentity,
)
from test_vocal_synthesis import _high_frequency_energy, peak, rms, samples_of

ALL_INTENTS = tuple(VocalIntent)
V2 = VoiceEngine.V2
ONSETS = {OnsetV2.BREATH, OnsetV2.GLIDE, OnsetV2.SOFT_PLOSIVE, OnsetV2.NASAL, OnsetV2.HUM}


def plan(intent, seed=3, identity=VoiceIdentity(), intensity=0.6, preset="baseline") -> PlanV2:
    return plan_utterance_v2(VocalizationRequest(intent, intensity, seed), identity, random.Random(seed), preset)


# --- engine selection -----------------------------------------------------


def test_v1_stays_the_default_engine():
    assert DEFAULT_ENGINE is VoiceEngine.V1
    request = VocalizationRequest(VocalIntent.GREETING, seed=5)
    assert synthesize(request).samples == synthesize(request, None, VoiceEngine.V1).samples
    assert synthesize(request).samples != synthesize(request, None, V2).samples


def test_the_v2_engine_is_synthesize_v2():
    request = VocalizationRequest(VocalIntent.CURIOUS, seed=5)
    identity = VoiceIdentity(base_pitch_hz=280.0)
    assert synthesize(request, identity, V2).samples == synthesize_v2(request, identity).samples


def test_synthesizer_for_binds_an_engine_in_the_controller_shape():
    request = VocalizationRequest(VocalIntent.HAPPY, seed=2)
    identity = VoiceIdentity()
    for engine in VoiceEngine:
        assert synthesizer_for(engine)(request, identity).samples == synthesize(request, identity, engine).samples
    assert synthesizer_for("v2")(request, identity).samples == synthesize_v2(request, identity).samples


# --- scripts --------------------------------------------------------------


def test_every_intent_has_a_consistent_script():
    assert set(SCRIPTS_V2) == set(VocalIntent)
    for intent, script in SCRIPTS_V2.items():
        assert len(script.gaps_s) == len(script.syllables) - 1, intent
        for syllable in script.syllables:
            assert syllable.vowel_from in VOWELS_V2 and syllable.vowel_to in VOWELS_V2
            assert syllable.onset in ONSETS
        assert 0.0 < script.gain <= 1.0


# --- PCM validity -------------------------------------------------------


@pytest.mark.parametrize("intent", ALL_INTENTS)
def test_v2_produces_valid_bounded_pcm(intent):
    for seed in (41, 42):
        clip = synthesize_v2(VocalizationRequest(intent, seed=seed))
        assert clip.sample_rate == SAMPLE_RATE and clip.frame_count > 0
        assert MIN_DURATION_S - 1e-3 <= clip.duration_s <= MAX_DURATION_S + 1e-3
        pcm = samples_of(clip)
        assert 0 < max(abs(x) for x in pcm) <= int(PEAK_HEADROOM * 32767) + 1
        assert pcm[0] == 0 and pcm[-1] == 0


def test_zero_volume_is_silence():
    clip = synthesize_v2(VocalizationRequest(VocalIntent.HAPPY, seed=1), VoiceIdentity(volume=0.0))
    assert clip.frame_count > 0 and peak(clip) == 0


@pytest.mark.parametrize("rate", [0.5, 2.0, 100.0, 0.01])
def test_extreme_rates_stay_within_the_duration_bounds(rate):
    for intent in ALL_INTENTS:
        clip = synthesize_v2(VocalizationRequest(intent, 1.0, seed=5), VoiceIdentity(speaking_rate=rate))
        assert MIN_DURATION_S - 1e-3 <= clip.duration_s <= MAX_DURATION_S + 1e-3


def test_extreme_pitches_and_brightness_still_render_finite_bounded_audio():
    for pitch in (1.0, 120.0, 700.0, 10_000.0):
        for brightness in (0.0, 1.0):
            clip = synthesize_v2(
                VocalizationRequest(VocalIntent.CURIOUS, 1.0, seed=8),
                VoiceIdentity(base_pitch_hz=pitch, brightness=brightness, pitch_variation=2.0),
            )
            assert 0 < peak(clip) <= int(PEAK_HEADROOM * 32767) + 1


def test_syllables_are_ordered_non_overlapping_and_inside_the_clip():
    for intent in ALL_INTENTS:
        for seed in range(10):
            utterance = plan(intent, seed, intensity=1.0)
            end = 0.0
            for syllable in utterance.syllables:
                assert syllable.start_s >= end - 1e-9 and syllable.duration_s > 0.0
                end = syllable.start_s + syllable.duration_s
            assert end <= utterance.duration_s


def test_joins_are_click_free():
    """Every syllable starts and ends at silence, and the pauses are silent."""
    for intent in ALL_INTENTS:
        utterance = plan(intent, 41)
        pcm = samples_of(render_plan_v2(utterance, random.Random(41)))
        top = max(abs(x) for x in pcm)
        for syllable in utterance.syllables:
            first = int(syllable.start_s * SAMPLE_RATE)
            last = first + max(64, int(round(syllable.duration_s * SAMPLE_RATE))) - 1
            # Edges within 2% of the peak: a fade, not a step.
            assert abs(pcm[first]) <= 0.02 * top, (intent, "start")
            assert abs(pcm[min(last, len(pcm) - 1)]) <= 0.02 * top, (intent, "end")
        for left, right in zip(utterance.syllables, utterance.syllables[1:]):
            gap_start = int((left.start_s + left.duration_s) * SAMPLE_RATE) + 2
            gap_end = int(right.start_s * SAMPLE_RATE) - 1
            assert all(x == 0 for x in pcm[gap_start:gap_end]), intent


# --- determinism and parameter effects ----------------------------------


def test_the_same_seed_reproduces_the_same_bytes():
    request = VocalizationRequest(VocalIntent.CONFUSED, 0.8, seed=1234)
    assert synthesize_v2(request).samples == synthesize_v2(request).samples


def test_seeds_vary_the_detail_but_keep_the_intent_script():
    clips = {synthesize_v2(VocalizationRequest(VocalIntent.HAPPY, seed=s)).samples for s in range(5)}
    assert len(clips) == 5
    shapes = {
        tuple((s.vowel_from, s.vowel_to, s.onset) for s in plan(VocalIntent.HAPPY, seed).syllables)
        for seed in range(5)
    }
    assert len(shapes) == 1


def test_intents_are_audibly_different_for_the_same_seed():
    clips = {synthesize_v2(VocalizationRequest(i, seed=7)).samples for i in ALL_INTENTS}
    assert len(clips) == len(ALL_INTENTS)


def _mean_pitch(utterance: PlanV2) -> float:
    return sum(sum(s.pitch_hz) for s in utterance.syllables) / (3 * len(utterance.syllables))


def test_base_pitch_moves_every_syllable():
    low = _mean_pitch(plan(VocalIntent.GREETING, 9, VoiceIdentity(base_pitch_hz=200.0)))
    high = _mean_pitch(plan(VocalIntent.GREETING, 9, VoiceIdentity(base_pitch_hz=400.0)))
    assert high == pytest.approx(2 * low, rel=0.05)


def test_happy_sits_higher_than_thinking():
    assert _mean_pitch(plan(VocalIntent.HAPPY, 9)) > _mean_pitch(plan(VocalIntent.THINKING, 9))


def test_question_like_intents_end_rising_and_closing_ones_end_falling():
    for intent, rising in (
        (VocalIntent.CURIOUS, True),
        (VocalIntent.CONFUSED, True),
        (VocalIntent.ERROR, False),
        (VocalIntent.FAREWELL, False),
    ):
        for seed in range(5):
            start, _, end = plan(intent, seed).syllables[-1].pitch_hz
            assert (end > start) is rising, (intent, seed)


def test_speaking_rate_shortens_the_utterance():
    request = VocalizationRequest(VocalIntent.FAREWELL, seed=4)
    slow = synthesize_v2(request, VoiceIdentity(speaking_rate=0.7))
    fast = synthesize_v2(request, VoiceIdentity(speaking_rate=1.6))
    assert fast.duration_s < slow.duration_s


def test_volume_scales_the_peak():
    request = VocalizationRequest(VocalIntent.GREETING, seed=4)
    quiet = peak(synthesize_v2(request, VoiceIdentity(volume=0.2)))
    loud = peak(synthesize_v2(request, VoiceIdentity(volume=0.8)))
    assert loud == pytest.approx(4 * quiet, rel=0.02)


def test_pitch_variation_zero_flattens_every_gesture():
    flat = plan(VocalIntent.HAPPY, 1, VoiceIdentity(pitch_variation=0.0), intensity=1.0)
    assert {round(p, 6) for s in flat.syllables for p in s.pitch_hz} == {VoiceIdentity().base_pitch_hz}
    assert flat.vibrato_st == 0.0


def test_brightness_raises_high_frequency_content():
    request = VocalizationRequest(VocalIntent.ACKNOWLEDGEMENT, seed=6)
    dull = _high_frequency_energy(synthesize_v2(request, VoiceIdentity(brightness=0.0)))
    bright = _high_frequency_energy(synthesize_v2(request, VoiceIdentity(brightness=1.0)))
    assert bright > dull * 1.1


def test_intensity_widens_pitch_movement():
    def spread(intensity: float) -> float:
        pitches = [p for s in plan(VocalIntent.HAPPY, 2, intensity=intensity).syllables for p in s.pitch_hz]
        return max(pitches) / min(pitches)

    assert spread(1.0) > spread(0.0)


# --- what V2 changes ------------------------------------------------------


def _share_above(values, cutoff_hz: float) -> float:
    """Fraction of a Hann-windowed frame's energy above ``cutoff_hz``.

    Parseval gives the total; a direct DFT sums only the bins below the cutoff,
    so no FFT library is needed.
    """
    n = len(values)
    frame = [v * (0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1))) for i, v in enumerate(values)]
    total = n * sum(v * v for v in frame)
    below = 0.0
    for k in range(int(cutoff_hz * n / SAMPLE_RATE) + 1):
        re = sum(v * math.cos(2.0 * math.pi * k * i / n) for i, v in enumerate(frame))
        im = sum(v * math.sin(2.0 * math.pi * k * i / n) for i, v in enumerate(frame))
        below += (re * re + im * im) * (1 if k == 0 else 2)
    return max(0.0, 1.0 - below / total)


def _loudest_frame(pcm, size: int = 1024) -> list[int]:
    energy = [x * x for x in pcm]
    window = sum(energy[:size])
    best, best_at = window, 0
    for i in range(size, len(pcm)):
        window += energy[i] - energy[i - size]
        if window > best:
            best, best_at = window, i - size + 1
    return list(pcm[best_at:best_at + size])


def test_v2_has_a_richer_source_than_v1():
    """V2's harmonics reach above 1 kHz in every intent; V1's mostly do not.

    V1 is near-sine for most intents (under 1% of the energy above 1 kHz) but
    not all — its bright "a"/"e" farewell already gets there — so the
    comparison is over the whole set, while the floor holds per intent.
    """
    v1_total = v2_total = 0.0
    for intent in ALL_INTENTS:
        request = VocalizationRequest(intent, seed=41)
        v1 = _share_above(_loudest_frame(samples_of(synthesize(request))), 1000.0)
        v2 = _share_above(_loudest_frame(samples_of(synthesize_v2(request))), 1000.0)
        assert v2 > 0.04, (intent, v2)
        v1_total, v2_total = v1_total + v1, v2_total + v2
    assert v2_total > 4.0 * v1_total


def _single_syllable(vowel_from: str, vowel_to: str) -> list[int]:
    syllable = SyllableV2(
        start_s=0.02, duration_s=0.4, pitch_hz=(300.0, 300.0, 300.0),
        vowel_from=vowel_from, vowel_to=vowel_to, onset=OnsetV2.HUM, level=1.0, vibrato_rate_hz=5.0,
    )
    utterance = PlanV2(
        syllables=(syllable,), duration_s=0.45, loudness=1.0, brightness=0.45, volume=0.5, vibrato_st=0.0,
    )
    return list(samples_of(render_plan_v2(utterance, random.Random(0))))


def test_the_vowel_colour_morphs_within_a_syllable():
    """``uu → ii`` raises the second formant: the end has more energy up high."""
    size = 1024
    start, end = int(0.02 * SAMPLE_RATE), int(0.42 * SAMPLE_RATE)
    head = slice(start + 600, start + 600 + size)
    tail = slice(end - 1200 - size, end - 1200)

    morphing = _single_syllable("uu", "ii")
    assert _share_above(morphing[tail], 1600.0) > 3.0 * _share_above(morphing[head], 1600.0)
    # A steady vowel, by contrast, keeps its colour.
    steady = _single_syllable("uu", "uu")
    assert _share_above(steady[tail], 1600.0) == pytest.approx(_share_above(steady[head], 1600.0), rel=0.25)


# --- standalone demo ------------------------------------------------------


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "voice_demo.py"


def _demo(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--no-play", *args],
        capture_output=True, text=True, timeout=120,
    )


def test_the_demo_exports_v2_with_the_v1_file_names(tmp_path):
    result = _demo("--engine", "v2", "--out", str(tmp_path), "--intent", "curious", "--repeat", "2", "--seed", "41")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["00_curious.wav", "01_curious.wav"]
    with wave.open(str(tmp_path / "00_curious.wav"), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
    assert frames == synthesize_v2(VocalizationRequest(VocalIntent.CURIOUS, seed=41)).samples


def test_the_demo_ab_export_writes_both_engines_and_a_master(tmp_path):
    result = _demo("--engine", "ab", "--out", str(tmp_path), "--intent", "error", "--seed", "3")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "00_error_v1.wav", "00_error_v2.wav", "ab_all_intents.wav",
    ]
    request = VocalizationRequest(VocalIntent.ERROR, seed=3)
    with wave.open(str(tmp_path / "00_error_v1.wav"), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == synthesize(request).samples
    with wave.open(str(tmp_path / "ab_all_intents.wav"), "rb") as wav:
        master = wav.readframes(wav.getnframes())
    v1, v2 = synthesize(request).samples, synthesize_v2(request).samples
    assert master.startswith(v1) and v2 in master
    assert master.index(v2) > len(v1)  # V1 first, a pause, then V2


# --- experimental presets -------------------------------------------------

EXPERIMENTAL = ("soft", "babble", "expressive")
ALL_PRESETS = ("baseline", *EXPERIMENTAL)


def test_the_presets_are_baseline_soft_babble_and_expressive():
    assert tuple(PRESETS_V2) == ALL_PRESETS
    for name, preset in PRESETS_V2.items():
        assert preset.name == name
    assert PRESETS_V2["baseline"] == PresetV2() == BASELINE


def test_preset_selection_by_name_or_value_and_unknown_names_fail():
    assert preset_v2(" Babble ") is PRESETS_V2["babble"]
    assert preset_v2(PRESETS_V2["soft"]) is PRESETS_V2["soft"]
    with pytest.raises(ValueError, match="unknown V2 preset"):
        preset_v2("operatic")
    with pytest.raises(ValueError):
        synthesize_v2(VocalizationRequest(VocalIntent.HAPPY, seed=1), preset="operatic")


def test_baseline_is_the_v2_the_application_plays():
    """The default preset, and the engine production selects, are the same bytes."""
    for intent in ALL_INTENTS:
        request = VocalizationRequest(intent, seed=41)
        baseline = synthesize_v2(request, preset="baseline").samples
        assert synthesize_v2(request).samples == baseline
        assert synthesize(request, None, V2).samples == baseline
        assert synthesizer_for(V2)(request, VoiceIdentity()).samples == baseline


@pytest.mark.parametrize("name", EXPERIMENTAL)
def test_each_preset_is_deterministic_and_seeds_still_vary_it(name):
    request = VocalizationRequest(VocalIntent.CONFUSED, 0.8, seed=1234)
    assert synthesize_v2(request, preset=name).samples == synthesize_v2(request, preset=name).samples
    clips = {synthesize_v2(VocalizationRequest(VocalIntent.HAPPY, seed=s), preset=name).samples for s in range(5)}
    assert len(clips) == 5


def test_the_presets_sound_different_from_each_other():
    for intent in ALL_INTENTS:
        request = VocalizationRequest(intent, seed=41)
        assert len({synthesize_v2(request, preset=name).samples for name in ALL_PRESETS}) == 4, intent


@pytest.mark.parametrize("name", EXPERIMENTAL)
@pytest.mark.parametrize("intent", ALL_INTENTS)
def test_each_preset_produces_valid_bounded_unclipped_pcm(name, intent):
    for seed in (41, 42):
        clip = synthesize_v2(VocalizationRequest(intent, seed=seed), preset=name)
        assert clip.sample_rate == SAMPLE_RATE
        assert MIN_DURATION_S - 1e-3 <= clip.duration_s <= MAX_DURATION_S + 1e-3
        pcm = samples_of(clip)
        assert 0 < max(abs(x) for x in pcm) <= int(PEAK_HEADROOM * 32767) + 1
        assert pcm[0] == 0 and pcm[-1] == 0


@pytest.mark.parametrize("name", EXPERIMENTAL)
def test_each_preset_stays_bounded_at_extreme_settings(name):
    extremes = (
        VoiceIdentity(speaking_rate=0.01, pitch_variation=2.0, base_pitch_hz=700.0, brightness=1.0, volume=1.0),
        VoiceIdentity(speaking_rate=100.0, pitch_variation=2.0, base_pitch_hz=1.0, brightness=0.0, volume=1.0),
    )
    for identity in extremes:
        for intent in ALL_INTENTS:
            clip = synthesize_v2(VocalizationRequest(intent, 1.0, seed=5), identity, name)
            assert MIN_DURATION_S - 1e-3 <= clip.duration_s <= MAX_DURATION_S + 1e-3
            assert 0 < peak(clip) <= int(PEAK_HEADROOM * 32767) + 1
    silent = synthesize_v2(VocalizationRequest(VocalIntent.HAPPY, seed=1), VoiceIdentity(volume=0.0), name)
    assert peak(silent) == 0


@pytest.mark.parametrize("name", EXPERIMENTAL)
def test_each_preset_keeps_syllables_ordered_click_free_and_inside_the_clip(name):
    for intent in ALL_INTENTS:
        for seed in (41, 42, 7):
            utterance = plan_utterance_v2(VocalizationRequest(intent, 1.0, seed), VoiceIdentity(), random.Random(seed), name)
            end = 0.0
            for syllable in utterance.syllables:
                assert syllable.start_s >= end - 1e-9 and syllable.duration_s > 0.0
                assert syllable.onset in ONSETS
                end = syllable.start_s + syllable.duration_s
            assert end <= utterance.duration_s

            pcm = samples_of(render_plan_v2(utterance, random.Random(seed)))
            top = max(abs(x) for x in pcm)
            for syllable in utterance.syllables:
                first = int(syllable.start_s * SAMPLE_RATE)
                last = min(first + max(64, int(round(syllable.duration_s * SAMPLE_RATE))) - 1, len(pcm) - 1)
                assert abs(pcm[first]) <= 0.02 * top and abs(pcm[last]) <= 0.02 * top, (intent, seed)


@pytest.mark.parametrize("name", EXPERIMENTAL)
def test_each_preset_keeps_the_intent_contours(name):
    """One identity across presets: the same intents rise, fall and sit high or low."""
    for intent, rising in (
        (VocalIntent.CURIOUS, True),
        (VocalIntent.CONFUSED, True),
        (VocalIntent.ERROR, False),
        (VocalIntent.FAREWELL, False),
    ):
        for seed in range(5):
            start, _, end = plan(intent, seed, preset=name).syllables[-1].pitch_hz
            assert (end > start) is rising, (intent, seed)
    assert _mean_pitch(plan(VocalIntent.HAPPY, 9, preset=name)) > _mean_pitch(plan(VocalIntent.THINKING, 9, preset=name))


def _mean_over_intents(measure, name: str) -> float:
    return sum(measure(intent, name) for intent in ALL_INTENTS) / len(ALL_INTENTS)


def test_soft_is_darker_than_baseline():
    def brightness(intent, name):
        clip = synthesize_v2(VocalizationRequest(intent, seed=41), preset=name)
        return _share_above(_loudest_frame(samples_of(clip)), 1000.0)

    assert _mean_over_intents(brightness, "soft") < 0.75 * _mean_over_intents(brightness, "baseline")


def test_babble_has_sharper_vowels_and_more_syllables_than_baseline():
    def formant_contrast(preset):
        # With no tilt, the resonance alone: the F2 peak of "ii" over the trough below it.
        weights = _harmonic_weights(100.0, VOWELS_V2["ii"], 0.0, preset)
        return weights[22] / weights[14]  # 2300 Hz vs 1500 Hz

    assert formant_contrast(PRESETS_V2["babble"]) > 1.5 * formant_contrast(BASELINE)

    def syllables(intent, name):
        return sum(len(plan(intent, seed, preset=name).syllables) for seed in range(6)) / 6

    assert _mean_over_intents(syllables, "babble") > 1.15 * _mean_over_intents(syllables, "baseline")


def test_expressive_moves_more_but_within_its_excursion_limit():
    def span(intent, name):
        hz = [p for s in plan(intent, 3, preset=name).syllables for p in s.pitch_hz]
        return 12.0 * math.log2(max(hz) / min(hz))

    assert _mean_over_intents(span, "expressive") > 1.2 * _mean_over_intents(span, "baseline")

    limit = PRESETS_V2["expressive"].max_excursion_st
    identity = VoiceIdentity(pitch_variation=2.0)
    for intent in ALL_INTENTS:
        for p in (p for s in plan(intent, 3, identity, 1.0, "expressive").syllables for p in s.pitch_hz):
            assert abs(12.0 * math.log2(p / identity.base_pitch_hz)) <= limit + 1e-9


def test_the_demo_exports_every_preset_and_a_loudness_matched_comparison(tmp_path):
    result = _demo("--engine", "v2", "--preset", "all", "--out", str(tmp_path), "--intent", "greeting", "--seed", "41")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*ALL_PRESETS, "compare"])
    request = VocalizationRequest(VocalIntent.GREETING, seed=41)
    lengths = []
    for name in ALL_PRESETS:
        assert [p.name for p in (tmp_path / name).iterdir()] == ["00_greeting.wav"]
        with wave.open(str(tmp_path / name / "00_greeting.wav"), "rb") as wav:
            frames = wav.readframes(wav.getnframes())
        assert frames == synthesize_v2(request, preset=name).samples
        lengths.append(len(frames) // 2)
    compare = tmp_path / "compare"
    assert sorted(p.name for p in compare.iterdir()) == [
        "00_greeting_presets.wav", "all_presets_compare.wav", "order.txt",
    ]
    assert "baseline, soft, babble, expressive" in (compare / "order.txt").read_text(encoding="utf-8")

    # The comparison holds the four versions, in order, at matching loudness.
    with wave.open(str(compare / "00_greeting_presets.wav"), "rb") as wav:
        pcm = array("h", wav.readframes(wav.getnframes()))
    levels, cursor = [], 0
    for length in lengths:
        levels.append(rms(pcm[cursor:cursor + length]))
        cursor += length + int(0.4 * SAMPLE_RATE)
    assert max(levels) / min(levels) < 1.03


def test_the_demo_rejects_a_preset_without_the_v2_engine(tmp_path):
    result = _demo("--engine", "v1", "--preset", "soft", "--out", str(tmp_path))
    assert result.returncode == 2 and "--engine v2" in result.stderr
