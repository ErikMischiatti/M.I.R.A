# Configuration

M.I.R.A. has no configuration file. Runtime behavior is selected by a small set
of environment variables, a packaged expression-profile file, and fixed tables
in the desktop-action module. Architecture context is in
[architecture.md](architecture.md).

## Environment variables

All variables are optional and are read once, when the application graph is
built. Constructor arguments (used by tests) take precedence over the
environment.

| Variable | Default | Effect |
|---|---|---|
| `MIRA_INTENT_ENGINE` | `rule` | `llm` (case-insensitive) selects `LLMIntentEngine`; any other value selects `RuleIntentEngine`. |
| `MIRA_OLLAMA_MODEL` | `llama3.2:3b` | Ollama model name. |
| `MIRA_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL; a trailing `/` is removed. |
| `MIRA_OLLAMA_TIMEOUT_S` | `10` | Per-request socket timeout in seconds. Non-numeric or non-positive values fall back to the default. |
| `MIRA_LLM_ACTION_MIN_CONFIDENCE` | `0.65` | Minimum LLM confidence for a proposed action to be kept. Non-numeric or NaN values fall back to the default; numbers are clamped to `0.0..1.0`. |

| `MIRA_CAMERA` | unset (disabled) | `1`, `true`, `yes` or `on` (case-insensitive) enables camera capture; anything else leaves it disabled. |
| `MIRA_CAMERA_DEVICE` | `0` | Camera to open: a device index such as `0`, or a device path such as `/dev/video2`. Blank selects the default. |
| `MIRA_CAMERA_WIDTH` | device default | Requested frame width in pixels. Non-integer or non-positive values are ignored. |
| `MIRA_CAMERA_HEIGHT` | device default | Requested frame height in pixels. Non-integer or non-positive values are ignored. |
| `MIRA_CAMERA_FPS` | device default | Requested frame rate. Non-numeric, non-finite or non-positive values are ignored. |
| `MIRA_VOICE` | unset (disabled) | `1`, `true`, `yes` or `on` (case-insensitive) enables the nonverbal voice; anything else leaves it disabled. |
| `MIRA_VOICE_VOLUME` | `0.5` | Peak level, `0..1`. |
| `MIRA_VOICE_PITCH` | `320` | Base pitch in Hz, clamped to `120..700`. |
| `MIRA_VOICE_RATE` | `1.0` | Speaking-rate multiplier, clamped to `0.5..2.0`. |
| `MIRA_VOICE_BRIGHTNESS` | `0.45` | Timbre, `0` soft and round to `1` crisp. |
| `MIRA_VOICE_ENGINE` | `v1` | Synthesizer: `v1`, or the experimental `v2` (case-insensitive); anything else keeps `v1`. |

The four `MIRA_OLLAMA_*`/`MIRA_LLM_*` variables only matter in LLM mode, and
the `MIRA_CAMERA_*` settings only when `MIRA_CAMERA` enables the camera, and
the `MIRA_VOICE_*` settings only when `MIRA_VOICE` enables the voice.
Non-numeric or non-finite voice values fall back to the default; numbers out of
range are clamped.

Example:

```bash
MIRA_INTENT_ENGINE=llm MIRA_OLLAMA_MODEL=llama3.2:3b MIRA_OLLAMA_TIMEOUT_S=5 \
  python -m mira.main
