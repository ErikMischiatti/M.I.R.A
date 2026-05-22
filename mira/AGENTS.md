# M.I.R.A. — Agent Instructions

M.I.R.A. is a modular interactive robotic assistant developed software-first and later intended for physical embodiment.

The project is currently a Python + PySide6 embodied desktop assistant prototype with:
- expressive animated eyes,
- compact chat-based interaction,
- event-driven state management,
- modular cognition,
- optional local LLM integration through Ollama,
- session memory,
- and local action execution.

## Project goals

- Build a Python + PySide6 desktop embodied assistant.
- Keep the architecture modular and scalable.
- Support both rule-based and local LLM-backed cognition.
- Keep local LLM integration optional.
- Preserve responsive UI behavior.
- Provide expressive embodied feedback through animated face states.
- Support safe local actions through an explicit action registry.
- Prepare the software architecture for future robotic embodiment.

## Architecture

Main modules:

- `mira/core`
  - event bus
  - state manager
  - brain orchestration
  - session memory
  - interaction manager
  - embodied behavior

- `mira/cognition`
  - intent engine abstraction
  - rule-based intent engine
  - LLM intent engine
  - Ollama client
  - LLM schemas
  - response builder

- `mira/actions`
  - action models
  - action registry
  - action executor
  - built-in actions
  - desktop actions

- `mira/ui`
  - main window
  - compact chat panel
  - debug drawer
  - expressive face widget
  - face controller
  - expression profiles and rendering support

- `mira/config`
  - expression profile configuration

## Development rules

- Keep the architecture modular.
- Do not couple UI directly to LLM logic.
- Do not couple cognition directly to PySide6 widgets.
- Prefer small, testable functions.
- Keep local LLM integration optional.
- Preserve fallback to the rule-based intent engine.
- Avoid blocking the PySide6 UI thread.
- Keep long-running work out of the main UI thread.
- Add or update tests when modifying cognition, actions, or response-building behavior.
- Use clear logging for state transitions, LLM failures, fallback behavior, and stale async results.
- Keep changes focused and reviewable.
- Do not introduce external dependencies unless clearly justified.
- Do not modify `expression_profiles.json` unless the change is intentional and explained.

## Async LLM rules

`Brain.process_text_async()` must remain UI-safe.

Worker threads may:
- run intent inference,
- build/propose an optional `ActionRequest`.

Worker threads must not:
- execute actions,
- mutate `SessionMemory`,
- emit `EventBus` events,
- call `StateManager.set_state()`,
- call UI callbacks,
- touch PySide6 widgets.

Main-thread finalization must:
- validate request ordering before side effects,
- reject stale results,
- update `SessionMemory`,
- emit events,
- execute actions through `ActionExecutor`,
- build the final `BrainResponse`,
- update face state,
- call the UI response callback.

A stale LLM result must never execute actions or cause local side effects.

## LLM configuration

The default engine is rule-based.

Use LLM mode with:

```bash
MIRA_INTENT_ENGINE=llm python3 -m mira.main
```

Supported Ollama environment variables:

```bash
MIRA_OLLAMA_MODEL
MIRA_OLLAMA_BASE_URL
MIRA_OLLAMA_TIMEOUT_S
```

The LLM path must preserve fallback to `RuleIntentEngine` on Ollama failure or timeout.

## Response behavior

`ResponseBuilder` may use LLM-provided fields only through safe checks.

Rules:
- Action responses have priority over LLM conversational text.
- `llm_response_text` may be used only for non-action responses.
- `empty_input` remains deterministic.
- `llm_emotion` must be mapped to `FaceState` through an explicit allowlist.
- Do not use unchecked dynamic enum lookup for emotions.
- Do not expose raw LLM JSON or internal metadata in the UI.
- Successful actions should produce clear user-facing feedback.
- Failed or unsupported actions must fail safely and produce clear user-facing feedback.

## Local desktop actions

- All desktop actions must be registered through `ActionRegistry` and executed through `ActionExecutor`.
- Keep desktop actions narrow, explicit, and safe by default.
- UI classes may display action lifecycle feedback from events, but must not execute actions directly.

## GUI rules

The GUI should feel like a compact embodied assistant, not a bulky debug tool.

Current intended behavior:
- `FaceWidget` and `ChatPanel` form the main compact companion layout.
- `DebugPanel` is hidden by default.
- The debug drawer opens inside the existing window space.
- Opening/closing debug must not resize the top-level window.
- Face rendering must remain stable when resizing or toggling debug.
- Eyes must not stretch or deform due to window aspect ratio changes.
- Manual window resizing must remain possible.

Do not move LLM/cognition logic into UI classes.

## Runtime commands

Run in rule mode:

```bash
MIRA_INTENT_ENGINE=rule python3 -m mira.main
```

Run in LLM mode:

```bash
MIRA_INTENT_ENGINE=llm python3 -m mira.main
```

Run in LLM mode with short timeout:

```bash
MIRA_INTENT_ENGINE=llm MIRA_OLLAMA_TIMEOUT_S=1 python3 -m mira.main
```

## Validation commands

Use these after changes:

```bash
python3 -m compileall mira
git diff --check
```

If tests exist:

```bash
python3 -m pytest
```

For UI changes, also manually verify:
- app startup,
- chat submission,
- face state changes,
- debug drawer open/close,
- window resizing,
- stable eye proportions.

For LLM changes, manually verify:
- rule mode still works,
- LLM mode works,
- Ollama timeout falls back cleanly,
- UI remains responsive while LLM inference is pending.

## Git workflow

Before editing:
- run `git status --short`,
- inspect relevant files,
- explain the intended patch,
- keep the change focused.

After editing:
- summarize changed files,
- run validation,
- provide manual test steps,
- do not commit or push unless explicitly asked.

## Current focus

Current main focus areas:

- Polish compact GUI and embodied assistant UX.
- Keep debug tools available without dominating the normal UI.
- Improve local LLM behavior while preserving responsiveness.
- Prepare for future session-context integration in the LLM prompt.
- Keep the codebase clean, modular, and easy to extend.
