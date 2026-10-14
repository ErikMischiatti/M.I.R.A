#!/usr/bin/env python3
"""Hear (or export) MIRA's nonverbal voice without starting the assistant.

Examples, from the repository root:

    venv/bin/python scripts/voice_demo.py                      # every intent, in turn
    venv/bin/python scripts/voice_demo.py --intent curious --repeat 3
    venv/bin/python scripts/voice_demo.py --pitch 260 --rate 0.8 --brightness 0.2
    venv/bin/python scripts/voice_demo.py --no-play --out voice_wavs --seed 7
    venv/bin/python scripts/voice_demo.py --engine v2                  # experimental V2
    venv/bin/python scripts/voice_demo.py --engine ab --no-play --out voice_ab --seed 41

``--engine`` picks the synthesizer: ``v1`` (the default, as in the
application), ``v2`` (opt-in, ``MIRA_VOICE_ENGINE=v2``), or ``ab``, which renders
every request with both — same seed, same identity — and plays V1 then V2. An
``ab`` export writes ``NN_<intent>_v1.wav`` and ``NN_<intent>_v2.wav`` side by
side, plus ``ab_all_intents.wav``: each pair in turn, separated by pauses.

``--preset`` (with ``--engine v2``) picks an experimental V2 sound-design preset:
``baseline`` (the default — what the application plays), ``soft``, ``babble``,
``expressive``, or ``all``. An ``all`` export writes one folder per preset, each
with the usual ``NN_<intent>.wav`` from the same seed and identity, plus
``compare/``: for greeting, curious, happy and thinking, the four presets in turn
(baseline, soft, babble, expressive), loudness-matched, with short gaps::

    venv/bin/python scripts/voice_demo.py --engine v2 --preset all --no-play --out mira_voice_presets --seed 41

Playback goes through the same pipeline the application uses — `VoiceController`
on a `QtScheduler`, rendering off the main thread, playing through
`QtAudioOutput` — with the cooldown disabled so every request is heard. When no
audio device is available (headless machine, CI, container), the clips are
written as WAV files instead, so there is always something to listen to.
"""

from __future__ import annotations

import argparse
import math
import sys
from array import array
from collections.abc import Callable
from functools import partial
from pathlib import Path

from mira.domain.vocal_synthesis import PEAK_HEADROOM, clip_to_wav_bytes, synthesizer_for
from mira.domain.vocal_synthesis_v2 import PRESETS_V2, synthesize_v2
from mira.domain.vocalization import (
    DEFAULT_ENGINE,
    DEFAULT_INTENSITY,
    PcmClip,
    VocalIntent,
    VocalizationRequest,
    VoiceEngine,
    VoiceIdentity,
)

FALLBACK_OUT_DIR = Path("mira_voice_demo")
PAUSE_BETWEEN_MS = 350
AB = "ab"
AB_MASTER_NAME = "ab_all_intents.wav"
# Pauses in the A/B master: between V1 and V2 of one request, and after the pair.
AB_PAIR_PAUSE_S = 0.32
AB_NEXT_PAUSE_S = 0.5
ALL_PRESETS = "all"
BASELINE_PRESET = "baseline"
COMPARE_DIR = "compare"
COMPARE_MASTER_NAME = "all_presets_compare.wav"
# The intents whose presets are compared back to back.
COMPARE_INTENTS = (VocalIntent.GREETING, VocalIntent.CURIOUS, VocalIntent.HAPPY, VocalIntent.THINKING)
COMPARE_PAUSE_S = 0.4
COMPARE_NEXT_PAUSE_S = 0.9

Synthesizer = Callable[[VocalizationRequest, VoiceIdentity], PcmClip]


