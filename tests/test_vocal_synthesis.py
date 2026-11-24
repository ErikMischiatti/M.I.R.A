"""The nonverbal voice's vocabulary and synthesizer, without any audio device.

Everything here is pure computation: requests, identities, plans and PCM bytes.
No test plays a sound.
"""

from __future__ import annotations

import io
import math
import random
import wave
from array import array

import pytest

from mira.domain.vocal_synthesis import (
    MAX_DURATION_S,
    MIN_DURATION_S,
    PEAK_HEADROOM,
    PROSODY,
    SAMPLE_RATE,
    Onset,
    clip_to_wav_bytes,
    plan_utterance,
    prosody_for,
    synthesize,
)
from mira.domain.vocalization import (
    PcmClip,
    VocalIntent,
    VocalizationRequest,
    VoiceIdentity,
)

ALL_INTENTS = tuple(VocalIntent)


def samples_of(clip: PcmClip) -> array:
    pcm = array("h")
    pcm.frombytes(clip.samples)
    return pcm


def peak(clip: PcmClip) -> int:
    return max(abs(x) for x in samples_of(clip))


def rms(values) -> float:
    values = list(values)
    return math.sqrt(sum(v * v for v in values) / len(values))


# --- intent model -------------------------------------------------------


def test_every_intent_has_a_prosody_row():
    assert set(PROSODY) == set(VocalIntent)


@pytest.mark.parametrize("intent", ALL_INTENTS)
def test_each_intent_maps_to_a_valid_bounded_request(intent):
    prosody = prosody_for(intent)
    low, high = prosody.syllables
    assert 1 <= low <= high <= 6
    assert 0.0 < prosody.energy <= 1.0
    assert 0.0 <= prosody.consonant_chance <= 1.0
    assert prosody.vowels and all(v in "aeioum" for v in prosody.vowels)
    assert all(abs(st) <= 12 for st in prosody.contour_st + (prosody.final_glide_st,))

    for intensity in (0.0, 0.5, 1.0):
        plan = plan_utterance(
            VocalizationRequest(intent, intensity, seed=3), VoiceIdentity(), random.Random(3)
        )
        assert low <= len(plan.syllables) <= high
        assert MIN_DURATION_S <= plan.duration_s <= MAX_DURATION_S + 1e-9


@pytest.mark.parametrize("intensity", [-0.1, 1.1, math.nan, math.inf])
def test_a_request_rejects_intensity_outside_the_unit_range(intensity):
    with pytest.raises(ValueError):
        VocalizationRequest(VocalIntent.HAPPY, intensity)


def test_a_request_rejects_an_unknown_intent():
    with pytest.raises(ValueError):
        VocalizationRequest("sing")  # type: ignore[arg-type]


def test_identity_bounding_clamps_and_replaces_non_finite_values():
    wild = VoiceIdentity(
        base_pitch_hz=20_000.0, pitch_variation=-3.0, speaking_rate=math.nan,
        brightness=5.0, volume=math.inf,
    ).bounded()
    assert wild.base_pitch_hz == VoiceIdentity.PITCH_RANGE_HZ[1]
    assert wild.pitch_variation == 0.0
    assert wild.speaking_rate == VoiceIdentity().speaking_rate
    assert wild.brightness == 1.0
    assert wild.volume == VoiceIdentity().volume


# --- PCM validity -------------------------------------------------------


@pytest.mark.parametrize("intent", ALL_INTENTS)
def test_synthesis_produces_valid_bounded_pcm(intent):
    clip = synthesize(VocalizationRequest(intent, seed=11))
    assert clip.sample_rate == SAMPLE_RATE
    assert clip.SAMPLE_WIDTH_BYTES == 2 and clip.CHANNELS == 1
    assert clip.frame_count > 0
    assert MIN_DURATION_S - 1e-3 <= clip.duration_s <= MAX_DURATION_S + 1e-3

    pcm = samples_of(clip)
    top = max(abs(x) for x in pcm)
    assert 0 < top <= int(PEAK_HEADROOM * 32767) + 1
    # Fades: both ends start and finish near silence, so nothing clicks.
    assert abs(pcm[0]) < 200 and abs(pcm[-1]) < 200


