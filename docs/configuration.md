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

| `MIRA_PERSON_DETECTION` | unset (disabled) | `1`, `true`, `yes` or `on` (case-insensitive) enables human presence perception. Needs the camera; without it perception reports `unavailable`. |
| `MIRA_PERSON_DETECTION_MODEL` | OpenCV's bundled `haarcascade_frontalface_default.xml` | Path to an OpenCV cascade XML to detect with instead. Blank selects the default. |
| `MIRA_PERSON_DETECTION_CONFIDENCE` | `0.4` | Minimum detection score in `0.0..1.0`. Non-numeric or out-of-range values fall back to the default. |

The four `MIRA_OLLAMA_*`/`MIRA_LLM_*` variables only matter in LLM mode, the
`MIRA_CAMERA_*` settings only when `MIRA_CAMERA` enables the camera, and the
`MIRA_PERSON_DETECTION_*` settings only when person detection is enabled.

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

## Human presence perception

Optional, off by default, and dependent on the camera. It uses the same
`camera` extra, which provides OpenCV and numpy:

```bash
MIRA_CAMERA=1 MIRA_PERSON_DETECTION=1 venv/bin/python -m mira.main
```

**Model.** The default is OpenCV's frontal-face Haar cascade, which ships inside
the OpenCV wheel under OpenCV's BSD-3-Clause license. Nothing is downloaded,
and no model file is stored in this repository. It finds people facing the
camera; someone turned away, in profile or in poor light may not be detected.
To try another cascade, for example the bundled upper-body model, point
`MIRA_PERSON_DETECTION_MODEL` at its XML file:

```bash
MIRA_PERSON_DETECTION_MODEL="$(venv/bin/python -c 'import cv2; print(cv2.data.haarcascades)')haarcascade_upperbody.xml"
```

**Confidence.** Cascades report how many neighbouring windows agreed, not a
probability. The score maps that onto `0.0..1.0` (twelve agreeing windows
score 1.0). Raise `MIRA_PERSON_DETECTION_CONFIDENCE` to suppress false
detections; lower it to catch weaker ones.

**Presence.** Someone counts as present after 0.3 s of continuous detection
and as gone after 1.5 s without one. These timings are fixed in code.

**Status** (debug drawer, **Camera → Human perception**):

| State | Meaning |
|---|---|
| `disabled` | `MIRA_PERSON_DETECTION` is not set. |
| `stopped` | Configured but not running. |
| `starting` | Loading the model, waiting for camera frames, or analysing the first frame (about 1.6 s of one-time OpenCV initialization). |
| `active` | Analysing frames; shows presence, results per second and inference latency. |
| `unavailable` | The camera is disabled, OpenCV is not installed, or the model file is missing or unreadable. |
| `failed` | The detector failed to load, or failed five times in a row. |

**Cost.** Every camera frame is analysed. On the development machine that was
about 30 analyses per second at 25–50 ms each, using roughly 3–4 CPU cores
through OpenCV's internal threading. No rate limit is implemented yet.
Detections are held in memory only, and only the newest; nothing is recorded.

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
