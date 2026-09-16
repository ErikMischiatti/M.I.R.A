# M.I.R.A. — Modular Interactive Robotic Agent

M.I.R.A. is a **software-first embodied AI assistant prototype** built in Python and PySide6.

The project explores how an assistant can feel more present by combining:

- an expressive animated face,
- text-based interaction,
- modular cognition,
- session memory,
- local desktop actions,
- and a scalable architecture designed to later evolve into a physical robotic platform.

Rather than starting from motors and sensors, M.I.R.A. first develops the **interaction and behavioral core** of the future system: how the agent receives input, interprets requests, selects actions, produces responses, and visually expresses its internal state.

---

## Demo

<p align="center">
  <img src="assets/mira_record.gif" alt="M.I.R.A. desktop prototype in action" width="850"/>
</p>

---

## Project Idea

M.I.R.A. is designed around the idea that an intelligent interface should not only answer, but also appear:

- **attentive** while the user interacts,
- **reactive** during processing,
- **expressive** in its responses,
- and **embodied** through motion and visible state changes.

The current implementation is a compact desktop application with:

- animated eyes in an embodied companion layout,
- a compact chat interface,
- a hidden-by-default debug drawer for expression tuning,
- intent recognition,
- session memory,
- action execution,
- and an optional local LLM-backed cognitive path through Ollama.

The long-term goal is to reuse this software core as the interaction layer of a future physical robotic assistant.

---

## Current Status

**Active software prototype**

The project currently focuses on the desktop software layer:

- interaction,
- cognition,
- expressive behavior,
- and local action execution.

Hardware integration is intentionally planned for a later stage, after the software architecture has matured.

---

## Main Features

### Expressive Face

Animated eyes with:

- blinking,
- idle motion,
- smooth state transitions,
- eyelid deformation,
- asymmetry,
- thinking drift,
- speaking pulse,
- and cursor-aware gaze behavior.

Supported expressive states currently include:

- `IDLE`
- `LISTENING`
- `THINKING`
- `SPEAKING`
- `HAPPY`
- `TIRED`
- `ANGRY`
- `CONFUSED`

### Event-Driven Interaction

A central event bus coordinates:

- user input,
- focus changes,
- processing state,
- inferred intents,
- action execution,
- response delivery,
- and visual feedback.

### Modular Cognitive Layer

The system supports interchangeable intent engines:

- a deterministic **rule-based engine**,
- and an optional **local LLM-backed engine** through Ollama.

Both produce the same normalized intent format, allowing the rest of the architecture to remain unchanged.

### Session Memory

The assistant stores:

- recent user messages,
- recent assistant responses,
- the last inferred intent,
- and lightweight contextual data for the current session.

### Local Action System

M.I.R.A. routes local actions through contracts, a source-independent execution
policy, and a registry/executor boundary. Current actions include:

- retrieving current time and date,
- repeating text,
- inspecting session memory,
- opening URLs,
- launching selected desktop applications,
- opening allowed local directories,
- showing system notifications,
- retrieving basic system information,
- and reporting the source-checkout path during development.

Read-only actions and explicitly allowed desktop side effects execute directly.
Clearing session memory is confirmation-required and remains blocked with an
explicit result until a confirmation flow is implemented.

### Debug Drawer

A developer debug drawer is available from the GUI, but hidden by default so the normal experience stays compact. It allows direct editing of:

- expression profiles,
- eye geometry,
- animation flags,
- blink timing,
- idle motion,
- asymmetry parameters.

Profiles can be saved and reloaded from configuration files without modifying the application logic.

---

## Architecture Overview

The normative Foundation v1 design, dependency map, lifecycle ownership,
embodiment boundary, action policy, and extension points are documented in
[`docs/architecture.md`](docs/architecture.md).

Runtime composition and interaction graph:

```text
main
├── application composition → core → cognition/actions/memory/domain
└── MainWindow → FaceController → EmbodimentFrame renderer
```

The package is split into technology-independent domain vocabulary, session
memory and messaging, action/cognition/core orchestration, concrete adapters,
an explicit application composition root, and Qt presentation. Automated
checkers enforce dependency direction, Qt containment, and the single semantic
state authority. A historical, non-normative snapshot remains at
[`docs/MIRA_technical_analysis.md`](docs/MIRA_technical_analysis.md).

---

## Local LLM Integration