def test_the_wav_encoding_round_trips():
    clip = synthesize(VocalizationRequest(VocalIntent.GREETING, seed=2))
    with wave.open(io.BytesIO(clip_to_wav_bytes(clip)), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.readframes(wav.getnframes()) == clip.samples


def test_zero_volume_is_silence_not_noise():
    clip = synthesize(VocalizationRequest(VocalIntent.HAPPY, seed=1), VoiceIdentity(volume=0.0))
    assert clip.frame_count > 0
    assert peak(clip) == 0


@pytest.mark.parametrize("rate", [0.5, 2.0, 100.0, 0.01])
def test_extreme_rates_stay_within_the_duration_bounds(rate):
    for intent in ALL_INTENTS:
        clip = synthesize(VocalizationRequest(intent, 1.0, seed=5), VoiceIdentity(speaking_rate=rate))
        assert MIN_DURATION_S - 1e-3 <= clip.duration_s <= MAX_DURATION_S + 1e-3


def test_extreme_pitches_still_render_finite_bounded_audio():
    for pitch in (1.0, 120.0, 700.0, 10_000.0):
        clip = synthesize(
            VocalizationRequest(VocalIntent.CURIOUS, 1.0, seed=8), VoiceIdentity(base_pitch_hz=pitch)
        )
        assert 0 < peak(clip) <= 32767


def test_syllables_are_ordered_non_overlapping_and_inside_the_clip():
    for intent in ALL_INTENTS:
        for seed in range(10):
            plan = plan_utterance(
                VocalizationRequest(intent, 1.0, seed), VoiceIdentity(), random.Random(seed)
            )
            end = 0.0
            for syllable in plan.syllables:
                assert syllable.start_s >= end - 1e-9
                assert syllable.duration_s > 0.0
                assert syllable.onset in (Onset.NONE, Onset.PLOSIVE, Onset.FRICATIVE)
                end = syllable.start_s + syllable.duration_s
            assert end <= plan.duration_s


# --- determinism and parameter effects ----------------------------------


def test_the_same_seed_reproduces_the_same_bytes():
    request = VocalizationRequest(VocalIntent.CONFUSED, 0.8, seed=1234)
    assert synthesize(request).samples == synthesize(request).samples


def test_different_seeds_vary_the_utterance():
    clips = {synthesize(VocalizationRequest(VocalIntent.HAPPY, seed=s)).samples for s in range(5)}
    assert len(clips) == 5


def test_intents_are_audibly_different_for_the_same_seed():
    clips = {synthesize(VocalizationRequest(i, seed=7)).samples for i in ALL_INTENTS}
    assert len(clips) == len(ALL_INTENTS)


def _mean_pitch(intent: VocalIntent, identity: VoiceIdentity, intensity: float = 0.6) -> float:
    plan = plan_utterance(VocalizationRequest(intent, intensity, 9), identity, random.Random(9))
    return sum(s.pitch_start_hz + s.pitch_end_hz for s in plan.syllables) / (2 * len(plan.syllables))


def test_base_pitch_moves_every_syllable():
    low = _mean_pitch(VocalIntent.GREETING, VoiceIdentity(base_pitch_hz=200.0))
    high = _mean_pitch(VocalIntent.GREETING, VoiceIdentity(base_pitch_hz=400.0))
    assert high == pytest.approx(2 * low, rel=0.05)


def test_happy_sits_higher_than_thinking():
    identity = VoiceIdentity()
    assert _mean_pitch(VocalIntent.HAPPY, identity) > _mean_pitch(VocalIntent.THINKING, identity)


def test_question_like_intents_end_rising_and_closing_ones_end_falling():
    for intent, rising in (
        (VocalIntent.CURIOUS, True),
        (VocalIntent.CONFUSED, True),
        (VocalIntent.ERROR, False),
        (VocalIntent.FAREWELL, False),
    ):
        for seed in range(5):
            plan = plan_utterance(
                VocalizationRequest(intent, 0.6, seed), VoiceIdentity(), random.Random(seed)
            )
            last = plan.syllables[-1]
            assert (last.pitch_end_hz > last.pitch_start_hz) is rising, (intent, seed)


def test_speaking_rate_shortens_the_utterance():
    request = VocalizationRequest(VocalIntent.FAREWELL, seed=4)
    slow = synthesize(request, VoiceIdentity(speaking_rate=0.7))
    fast = synthesize(request, VoiceIdentity(speaking_rate=1.6))
    assert fast.duration_s < slow.duration_s


def test_volume_scales_the_peak():
    request = VocalizationRequest(VocalIntent.GREETING, seed=4)
    quiet = peak(synthesize(request, VoiceIdentity(volume=0.2)))
    loud = peak(synthesize(request, VoiceIdentity(volume=0.8)))
    assert loud == pytest.approx(4 * quiet, rel=0.02)


def test_pitch_variation_zero_flattens_the_contour():
    flat = plan_utterance(
        VocalizationRequest(VocalIntent.HAPPY, 1.0, 1), VoiceIdentity(pitch_variation=0.0), random.Random(1)
    )
    assert len({round(s.pitch_start_hz, 6) for s in flat.syllables}) == 1


def _high_frequency_energy(clip: PcmClip) -> float:
    pcm = samples_of(clip)
    # First difference as a crude high-pass: brighter timbre, more of it.
    return rms(pcm[i] - pcm[i - 1] for i in range(1, len(pcm))) / rms(pcm)


def test_brightness_raises_high_frequency_content():
    request = VocalizationRequest(VocalIntent.ACKNOWLEDGEMENT, seed=6)
    dull = _high_frequency_energy(synthesize(request, VoiceIdentity(brightness=0.0)))
    bright = _high_frequency_energy(synthesize(request, VoiceIdentity(brightness=1.0)))
    assert bright > dull * 1.1


def test_intensity_widens_pitch_movement():
    def spread(intensity: float) -> float:
        plan = plan_utterance(
            VocalizationRequest(VocalIntent.HAPPY, intensity, 2), VoiceIdentity(), random.Random(2)
        )
        pitches = [p for s in plan.syllables for p in (s.pitch_start_hz, s.pitch_end_hz)]
        return max(pitches) / min(pitches)

    assert spread(1.0) > spread(0.0)


# --- standalone demo ------------------------------------------------------


def test_the_demo_exports_wav_files_without_an_audio_device(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "voice_demo.py"
    result = subprocess.run(
        [sys.executable, str(script), "--no-play", "--out", str(tmp_path),
         "--intent", "happy", "--repeat", "2", "--seed", "1"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["00_happy.wav", "01_happy.wav"]
    with wave.open(str(tmp_path / files[0]), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE and wav.getnframes() > 0