def parse_args(argv: list[str]) -> argparse.Namespace:
    defaults = VoiceIdentity()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--intent", default="all", choices=["all", *(i.value for i in VocalIntent)],
        help="which vocalization to produce (default: all, in order)",
    )
    parser.add_argument(
        "--engine", default=DEFAULT_ENGINE.value, choices=[*(e.value for e in VoiceEngine), AB],
        help="synthesizer: v1 (default), v2 (experimental) or ab (both, V1 then V2)",
    )
    parser.add_argument(
        "--preset", default=BASELINE_PRESET, choices=[*PRESETS_V2, ALL_PRESETS],
        help="V2 sound-design preset (with --engine v2): baseline (default), soft, babble, expressive or all",
    )
    parser.add_argument("--intensity", type=float, default=DEFAULT_INTENSITY, help="0..1")
    parser.add_argument("--seed", type=int, default=None, help="reproducible output; omit for natural variation")
    parser.add_argument("--repeat", type=int, default=1, help="utterances per intent")
    parser.add_argument("--pitch", type=float, default=defaults.base_pitch_hz, help="base pitch in Hz")
    parser.add_argument("--variation", type=float, default=defaults.pitch_variation, help="pitch movement multiplier")
    parser.add_argument("--rate", type=float, default=defaults.speaking_rate, help="speaking-rate multiplier")
    parser.add_argument("--brightness", type=float, default=defaults.brightness, help="timbre, 0 soft .. 1 crisp")
    parser.add_argument("--volume", type=float, default=defaults.volume, help="peak level, 0..1")
    parser.add_argument("--out", type=Path, default=None, help="also write WAV files to this directory")
    parser.add_argument("--no-play", action="store_true", help="do not open an audio device")
    args = parser.parse_args(argv)
    if not 0.0 <= args.intensity <= 1.0:
        parser.error("--intensity must be within 0..1")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if args.preset != BASELINE_PRESET and args.engine != VoiceEngine.V2.value:
        parser.error("--preset applies to --engine v2 only")
    return args


def build_requests(args: argparse.Namespace) -> list[VocalizationRequest]:
    intents = list(VocalIntent) if args.intent == "all" else [VocalIntent(args.intent)]
    requests = []
    for intent in intents:
        for index in range(args.repeat):
            seed = None if args.seed is None else args.seed + index
            requests.append(VocalizationRequest(intent, args.intensity, seed))
    return requests


def variants_for(engine: str, preset: str = BASELINE_PRESET) -> list[tuple[str, Synthesizer]]:
    """The labelled synthesizers one request is rendered with, in listening order."""
    if engine == AB:
        return [(each.value, synthesizer_for(each)) for each in VoiceEngine]
    if engine != VoiceEngine.V2.value:
        return [(engine, synthesizer_for(VoiceEngine(engine)))]
    names = list(PRESETS_V2) if preset == ALL_PRESETS else [preset]
    return [
        (name, synthesizer_for(VoiceEngine.V2) if name == BASELINE_PRESET else partial(synthesize_v2, preset=name))
        for name in names
    ]