M.I.R.A. includes an optional LLM-based intent engine using a local Ollama model.

The LLM path currently:

- receives natural language input,
- includes bounded, sanitized recent conversation context through `SessionContextBuilder`,
- keeps the current user input separate from previous conversation context in the prompt,
- converts it into a structured JSON result,
- selects a normalized intent,
- optionally proposes an available action and parameters,
- suppresses low-confidence proposed actions using `MIRA_LLM_ACTION_MIN_CONFIDENCE`,
- exposes safe fallback diagnostics through `llm_fallback_used` and `llm_fallback_reason`,
- preserves compatibility with the same backend contract used by the rule-based engine.

LLM-proposed actions are gated by `MIRA_LLM_ACTION_MIN_CONFIDENCE`. The default threshold is `0.65`; invalid values fall back to the default, and numeric values are clamped to the `0.0..1.0` range. When an LLM action is suppressed because its confidence is below the configured threshold, the intent metadata includes:

- `action_suppressed_reason = "low_confidence"`
- `action_min_confidence = <threshold>`

Implemented LLM fallback reasons are:

- `client_error`
- `invalid_response`
- `invalid_json`
- `unsupported_intent`
- `unknown_action`
- `intent_action_mismatch`
- `invalid_parameters`
- `low_confidence_action`
- `invalid_schema`

The LLM integration is still under active development. Future work focuses on:

- reducing response latency,
- extending the use of LLM-generated responses,
- adding persistent memory,
- adding UI confirmation flow for actions,
- replacing lightweight schema checks with a full JSON-schema validator dependency if needed,
- and linking LLM-derived emotional output more directly to embodied behavior.

Run the rule-based intent engine explicitly:

```bash
MIRA_INTENT_ENGINE=rule python3 -m mira.main
```

Run the LLM-backed intent engine:

```bash
MIRA_INTENT_ENGINE=llm python3 -m mira.main
```

Useful Ollama configuration variables:

```bash
MIRA_OLLAMA_MODEL=llama3.2:3b
MIRA_OLLAMA_BASE_URL=http://localhost:11434
MIRA_OLLAMA_TIMEOUT_S=10
MIRA_LLM_ACTION_MIN_CONFIDENCE=0.65
```

Without `MIRA_INTENT_ENGINE`, the system defaults to the rule-based engine.

---

## Technologies

- Python
- PySide6 / Qt
- Event-driven architecture
- Rule-based intent inference
- Local LLM integration with Ollama
- Action contracts, execution policy, registry, and executor
- Session memory
- Procedural animation
- Desktop automation
- Human-machine interaction

---

## Requirements

- Python 3.12
- PySide6
- Ollama, only required when using the optional local LLM-backed intent engine

---

## Project Structure

```text
mira/
├── domain/       # technology-independent vocabulary and playback
├── memory/       # session retention
├── messaging/    # synchronous event bus
├── actions/      # contracts, execution policy, executor, handlers
├── cognition/    # rule and optional local-LLM inference
├── core/         # turn, state, interaction, and behavior orchestration
├── adapters/     # Qt scheduler implementation
├── application/  # construction and wiring
├── ui/           # Qt presentation and face control
├── config/       # packaged expression profiles
└── main.py       # process entry point
```

See [`docs/architecture.md`](docs/architecture.md) for dependency direction and
implemented boundaries.

---

## Running the Project

### 1. Clone the repository

```bash
git clone https://github.com/ErikMischiatti/H.A.R.O..git M.I.R.A.
cd M.I.R.A.
```

### 2. Create and activate a virtual environment

```bash
python3.12 -m venv venv
source venv/bin/activate
```

### 3. Install the dependencies and the package

```bash
python -m pip install --requirement requirements.txt
```

This installs M.I.R.A. in editable mode with the exact development and CI
dependency versions recorded in `constraints.txt`. Editable installation makes
`import mira` independent of the working directory. Without it the package is
found only when the repository root
happens to be on `sys.path`, which is why `python3 -m mira.main` and
`python -m pytest` work from the repository root and nothing works from
anywhere else — those two commands put the working directory on `sys.path`,
while the `pytest` console script does not.

Editable (`-e`) keeps Python source edits immediately visible. Reinstall after
dependency or packaging metadata changes, or if the repository moves; an
ordinary source-only `git pull` needs no reinstall.

