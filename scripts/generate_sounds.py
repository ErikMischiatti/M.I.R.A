"""Render MIRA's packaged UI sounds into ``mira/sounds/``.

Original and synthetic: nothing is downloaded or sampled, and only the
standard library is used, so the sound can be reproduced and changed from
this file alone. The WAV it writes is what ships; a test checks that it is
still what this script renders.

``timer_done.wav`` — the countdown has finished. A rising major arpeggio
(G5, C6, E6), about 0.8 s: friendly, recognisable and never an alarm. Each
note is a soft two-operator FM voice (modulator at twice the pitch) whose
brightness falls away quickly, so it starts like a small electronic bell
and settles into a round tone; it glides up into its pitch from a
semitone and a half below, which gives the slightly robotic, playful
"bloop" of a video-game UI. The last note rings longest with a light
vibrato. Peak -7 dBFS, silent at both ends, so it never clicks.

    venv/bin/python scripts/generate_sounds.py          # write the asset
    venv/bin/python scripts/generate_sounds.py --check  # is it up to date?
"""

from __future__ import annotations

import argparse
import math
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 44100
SOUNDS_DIR = Path(__file__).resolve().parents[1] / "mira" / "sounds"
TIMER_DONE_PATH = SOUNDS_DIR / "timer_done.wav"


@dataclass(frozen=True)
class Note:
    start_s: float
    frequency_hz: float
    level: float
    decay_s: float


TIMER_DONE_NOTES = (
    Note(0.00, 783.99, 0.80, 0.07),  # G5
    Note(0.11, 1046.50, 0.85, 0.08),  # C6
    Note(0.22, 1318.51, 1.00, 0.20),  # E6, left ringing
)
TIMER_DONE_DURATION_S = 0.8
PEAK = 10 ** (-7 / 20)

ATTACK_S = 0.006
GLIDE_SEMITONES = 1.5
GLIDE_S = 0.012
BRIGHT_INDEX = 1.2
BRIGHT_DECAY_S = 0.04
SOFT_INDEX = 0.4
VIBRATO_HZ = 7.0
VIBRATO_DEPTH = 0.004
FADE_OUT_S = 0.04


def _add_note(samples: list[float], note: Note) -> None:
    phase = 0.0
    for index in range(round(note.start_s * SAMPLE_RATE), len(samples)):
        t = index / SAMPLE_RATE - note.start_s
        glide = 2 ** (-GLIDE_SEMITONES / 12 * math.exp(-t / GLIDE_S))
        vibrato = 1 + VIBRATO_DEPTH * math.sin(2 * math.pi * VIBRATO_HZ * t) * min(t / 0.15, 1.0)
        phase += 2 * math.pi * note.frequency_hz * glide * vibrato / SAMPLE_RATE
        brightness = SOFT_INDEX + BRIGHT_INDEX * math.exp(-t / BRIGHT_DECAY_S)
        envelope = min(t / ATTACK_S, 1.0) * math.exp(-t / note.decay_s)
        samples[index] += note.level * envelope * math.sin(phase + brightness * math.sin(2 * phase))


def render_timer_done() -> list[float]:
    """The chime as samples in [-1, 1] at `SAMPLE_RATE`."""
    samples = [0.0] * round(TIMER_DONE_DURATION_S * SAMPLE_RATE)
    for note in TIMER_DONE_NOTES:
        _add_note(samples, note)
    scale = PEAK / max(abs(sample) for sample in samples)
    fade = round(FADE_OUT_S * SAMPLE_RATE)
    count = len(samples)
    for index in range(count):
        # Quadratic, so the last samples arrive at silence flat, not at a slope.
        tail = min((count - 1 - index) / fade, 1.0) ** 2
        samples[index] *= scale * tail
    return samples


def to_pcm16(samples: list[float]) -> list[int]:
    return [max(-32768, min(32767, round(sample * 32767))) for sample in samples]


def write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(value.to_bytes(2, "little", signed=True) for value in to_pcm16(samples))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(frames)


def read_pcm16(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
    return [int.from_bytes(frames[i:i + 2], "little", signed=True) for i in range(0, len(frames), 2)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only check that the packaged sound is up to date")
    arguments = parser.parse_args(argv)
    samples = render_timer_done()
    if arguments.check:
        rendered = to_pcm16(samples)
        packaged = read_pcm16(TIMER_DONE_PATH) if TIMER_DONE_PATH.is_file() else []
        if len(packaged) != len(rendered) or max(abs(a - b) for a, b in zip(rendered, packaged)) > 1:
            print(f"{TIMER_DONE_PATH} is out of date; run scripts/generate_sounds.py", file=sys.stderr)
            return 1
        print(f"{TIMER_DONE_PATH} is up to date")
        return 0
    write_wav(TIMER_DONE_PATH, samples)
    print(f"wrote {TIMER_DONE_PATH} ({len(samples) / SAMPLE_RATE:.2f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