def export(
    requests: list[VocalizationRequest],
    identity: VoiceIdentity,
    directory: Path,
    engine: str = DEFAULT_ENGINE.value,
    preset: str = BASELINE_PRESET,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    variants = variants_for(engine, preset)
    by_preset = preset == ALL_PRESETS
    master: list[PcmClip] = []
    groups: list[tuple[str, list[PcmClip]]] = []
    for index, request in enumerate(requests):
        group = []
        for label, synthesizer in variants:
            clip = synthesizer(request, identity)
            folder = directory / label if by_preset else directory
            folder.mkdir(exist_ok=True)
            suffix = f"_{label}" if engine == AB else ""
            path = folder / f"{index:02d}_{request.intent.value}{suffix}.wav"
            path.write_bytes(clip_to_wav_bytes(clip))
            print(f"wrote {path}  ({clip.duration_s:.2f} s)")
            master.append(clip)
            group.append(clip)
        if request.intent in COMPARE_INTENTS:
            groups.append((f"{index:02d}_{request.intent.value}", group))
    if engine == AB:
        path = directory / AB_MASTER_NAME
        path.write_bytes(clip_to_wav_bytes(_ab_master(master, len(variants))))
        print(f"wrote {path}  (each request: {' then '.join(label for label, _ in variants)})")
    if by_preset and groups:
        _export_comparison(groups, [label for label, _ in variants], directory / COMPARE_DIR)


def _export_comparison(groups: list[tuple[str, list[PcmClip]]], labels: list[str], directory: Path) -> None:
    """One loudness-matched file per compared request, and all of them in one."""
    directory.mkdir(exist_ok=True)
    everything: list[PcmClip] = []
    for name, clips in groups:
        matched = _rms_matched(clips)
        path = directory / f"{name}_presets.wav"
        path.write_bytes(clip_to_wav_bytes(_joined(matched, COMPARE_PAUSE_S, COMPARE_NEXT_PAUSE_S)))
        print(f"wrote {path}  ({' → '.join(labels)})")
        everything.extend(matched)
    path = directory / COMPARE_MASTER_NAME
    path.write_bytes(clip_to_wav_bytes(_joined(everything, COMPARE_PAUSE_S, COMPARE_NEXT_PAUSE_S, len(labels))))
    order = directory / "order.txt"
    order.write_text(
        f"Each comparison plays, in order: {', '.join(labels)}.\n"
        f"{COMPARE_MASTER_NAME}: {', '.join(name for name, _ in groups)}, in turn.\n"
        "Clips are RMS-matched within each group; the per-preset folders are not.\n",
        encoding="utf-8",
    )
    print(f"wrote {path} and {order}")


def _rms(pcm: array) -> float:
    return math.sqrt(sum(x * x for x in pcm) / max(1, len(pcm)))


def _rms_matched(clips: list[PcmClip]) -> list[PcmClip]:
    """Scale each clip to the group's mean RMS, never above the synthesizers' peak headroom.

    Presets differ in timbre, so equal peaks are not equal loudness; matching RMS
    lets a listener compare character rather than level.
    """
    pcms = []
    for clip in clips:
        pcm = array("h")
        pcm.frombytes(clip.samples)
        pcms.append(pcm)
    levels = [_rms(pcm) for pcm in pcms]
    target = sum(levels) / len(levels)
    ceiling = PEAK_HEADROOM * 32767
    matched = []
    for clip, pcm, level in zip(clips, pcms, levels):
        top = max((abs(x) for x in pcm), default=0)
        if level <= 0.0 or top == 0:
            matched.append(clip)
            continue
        gain = min(target / level, ceiling / top)
        scaled = array("h", (int(round(x * gain)) for x in pcm))
        matched.append(PcmClip(sample_rate=clip.sample_rate, samples=scaled.tobytes()))
    return matched


def _joined(clips: list[PcmClip], pause_s: float, next_pause_s: float, per_group: int | None = None) -> PcmClip:
    """Clips in turn: ``pause_s`` between them, ``next_pause_s`` after each group."""
    per_group = per_group or len(clips)
    sample_rate = clips[0].sample_rate
    parts = []
    for index, clip in enumerate(clips):
        last_of_group = index % per_group == per_group - 1
        parts.append(clip.samples)
        seconds = next_pause_s if last_of_group else pause_s
        parts.append(bytes(PcmClip.SAMPLE_WIDTH_BYTES * int(seconds * sample_rate)))
    return PcmClip(sample_rate=sample_rate, samples=b"".join(parts))


def _ab_master(clips: list[PcmClip], per_request: int) -> PcmClip:
    """Concatenate clips, a short pause inside each A/B group, a longer one after."""
    return _joined(clips, AB_PAIR_PAUSE_S, AB_NEXT_PAUSE_S, per_request)


def play(
    requests: list[VocalizationRequest],
    identity: VoiceIdentity,
    engine: str = DEFAULT_ENGINE.value,
    preset: str = BASELINE_PRESET,
) -> bool:
    """Play every request in order, once per variant. False if there is no audio output."""
    from PySide6.QtCore import QCoreApplication, QTimer

    from mira.adapters.qt_audio import QtAudioOutput
    from mira.adapters.qt_scheduler import QtScheduler
    from mira.core.voice import VoiceController

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    output = QtAudioOutput()
    if not output.available:
        print(f"no audio playback: {output.unavailable_reason}")
        return False

    scheduler = QtScheduler()
    # One controller per variant, sharing the output; only one is ever busy, so
    # clips still never overlap. Closing the shared output twice is harmless.
    voices = {
        label: VoiceController(
            scheduler, output, enabled=True, identity=identity,
            cooldown_ms=0, max_queue=0, gap_ms=PAUSE_BETWEEN_MS,
            synthesizer=synthesizer,
        )
        for label, synthesizer in variants_for(engine, preset)
    }
    pending = [(request, each) for request in requests for each in voices]

    def step() -> None:
        if any(voice.busy for voice in voices.values()):
            return
        if not pending:
            poll.stop()
            for voice in voices.values():
                voice.shutdown()
            scheduler.shutdown()
            app.quit()
            return
        request, each = pending.pop(0)
        print(f"♪ {request.intent.value} ({each})")
        voices[each].vocalize(request.intent, request.intensity, request.seed)

    poll = QTimer()
    poll.timeout.connect(step)
    poll.start(30)
    app.exec()
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    identity = VoiceIdentity(
        base_pitch_hz=args.pitch,
        pitch_variation=args.variation,
        speaking_rate=args.rate,
        brightness=args.brightness,
        volume=args.volume,
    ).bounded()
    print(f"voice: {identity}, engine: {args.engine}, preset: {args.preset}")
    requests = build_requests(args)

    if args.out is not None:
        export(requests, identity, args.out, args.engine, args.preset)

    if args.no_play:
        if args.out is None:
            print("nothing to do: --no-play without --out")
            return 2
        return 0

    if not play(requests, identity, args.engine, args.preset) and args.out is None:
        print(f"exporting WAV files instead, to {FALLBACK_OUT_DIR}/")
        export(requests, identity, FALLBACK_OUT_DIR, args.engine, args.preset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