`pyproject.toml` is the single source of truth for direct runtime and
development compatibility. `constraints.txt` records the exact runtime/dev
dependency resolution tested on Python 3.12/Linux; `requirements.txt` only
applies that file and requests the `dev` extra. This makes those Python package
versions deterministic on the validated platform, but does not pin pip or the
isolated build backend and does not promise byte-identical wheels or identical
platform-specific packages on other operating systems.

For a runtime-only editable install within the supported ranges, without the
tested development lock, use:

```bash
python -m pip install --editable .
```

### Updating dependencies

Dependency upgrades are intentional changes:

1. Update a direct compatibility range in `pyproject.toml` only when support
   policy changes.
2. In a fresh Python 3.12 virtual environment, resolve the development install
   without the old constraints: `python -m pip install --editable ".[dev]"`.
3. Capture the new tested set with `python -m pip freeze --exclude-editable`,
   then replace the pinned entries in `constraints.txt` while preserving its
   explanatory header.
4. Review direct and transitive version changes.
5. Recreate clean environments through `requirements.txt` and run the complete
   validation suite before committing the update.

### 4. Run the default rule-based version

```bash
python3 -m mira.main
```

You can also select the rule engine explicitly:

```bash
MIRA_INTENT_ENGINE=rule python3 -m mira.main
```

### 5. Run with the local LLM-backed intent engine

Make sure Ollama is installed, running, and that the configured model is available.

```bash
MIRA_INTENT_ENGINE=llm python3 -m mira.main
```

Optional Ollama configuration:

```bash
MIRA_OLLAMA_MODEL=llama3.2:3b
MIRA_OLLAMA_BASE_URL=http://localhost:11434
MIRA_OLLAMA_TIMEOUT_S=10
MIRA_LLM_ACTION_MIN_CONFIDENCE=0.65
```

### 6. Optional: launch from anywhere with a single command

`bin/mira` runs the application in the repository's own virtual environment
from any working directory. Put it on your `PATH` once:

```bash
ln -s "$PWD/bin/mira" ~/.local/bin/MIRA
```

Then, from any directory:

```bash
MIRA
MIRA_INTENT_ENGINE=llm MIRA
```

The launcher resolves the repository through the symlink, so source-only pulls
need no launcher update. Dependency or packaging changes still require
reinstalling the environment; moving or renaming the repository requires
recreating the symlink.

The launcher preserves the directory from which it was called. Runtime
resources resolve from the installed package, while desktop directory actions
use explicit home, invocation-directory, and optional development-checkout
semantics. The invocation directory is context for relative paths and the
`current` alias; it does not widen the allowed-directory boundary.

Expression profiles currently use the packaged
`mira/config/expression_profiles.json` as both the active profile source and the
Save/Reload target. This preserves the debug drawer's existing persistence in
editable and other user-writable installations, but a future configuration
tranche should separate immutable bundled profiles from writable user
overrides. No user-config directory or migration is introduced here.

The project-path action is development-only. It reports the direct source
checkout containing the imported package and known project markers; a regular
installed package has no repository path and returns an unavailable result
instead of treating the invocation directory or `site-packages` as the project.

### Validation and contribution

Keep changes focused, add tests for changed behavior, and run the complete
baseline before submitting a change:

```bash
python -m compileall mira
python scripts/check_layering.py
python scripts/check_state_authority.py
QT_QPA_PLATFORM=offscreen python -m pytest
python -m pip check
git diff --check
```

---

## Roadmap

### Current

- PySide6 desktop application
- Expressive animated face
- Chat interface
- Compact embodied GUI with a hidden-by-default debug drawer
- Event bus and state manager
- Brain orchestration layer
- Session memory
- Rule-based intent engine
- Local action contracts, execution policy, registry, and executor
- Optional Ollama-backed LLM intent engine

### In Progress

- Improve LLM response latency
- Expand use of LLM-generated responses
- Better integration between cognitive output and expressive behavior

### Next

- Voice input and speech output
- Wake interaction
- Richer contextual memory
- Multimodal interaction management
- Webcam-based presence awareness

### Future

- Physical embodiment
- Sensors and actuators
- Audio hardware
- Mechanical expression
- Integration with a robotic platform

---

## Author

**Erik Mischiatti**  
M.Sc. Mechatronics Engineering

---

## License

This project is licensed under the MIT License.  
See the [LICENSE](LICENSE) file for details.
