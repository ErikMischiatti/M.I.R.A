# Development

How to set up, run, validate, and change M.I.R.A. from source. The system
design is in [architecture.md](architecture.md); runtime options are in
[configuration.md](configuration.md). Contributor and agent rules for the
codebase are in [`mira/AGENTS.md`](../mira/AGENTS.md).

**Contents:** [Environment](#environment) · [Running](#running) ·
[Repository layout](#repository-layout) · [Validation](#validation) ·
[Tests](#tests) · [Dependencies](#dependencies) · [Conventions](#conventions)

## Environment

Supported: Python 3.12 on Linux (the only combination CI validates;
`requires-python` is `>=3.12,<3.13`). Qt needs the EGL runtime; on Ubuntu,
`sudo apt-get install libegl1` if PySide6 fails to load.

```bash
git clone https://github.com/ErikMischiatti/H.A.R.O..git M.I.R.A.
cd M.I.R.A.
python3.12 -m venv venv
venv/bin/python -m pip install --requirement requirements.txt
```

`requirements.txt` installs the package in editable mode with the `dev` extra,
constrained to the exact versions in `constraints.txt`. The editable install is
what makes `import mira` independent of the working directory: without it the
package resolves only when the repository root happens to be on `sys.path`
(true for `python -m ...` run from the root, false for the `pytest` console
script or any other directory).

Editable mode keeps source edits live. Reinstall after dependency or packaging
metadata changes, or after moving the repository; a source-only `git pull`
needs no reinstall.

For a runtime-only editable install within the supported ranges, without the
tested lock or pytest:

```bash
venv/bin/python -m pip install --editable .
```

## Running

```bash
venv/bin/python -m mira.main                          # rule-based engine
MIRA_INTENT_ENGINE=llm venv/bin/python -m mira.main   # local Ollama engine
```

### Launcher

`bin/mira` runs the application with the repository's `venv` from any working
directory, and prints setup instructions if `venv` is missing. Link it onto
`PATH` once:

```bash
ln -s "$PWD/bin/mira" ~/.local/bin/MIRA
MIRA
MIRA_INTENT_ENGINE=llm MIRA
```

The launcher resolves the repository through the symlink, so source-only pulls
need no launcher update; moving or renaming the repository requires recreating
the symlink. It preserves the caller's working directory, which the desktop
actions use for the `current` alias and relative paths (see
[configuration.md](configuration.md#desktop-actions)).

## Repository layout

```text
mira/
├── domain/       # technology-independent vocabulary, scheduler port, embodiment playback
├── memory/       # session retention
├── messaging/    # synchronous event bus
├── actions/      # contracts, execution policy, registry, executor, handlers
├── cognition/    # rule and optional local-LLM inference, response building
├── core/         # turn, activity, interaction, and behavior orchestration
├── adapters/     # Qt scheduler implementation
├── application/  # construction and wiring
├── ui/           # Qt window, chat panel, debug drawer, face/ rendering
├── config/       # packaged expression profiles
└── main.py       # process entry point
bin/mira          # launcher script
scripts/          # architecture checkers
tests/            # pytest suite
docs/             # technical documentation
```

Each package's allowed dependencies are in
[architecture.md](architecture.md#2-layer-map).

## Validation

Run the full baseline before submitting a change:

```bash
venv/bin/python -m compileall mira
venv/bin/python scripts/check_layering.py
venv/bin/python scripts/check_state_authority.py
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest
venv/bin/python -m pip check
git diff --check
```

CI (`.github/workflows/ci.yml`) runs the first four on Ubuntu 24.04 with
Python 3.12 after installing through `requirements.txt`. No linter or type
checker is configured.

`QT_QPA_PLATFORM=offscreen` lets Qt tests run without a display. UI changes
still need a manual check: startup, chat submission, face state changes, debug
drawer open/close without resizing the window, and stable eye proportions when
resizing. LLM changes need rule mode, LLM mode, and a forced timeout
(`MIRA_OLLAMA_TIMEOUT_S=1` with Ollama stopped) checked for a clean fallback
and a responsive UI.

## Tests

`pyproject.toml` sets `testpaths = ["tests"]`, so `pytest` collects the same
suite from any directory. `tests/conftest.py` clears `MIRA_*` environment
variables for every test, so a developer's shell settings cannot leak in.

Tests are grouped by boundary rather than by file:

| Area | Examples |
|---|---|
| Architecture rules | `test_layering.py`, `test_activity_state_authority.py`, `test_memory_messaging_boundaries.py` |
| Composition and lifecycle | `test_application_composition.py`, `test_runtime_lifecycle.py`, `test_runtime_paths.py` |
| Scheduling | `test_scheduler.py`, `test_scheduler_contracts.py`, `test_qt_scheduler_adapter.py`, `test_brain_async_contract.py` |
| Cognition | `test_rule_intent_engine.py`, `test_llm_intent_engine.py`, `test_session_context_builder.py`, `test_response_builder.py` |
| Actions | `test_action_executor.py`, `test_action_execution_policy.py`, `test_action_contract_consistency.py`, `test_desktop_actions.py` |
| Embodiment | `test_embodiment_*.py`, `test_expression_store.py` |
| Memory and messaging | `test_memory_contracts.py`, `test_messaging_contracts.py` |

Shared test doubles live in `tests/doubles.py` (covered by
`test_shared_fixtures.py`); `tests/layering_harness.py` supports the checker
tests. `ManualScheduler` from `mira.domain.scheduler` drives full turns
deterministically without a Qt event loop. No test contacts a real Ollama
server.

## Dependencies

`pyproject.toml` is the single source of direct runtime and development
compatibility ranges. `constraints.txt` records the exact resolution tested on
Python 3.12/Linux; `requirements.txt` only applies it and requests the `dev`
extra. This pins Python package versions on the validated platform; it does not
pin pip or the build backend, or promise identical packages elsewhere.

Dependency upgrades are intentional changes:

1. Change a range in `pyproject.toml` only when support policy changes.
2. In a fresh Python 3.12 environment, resolve without the old constraints:
   `python -m pip install --editable ".[dev]"`.
3. Capture the new set with `python -m pip freeze --exclude-editable` and
   replace the pinned entries in `constraints.txt`, keeping its header.
4. Review direct and transitive version changes.
5. Recreate a clean environment through `requirements.txt` and run the full
   [validation](#validation) baseline before committing.

## Conventions

- Keep changes focused and add tests for changed behavior.
- Respect the layer map; never add an exception to either checker.
- Keep Qt out of every layer except `ui`, `adapters`, and `mira/main.py`.
- Keep long-running work off the UI thread; worker code must not cause side
  effects (see [architecture.md](architecture.md#4-interaction-flow)).
- Register every action with an explicit contract.
- Do not change `mira/config/expression_profiles.json` unintentionally.
