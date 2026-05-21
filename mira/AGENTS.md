# M.I.R.A. — Agent Instructions

M.I.R.A. is a modular interactive robotic assistant developed software-first and later intended for physical embodiment.

## Project goals

- Python + PySide6 desktop assistant
- Event-driven architecture
- Modular cognition layer
- Pluggable intent engines
- Optional local LLM via Ollama
- Expressive embodied UI
- Action execution on the local system

## Architecture

Main modules:

- `mira/core`: event bus, state manager, brain orchestration, session memory
- `mira/cognition`: intent engines and response generation
- `mira/actions`: action registry and action executor
- `mira/ui`: PySide6 interface, face widget, debug panel

## Development rules

- Keep the architecture modular.
- Do not couple UI directly to LLM logic.
- Prefer small, testable functions.
- Keep local LLM integration optional.
- Preserve fallback to rule-based intent engine.
- Avoid blocking the UI thread.
- Add tests when modifying cognition or actions.
- Use clear logging for state transitions and LLM failures.

## Current focus

Improve LLM integration by:

- reducing response latency
- avoiding long THINKING states
- handling Ollama timeout gracefully
- supporting streaming or partial response updates
- improving fallback behavior