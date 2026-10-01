# Configuration

The application has no configuration file. Runtime behavior is selected by a
small set of environment variables, a packaged expression-profile file, and
fixed tables in the desktop-action module. The notch keeps one user file of
its own, shared by `MIRA` and the standalone notch tools and described under
[Notch](#notch). Architecture context is in
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
| `MIRA_NOTCH_CONFIG` | `$XDG_CONFIG_HOME/mira/notch.json` | Notch configuration file used by `MIRA`, `MIRA-NOTCH` and `MIRA-TUNE`. `--config PATH` overrides it for the notch tools. |

The four `MIRA_OLLAMA_*`/`MIRA_LLM_*` variables only matter in LLM mode, and
the `MIRA_CAMERA_*` settings only when `MIRA_CAMERA` enables the camera.

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

## Expression profiles

`mira/config/expression_profiles.json` holds one visual profile per face
state (geometry, animation flags, blink timing, idle motion, asymmetry). It is
loaded by `mira/ui/face/expression_store.py`, resolved from the installed
package rather than the working directory.

The debug drawer's **Save** and **Reload** buttons write and read this same
packaged file. In an editable install that means the file in the repository;
there is no separate user-override location yet. Change the committed file only
intentionally.

## Notch

`MIRA`, `MIRA-NOTCH` and `MIRA-TUNE` (`python -m mira.ui.notch`) read the
notch's tuned settings from one JSON file: `MIRA_NOTCH_CONFIG`, else
`$XDG_CONFIG_HOME/mira/notch.json` (`~/.config/mira/notch.json`); the notch
tools also accept `--config PATH`. `MIRA` only reads it, at startup, and
prints which file it used. Only the tuning window's **Save** writes it. The production defaults live in code
and never change by saving; the tuning window's **Reset defaults** restores
them at run time only.

```json
{
  "version": 1,
  "compact":   {"width": 200.0, "height": 32.0, "radius": 14.0},
  "expanded":  {"width": 360.0, "height": 96.0, "radius": 24.0},
  "placement": {"offset_x": 0.0, "top_margin": 0.0},
  "eyes": {"height_share": 0.28, "top_offset": 0.0, "center_offset": 0.0,
           "spacing": 300.0, "motion_gain": 0.5},
  "animation": {"duration_ms": 200},
  "display": {"monitor": null}
}
```

Every key is required and no others are accepted. `display.monitor` is
`null` for automatic selection, or `{"name", "manufacturer", "model",
"serial"}` as Qt reports them (connector name plus EDID data; never an
enumeration index).

A missing file means the defaults. A file that cannot be read, is not JSON,
does not match the schema, or would clip the eyes is ignored with a
diagnostic, the notch starts with the defaults, and the file is left
untouched. Saving is atomic: a temporary file in the same directory replaces
the old one in a single rename.

**Placement.** `top_margin` 0 puts the notch's top edge on the selected
screen's top edge — the screen geometry, not the desktop work area below a
panel. Both offsets apply once, after centring on that screen.

**Eye bounds.** A configuration is valid only if each eye's whole motion
envelope — every pose the expression profiles' wander, drift, gaze, pulse,
deformation and asymmetry can reach — stays at least 1 px inside the notch
outline at COMPACT, EXPANDED and every size between. `height_share` sets
the eye size from the compact height and `motion_gain` how far motion moves
the eyes relative to that size. The tuning window shows an invalid
combination, does not apply it and will not save it.

**Monitor.** A connected preferred monitor is used. Otherwise, or in
automatic mode: one screen is used as is; of two, the primary (then a built-in
`eDP`/`LVDS`/`DSI` panel); of three or more, the geometrically central one. An
unplugged preferred monitor is replaced by the automatic choice until it is
plugged in again, and stays preferred.

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