```

## Local LLM

LLM mode needs a running [Ollama](https://ollama.com) server with the
configured model pulled (`ollama pull llama3.2:3b`). The client calls
`POST /api/generate` with a JSON schema in the `format` field and temperature
`0`; it uses only the Python standard library.

The model's intent is restricted to the allowlist in
`mira/cognition/llm_schema.py`. A proposed action is kept only if it is a
registered action, compatible with the intent, has valid required parameters,
and meets `MIRA_LLM_ACTION_MIN_CONFIDENCE`. A suppressed low-confidence action
adds `action_suppressed_reason = "low_confidence"` and
`action_min_confidence = <threshold>` to the intent entities.

Any deviation sets `llm_fallback_used = True` and one `llm_fallback_reason`:

| Reason | Cause | Result |
|---|---|---|
| `client_error` | Connection failure or timeout | Rule engine result |
| `invalid_json` | Ollama returned unparseable JSON | Rule engine result |
| `invalid_response` | Empty or non-object response | Rule engine result |
| `unsupported_intent` | Intent outside the allowlist | Intent becomes `unknown`, no action |
| `unknown_action` | Action not registered | LLM intent kept, no action |
| `intent_action_mismatch` | Action incompatible with the intent | LLM intent kept, no action |
| `invalid_parameters` | Missing or mistyped required parameter | LLM intent kept, no action |
| `low_confidence_action` | Confidence below threshold | LLM intent kept, no action |
| `invalid_schema` | Any other validation failure | LLM intent kept, no action |

These diagnostics stay in intent metadata; they are not shown in the chat UI.

## Camera

The camera is optional and off by default. It needs the `camera` extra, which
installs `opencv-python-headless`:

```bash
venv/bin/python -m pip install --constraint constraints.txt --editable ".[dev,camera]"
MIRA_CAMERA=1 MIRA_CAMERA_DEVICE=0 venv/bin/python -m mira.main
```

Use the headless wheel. The regular `opencv-python` wheel bundles its own Qt
plugins, which conflict with PySide6 in the same process.

Requested width, height and rate are best effort; the device may choose
something else, and the status reports what it actually delivers. Capture
starts with the application, on a background thread, so opening a slow or
missing device never blocks the UI. The debug drawer's
**Camera** section shows the state:

| State | Meaning |
|---|---|
| `disabled` | `MIRA_CAMERA` is not set. |
| `stopped` | Configured but not capturing, including after a normal stop. |
| `opening` | Acquiring the device. |
| `active` | Delivering frames; shows device, size and reported rate. |
| `unavailable` | Could not be opened: missing, busy (another application holds it), not permitted, or OpenCV is not installed. The reason is shown. |
| `failed` | Opened, but reads kept failing (five in a row) or the driver raised. |

A camera that is unavailable or failed does not affect anything else. Frames
are kept in memory only, and only the newest one; nothing is recorded to disk.

## Voice

The nonverbal voice is optional and off by default. It needs no extra package:
synthesis is pure Python and playback uses Qt Multimedia, which ships with
PySide6.

```bash
MIRA_VOICE=1 venv/bin/python -m mira.main
MIRA_VOICE=1 MIRA_VOICE_PITCH=260 MIRA_VOICE_RATE=0.85 MIRA_VOICE_VOLUME=0.3 \
  venv/bin/python -m mira.main
