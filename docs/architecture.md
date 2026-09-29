# M.I.R.A. Foundation v1 architecture

This is the normative architecture snapshot for the implemented Foundation v1
baseline. It describes current code first; the final section lists conceptual
extension directions and does not claim that those APIs or capabilities exist.

For setup and validation commands see [development.md](development.md); for
environment variables and runtime resources see
[configuration.md](configuration.md).

**Contents:** [Principles](#1-architectural-principles) ·
[Layers](#2-layer-map) · [Composition](#3-composition) ·
[Interaction flow](#4-interaction-flow) · [Messaging](#5-messaging) ·
[Cognition](#6-cognition) · [Memory](#7-memory) · [Actions](#8-actions) ·
[Embodiment](#9-embodiment) · [Lifecycle](#10-runtime-resources-and-lifecycle) ·
[Paths](#11-paths) · [Enforcement](#12-enforcement) ·
[Future boundaries](#13-intended-future-boundaries-not-implemented)

## 1. Architectural principles

- `mira.domain` contains technology-independent vocabulary and deterministic
  embodiment behavior. It does not import Qt or another `mira` layer.
- Side effects stay behind outer-layer boundaries: adapters bind technology,
  actions contain local effects, and UI renders or publishes interaction.
- `mira.application` is the construction root. UI receives a built graph and
  does not construct cognition, state, messaging, or scheduler services.
- `ActivityAuthority` is the sole committer of shared semantic activity/affect
  state; events notify subscribers but do not own state.
- Rendering consumes immutable, renderer-independent `EmbodimentFrame` values.
- Every action reaches an explicit, source-independent execution policy before
  its handler can run.

## 2. Layer map

The enforced import direction is summarized below. Imports within a layer are
allowed; imports not listed are rejected by `scripts/check_layering.py`.
Permissions are not transitive: `cognition` may import `actions`, but not
`messaging`.

| Layer | Responsibility | May import other M.I.R.A. layers |
|---|---|---|
| `domain` | Shared models, scheduler port, embodiment semantics and playback | none |
| `config` | Packaged configuration resources | none |
| `messaging` | Synchronous in-process event fan-out | none |
| `memory` | In-session retention and context | `domain` |
| `actions` | Contracts, registry, policy, executor, handlers | `domain`, `memory`, `messaging` |
| `cognition` | Rule/LLM intent engines, schema checks, response building | `domain`, `memory`, `actions` |
| `core` | Turn, interaction, activity, and behavior orchestration | `domain`, `messaging`, `memory`, `actions`, `cognition` |
| `adapters` | Concrete implementations of domain ports | `domain` |
| `application` | Object construction and wiring | `domain`, `messaging`, `core`, `adapters` |
| `ui` | Qt presentation and face control | `domain`, `application` |
| `main` | Process entry point | unrestricted composition entry point |

Qt imports are confined to `ui`, `adapters`, and the `main` entry point. Both
layering and Qt containment currently have zero declared exceptions.

## 3. Composition

The production graph is explicit (`mira/application/composition.py`):

```text
mira.main
  → QApplication
  → build_application()
      → EventBus
      → StateManager → ActivityAuthority
      → QtScheduler
      → Brain
      → InteractionManager
      → EmbodiedBehavior
  → MainWindow(application)
  → Qt event loop
```

`Brain` currently constructs its own session memory, selected intent engine,
response builder, action registry, executor, and concrete action handlers. The
composition root shares one bus, one activity authority, and one scheduler
across the graph. Tests may inject `ManualScheduler`; no service container or
runtime registry exists.

Construction order is observable: `InteractionManager` and `EmbodiedBehavior`
subscribe to the same events from their constructors, so the order they are
built in is the order their handlers run in.

## 4. Interaction flow

The UI calls `Brain.process_text_async`. The implemented flow is:

```text
UserInput
  → SessionMemory records the user message
  → input/processing events and semantic activity transitions
  → scheduler worker: intent inference + optional ActionRequest proposal
  → serialized completion: stale-request check
  → SessionMemory last-intent update + intent event
  → action validation → execution policy → executor → optional handler
  → ResponseBuilder
  → SessionMemory records the response
  → response event + semantic activity/affect transition
  → MainWindow chat callback and face rendering
```

Worker code has no authority to execute actions, mutate session memory, emit
events, change shared state, or touch widgets. Those effects occur only during
serialized finalization. Each submission receives a request id; a result whose
id is no longer the latest is discarded before any side effect. `process_text`
provides the same logical path synchronously for tests and non-UI callers.

## 5. Messaging

`EventBus` (`mira/messaging/events.py`) is a synchronous, in-process fan-out:
`emit` calls every subscriber in subscription order on the caller's thread.
It holds no state and has no unsubscribe or asynchronous delivery. Events
currently in use:

| Event | Emitted by | Subscribed by |
|---|---|---|
| `input_focused`, `input_unfocused`, `input_text_changed` | `MainWindow` (from `ChatPanel` signals) | `InteractionManager`, `EmbodiedBehavior` |
| `user_input_received` | `Brain` | `InteractionManager` |
| `processing_started` | `Brain` | `InteractionManager` |
| `intent_inferred` | `Brain` | `EmbodiedBehavior` |
| `action_started`, `action_completed`, `action_failed` | `ActionExecutor` | `MainWindow` |
| `response_ready` | `Brain` | `InteractionManager`, `EmbodiedBehavior` |
| `state_changed` | `StateManager` (via `ActivityAuthority`) | `MainWindow` |

## 6. Cognition

Intent engines implement `IntentEngine.infer(UserInput) -> IntentResult`
(`mira/cognition/intent_engine.py`). `Brain` selects one at construction from
`MIRA_INTENT_ENGINE`:

- `RuleIntentEngine` — deterministic substring/prefix matching. Its phrase
  set is predominantly Italian, with a few English aliases.
- `LLMIntentEngine` — prompts a local Ollama model (`OllamaClient`, standard
  library HTTP) for structured JSON constrained to an allowlisted intent enum.
  The prompt includes the registered action contracts and a bounded, sanitized
  recent-history block from `SessionContextBuilder`, kept separate from the
  current input. On client failure it falls back to `RuleIntentEngine`; on
  schema or policy failures it drops the proposed action and keeps the LLM
  intent, or `unknown` if the intent is not allowlisted. Every fallback is recorded in `IntentResult.entities`
  (`llm_fallback_used`, `llm_fallback_reason`).

`Brain.build_action_request` maps the intent to an optional `ActionRequest`,
either from a validated LLM proposal or from a fixed rule-intent mapping.
`ResponseBuilder` then produces the `BrainResponse`: action results take
priority, LLM response text is used only for non-action intents, and LLM
emotion labels map to `EmbodimentIntent` through an explicit allowlist.
The builder's fixed response texts and action messages are currently Italian.

Configuration and fallback reasons are listed in
[configuration.md](configuration.md#local-llm).

## 7. Memory

`SessionMemory` (`mira/memory/session_memory.py`) holds the current process's
conversation: a bounded history of user and assistant messages (default 20),
the last inferred intent, and a free-form context dictionary. It has no
persistence; everything is lost when the process exits. Actions read it for
session introspection, and `SessionContextBuilder` reads it to build LLM
prompt context (default: 8 messages, 1200 characters).

## 8. Actions

Action handling separates validation from authorization:

```text
ActionRequest
  → ActionExecutor boundary
      → generic request validation
      → ActionExecutionPolicy
          → safe
          → allowed_side_effect
          → confirmation_required (blocked)
          → denied (blocked)
      → registered handler dispatch
```

LLM schema checks validate action existence, intent compatibility, and required
parameters before a request is proposed. `ActionExecutor` validates the generic
request shape. `ActionExecutionPolicy` then authorizes only from the registered
contract; it does not inspect whether a request came from rules, an LLM, or
another future source. Unknown actions and handlers without contracts fail
closed. Contracts live in `mira/actions/action_contracts.py`.

Current classification:

- Read-only: time/date, echo, session reads, action listing, system information,
  and development project-path reporting.
- Allowed side effects: open URL, open application, show notification, and open
  an allowed directory.
- Confirmation required: clear session memory. It returns explicit
  `confirmation_required` metadata and its handler does not run.
- Explicitly denied built-ins: none.

No confirmation UI or confirmed-execution token exists yet. Desktop handlers
target a Linux desktop (`xdg-open`, `notify-send`, and fixed application
commands); see [configuration.md](configuration.md#desktop-actions).

## 9. Embodiment

The semantic-to-renderer path is:

```text
EmbodimentIntent
  → resolve_expression_key
  → ExpressionDefinition
  → PlaybackPose / EmbodimentPlayback
  → immutable EmbodimentFrame
  → FaceWidget renderer
```

`ActivityState` (idle, listening, thinking, speaking), `AffectState` (neutral,
happy, confused), and optional `ExpressionKey` remain independent semantic
inputs. Override, affect, then activity determines the expression.
`ExpressionKey` also keeps the `tired` and `angry` profiles, which runtime
behavior never selects; they are reachable only from the debug drawer.
`FaceState` is retained only at the current compatibility/presentation boundary.

`ExpressionDefinition`, `EmbodimentPlayback`, and `EmbodimentFrame` are pure
Python and Qt-independent. Playback advances from explicit elapsed time and
resolves shared pose plus per-eye asymmetry into normalized frame values.

The extraction is intentionally incomplete: `FaceController` still owns blink
timing, cursor gaze and hold behavior, idle target selection, scrutiny motion,
thinking drift, speaking pulse, and target deformation. It feeds their resolved
targets and eye-closed flags into the pure playback object. These behaviors are
not represented as independent domain services.

`EmbodiedBehavior` (in `core`) shapes timing around responses: brief affect
reactions to `greeting`/`unknown` intents and a delayed decay from the response
expression back to idle or listening.

## 10. Runtime resources and lifecycle

`QApplication` owns the process event loop; the main window owns its widget
tree, and `FaceWidget` owns its parented frame timer. `Application` owns the
shared scheduler boundary. `QApplication.aboutToQuit` calls
`Application.shutdown()`, which shuts down the scheduler.

`QtScheduler` owns delayed timers and completion receivers on its construction
thread. Shutdown is idempotent: it cancels pending timers, detaches completion
callbacks, rejects new work, and prevents in-flight results from re-entering the
application. Already-running Python work is not force-cancelled; the global Qt
thread pool owns its runnable until it returns. Current LLM network calls use a
configured socket/I/O timeout; this is not a wall-clock deadline for a worker.

EventBus subscriptions retain application-lifetime components, including the
single main window. There is no unsubscribe infrastructure because the current
runtime creates one graph/window and exits when it closes. Desktop applications,
URLs, directories, and notifications are fire-and-forget external effects;
processes launched by M.I.R.A. are not retained or terminated at shutdown.

## 11. Paths

- Package resources resolve from the installed `mira` package, not the process
  working directory. The expression profile JSON is currently both the packaged
  active profile and the debug drawer's save/reload target.
- The invocation directory is captured for the explicit `current` alias and for
  resolving relative directory requests. It is context, not an allowed-root
  grant.
- Home-directory paths and their aliases resolve from the user's home. Directory
  actions remain constrained to the home tree and, in a verified source checkout,
  the project tree.
- A project root is reported only when the imported package is the direct `mira`
  child of a checkout containing known project markers (`pyproject.toml` and
  `bin/mira`). Installed distributions do not infer a checkout from the working
  directory or `site-packages`.

## 12. Enforcement

Two standard-library checkers turn the boundaries above into CI failures:

- `scripts/check_layering.py` — import direction (section 2), Qt containment,
  and a declared layer for every package under `mira/`.
- `scripts/check_state_authority.py` — only `mira/core/activity_authority.py`
  commits state, and only declared modules may reference `StateManager`.

Both declare zero exceptions, and both are exercised by the test suite
(`tests/test_layering.py`, `tests/test_activity_state_authority.py`). The full
validation baseline is in [development.md](development.md#validation).

## 13. Intended future boundaries (not implemented)

The following are extension directions, not existing APIs:

- Camera/perception may produce observations or attention targets that feed the
  existing semantic activity/embodiment path; no camera subsystem exists.
- Voice input may enter the existing input/cognition pipeline, and voice output
  may consume responses; no audio subsystem exists.
- Persistent memory may replace or complement `SessionMemory` behind an explicit
  persistence boundary; current memory is session-only.
- A physical renderer or actuator may consume renderer-independent
  `EmbodimentFrame` output; no hardware abstraction or robot control exists.

These capabilities should extend a concrete requirement when introduced. They
are not prerequisites for starting feature work on the Foundation v1 baseline.