MIRA_VOICE=1 MIRA_VOICE_ENGINE=v2 venv/bin/python -m mira.main   # experimental V2 sound
```

`MIRA_VOICE_ENGINE` alone does not enable the voice.

MIRA greets on start and reacts to each response (see
[architecture.md](architecture.md#15-nonverbal-voice) for the mapping). With
no audio device, the voice logs `[Voice] no audio output device` once and stays
silent; everything else runs unchanged.

To hear every vocalization without starting the assistant:

```bash
venv/bin/python scripts/voice_demo.py                          # all intents, in turn
venv/bin/python scripts/voice_demo.py --intent curious --repeat 3
venv/bin/python scripts/voice_demo.py --pitch 260 --rate 0.8 --brightness 0.2 --volume 0.4
venv/bin/python scripts/voice_demo.py --no-play --out voice_wavs --seed 7   # WAV files only
venv/bin/python scripts/voice_demo.py --engine v2                  # experimental V2
venv/bin/python scripts/voice_demo.py --engine ab                  # each intent: V1, then V2
```

`--engine` is `v1` (default), `v2` or `ab`. To export both engines for
listening — every intent, two seeds each:

```bash
venv/bin/python scripts/voice_demo.py --engine v2 --no-play --out mira_voice_demo_v2 --seed 41 --repeat 2
venv/bin/python scripts/voice_demo.py --engine ab --no-play --out mira_voice_demo_ab --seed 41 --repeat 2
```

The first writes `NN_<intent>.wav`, the same names V1 uses. The `ab` export
writes `NN_<intent>_v1.wav` and `NN_<intent>_v2.wav` from the same seed and
identity, plus `ab_all_intents.wav`: each pair in turn, V1 first, 0.32 s apart,
then 0.5 s before the next. Clips keep their engine's own loudness; nothing is
re-normalized.

V2 also has experimental sound-design presets, for choosing a personality by
ear. They exist only in the demo and the `synthesize_v2(..., preset=...)` API;
`MIRA_VOICE_ENGINE=v2` always plays `baseline`.

| Preset | Character |
|---|---|
| `baseline` | V2 as integrated (the default). |
| `soft` | Warm and rounded: darker tilt, broader formants, an earlier high-cut, slower vowel glides, gentler envelopes, quieter onsets, smaller pitch movement. |
| `babble` | Chattering pseudo-speech: sharper formants for clearer vowels, quicker glides, firmer soft onsets, long syllables split around a short closure, looser rhythm and pitch. |
| `expressive` | Animated: about 1.45× wider pitch gestures under a ±9-semitone limit, a stretched final syllable, livelier vibrato, accented phrase edges. |

```bash
venv/bin/python scripts/voice_demo.py --engine v2 --preset babble          # play one preset
venv/bin/python scripts/voice_demo.py --engine v2 --preset all --no-play --out mira_voice_presets --seed 41
```

The `all` export writes `mira_voice_presets/<preset>/NN_<intent>.wav` for every
preset from the same seed and identity (peak-normalized like the application),
and `mira_voice_presets/compare/`: `NN_<intent>_presets.wav` for greeting,
curious, happy and thinking — baseline, soft, babble, expressive in turn, 0.4 s
apart and RMS-matched so they compare by character rather than level — plus
`all_presets_compare.wav` and `order.txt`. `--preset` requires `--engine v2`.

`--intensity` (`0..1`) widens pitch movement and energy; `--seed` makes the
output reproducible, and leaving it out lets each utterance vary slightly.
`--variation` scales pitch movement. When playback is unavailable, the demo
writes WAV files to `mira_voice_demo/` instead.

Limitations: the sound is designed for a small expressive character, not
speech, and is tuned by ear. Clips are 0.2–1.2 s and at most one waits while
another plays; a burst of responses inside the 400 ms cooldown produces one
vocalization. Playback is not synchronized with the face's speaking motion.
Under `QT_QPA_PLATFORM=offscreen` Qt still finds a real sound card if one
exists; CI machines have none and stay silent.

## Expression profiles

`mira/config/expression_profiles.json` holds one visual profile per face
state (geometry, animation flags, blink timing, idle motion, asymmetry). It is
loaded by `mira/ui/face/expression_store.py`, resolved from the installed
package rather than the working directory.

The debug drawer's **Save** and **Reload** buttons write and read this same
packaged file. In an editable install that means the file in the repository;
there is no separate user-override location yet. Change the committed file only
intentionally.

## Desktop actions

Desktop actions (`mira/actions/desktop_actions.py`) target a Linux desktop and
degrade to an explicit failure message when a tool is missing.

| Action | Mechanism |
|---|---|
| `open_url` | Python `webbrowser`; only `http`/`https` URLs with a host are accepted, and a missing scheme becomes `https://`. |
| `open_app` | Fixed commands: `firefox`, `google-chrome`, `x-terminal-emulator`, `gnome-calculator`, and `xdg-open` for the file manager. Italian and English aliases map onto these; other names are rejected. |
| `show_notification` | `notify-send`. |
| `open_directory` | `xdg-open`, restricted to directories inside the home tree or, in a verified source checkout, the project tree. |
| `get_system_info` | Python `platform`. |
| `get_project_path` | Reports the source checkout; unavailable for a regular installed package. |

Directory aliases include `home`, `desktop`, `downloads`, `documents`
(with Italian equivalents), `project`, and `current`. `current` and relative
paths resolve against the directory the process was started from; that
directory does not widen the allowed roots.

Clearing session memory is registered but requires confirmation, which is not
implemented, so the request is always blocked with an explicit result.
