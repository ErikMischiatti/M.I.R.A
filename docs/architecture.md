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
[Embodiment](#9-embodiment) · [Camera](#10-camera-input) ·
[Lifecycle](#11-runtime-resources-and-lifecycle) · [Paths](#12-paths) ·
[Enforcement](#13-enforcement) ·
[Future boundaries](#14-intended-future-boundaries-not-implemented)

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
- Camera frames travel on their own pull-based data plane, never on the
  semantic EventBus, and are produced off the UI thread.
- Every action reaches an explicit, source-independent execution policy before
  its handler can run.

## 2. Layer map

The enforced import direction is summarized below. Imports within a layer are
allowed; imports not listed are rejected by `scripts/check_layering.py`.
Permissions are not transitive: `cognition` may import `actions`, but not
`messaging`.

| Layer | Responsibility | May import other M.I.R.A. layers |
|---|---|---|
| `domain` | Shared models, scheduler and camera ports, embodiment semantics, micro-motions and playback | none |
| `config` | Packaged configuration resources | none |
| `messaging` | Synchronous in-process event fan-out | none |
| `memory` | In-session retention and context | `domain` |
| `actions` | Contracts, registry, policy, executor, handlers | `domain`, `memory`, `messaging` |
| `cognition` | Rule/LLM intent engines, schema checks, response building | `domain`, `memory`, `actions` |
| `core` | Turn, interaction, activity, and behavior orchestration; camera capture | `domain`, `messaging`, `memory`, `actions`, `cognition` |
| `adapters` | Concrete implementations of domain ports (Qt scheduler, OpenCV camera) | `domain` |
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
      → CameraCapture(OpenCVCameraSource if MIRA_CAMERA is set, else none)
  → DesktopShell(application)          (mira/ui/desktop_shell.py)
      → FaceController → FaceAnimator   (the one embodiment and its clock)
      → MainWindow(application, animator)
      → NotchWindow(animator, saved notch settings)   (build_notch)
      → NotchPresenter(application.scheduler, saved behaviour)
                                        (what the notch presents; bind_presenter)
  → aboutToQuit: desktop.shutdown(), then application.shutdown()
  → application.start()   (starts camera capture, if enabled)
  → desktop.start()       (shows the window, starts the animator)
  → Qt event loop
```

`mira.application` must not import `mira.ui`, and the face's runtime is a UI
object, so the UI has its own composition owner: `DesktopShell` creates one
`FaceController` stepped by one `FaceAnimator`, renders `state_changed` onto
it, and hands the animator to every surface that shows MIRA — the main window and
the notch. Surfaces only repaint on its ticks; none creates, steps or drives
the embodiment.

`EmbodimentIntent` (what MIRA is doing) and `NotchPresence` (how much notch is
visible) stay separate. What the notch presents is decided by one Qt-free
`NotchPresenter` the shell owns (section 9a); the window only renders it.

`mira/application/scheduling.py` holds the production `Scheduler` choice on
its own, so the standalone notch tools can time notices without loading the
application graph.

`Brain` currently constructs its own session memory, selected intent engine,
response builder, action registry, executor, and concrete action handlers. The
composition root shares one bus, one activity authority, and one scheduler
across the graph. Tests may inject `ManualScheduler` and a fake `CameraSource`;
no service container or runtime registry exists. Construction opens no device:
capture begins only at `Application.start()`.

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
  → MainWindow chat callback; DesktopShell renders the state on the shared face
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
| `state_changed` | `StateManager` (via `ActivityAuthority`) | `DesktopShell` (onto the shared embodiment) |

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
  → resolve_expression_key → ExpressionKey
      ├── ExpressionDefinition               base pose
      └── MotionSettings + gaze_weight_for   which motions, gaze strength
  → MicroMotions
      ├── BlinkMotion       blink interval, closed phase, rescheduling
      ├── IdleGaze          random wander offset
      ├── thinking drift    sideways glance (shared phase)
      ├── speaking pulse    rhythmic height (shared phase)
      ├── GazeAttention     follow · hold · return · scrutiny   ← AttentionTarget
      └── gaze deformation  width/height implied by the offset
  → MicroMotionState (additive deltas + eyes closed)
  → PlaybackPose target / EmbodimentPlayback
  → immutable EmbodimentFrame
  → FaceWidget renderer
```

`ActivityState` (idle, listening, thinking, speaking), `AffectState` (neutral,
happy, confused), and optional `ExpressionKey` remain independent semantic
inputs. Override, affect, then activity determines the expression.
`ExpressionKey` also keeps the `tired` and `angry` profiles, which runtime
behavior never selects; they are reachable only from the debug drawer.
`FaceState` is retained only at the current compatibility/presentation boundary.

Everything from `ExpressionDefinition` to `EmbodimentFrame` is pure Python in
`mira.domain` and imports no Qt:

- `mira/domain/micro_motion.py` — `MicroMotions` composes the micro-motions
  into one `MicroMotionState` per step. The expression's selection policy stays
  outside the generators: the profile says which motions are on
  (`MotionSettings`), and `gaze_weight_for` says how strongly an expression lets
  attention move the eyes.
- `mira/domain/gaze.py` — `GazeAttention` smooths toward an `AttentionTarget`,
  holds briefly after release, eases back to centre, and adds a small
  scrutiny orbit once settled on a target.
- `mira/domain/attention.py` — `AttentionTarget` is a normalized point in
  [-1, 1] on both axes: (0, 0) is straight ahead, +x is the viewer's right and
  +y is down, the same axes as frame offsets. It carries no pixels, widget or
  device information.

Micro-motions advance in fixed steps: one step per rendered frame, at the
playback reference rate of 30 ms. `EmbodimentPlayback` alone interpolates by
explicit elapsed time. Randomness comes from an injected `RandomSource`; the
default is the process-wide `random` module, and tests pass a seeded
`random.Random`.

The Qt side is a thin presentation shell. `FaceWidget` converts a cursor
position over the face canvas into an `AttentionTarget`
(`cursor_attention_target`) and releases attention when the cursor leaves.
`FaceController` owns the editable expression library, selects the profile for
the current intent, configures `MicroMotions`, and feeds the resulting target
to playback. It generates no motion itself and does not know that attention
came from a mouse.

`EmbodiedBehavior` (in `core`) shapes timing around responses: brief affect
reactions to `greeting`/`unknown` intents and a delayed decay from the response
expression back to idle or listening.

## 9a. Notch presentation

The notch is ambient presence and glanceable, transient information; the
main window stays the place for conversation, detail and configuration.

```text
EventBus ── state_changed ──────────┐
         ── action_completed/failed ┤      (DesktopShell maps them)
NotchWindow ── hover_changed ───────┤
                                    ▼
                           NotchPresenter          mira/ui/notch/presentation.py
                     (base presence, activity,     Qt-free; timers via the
                      hover, pin, one notice)      Scheduler port
                                    │
                           NotchPresentation        immutable value
               presence · activity · notice · reasons · actions · content
                                    ▼
                              NotchWindow  ── action_requested ──▶ DesktopShell
                      (eyes; symbol + label row)                   (MainWindow,
                                                                    settings)
```

- **Notices** (`mira/domain/notice.py`) are UI-independent: a `NoticeCategory`
  (why it matters — alert, device, task; what preferences are keyed on), a
  `NoticeTone` (info, success, warning, error) and optional short text. A
  producer never says how a notice is shown. Today's only producer is the
  shell mapping action results to TASK notices.
- **Presence** is resolved, never set from several places. The owner picks a
  base (hidden or shown); the notch is EXPANDED while any `ExpansionReason`
  holds — HOVER (if `expand_on_hover`), PINNED (tuning preview), NOTICE (a
  notice whose category expands automatically) — and COMPACT otherwise.
  Activity alone never expands; it only chooses the content shown while open.
- **Lifetime and replacement.** A notice shows for `notice_duration_ms`, on one
  timer from the injected `Scheduler` (the application's own in production).
  The latest notice replaces the showing one and gets the full duration; there
  is no queue. A notice whose category does not expand still becomes the
  content for its lifetime.
- **Rendering.** COMPACT shows only the eyes. EXPANDED keeps the eyes where
  the compact notch places them and uses the revealed row beneath them for a
  vector symbol (`symbol_painter`, Qt primitives only) and an optional short,
  elided label (`expanded_layout`). Valid configurations keep every eye pose
  inside the compact notch, so the row can never overlap the eyes.
- **Actions.** The window emits `NotchAction` values (double click:
  OPEN_MAIN_WINDOW; context menu: every offered action) and only those its
  presentation offers. `DesktopShell` honours them; the notch never holds
  `MainWindow`. The standalone tools offer none.

**Embodiment independence.** Nothing in the presenter assumes a microphone,
camera, text or pointer: notices are categories, content may lack a label,
the set of actions is presented per embodiment, and hover is just one
expansion reason. The desktop-specific adapter boundary is `NotchWindow`
plus the shell's mapping of bus events to presenter input; another
embodiment would supply its own renderer for `NotchPresentation` or its own
presenter for the same notices.

**Semantic eye treatments (not implemented).** Eyes are drawn from
`FaceController`'s `EmbodimentFrame` by `EyePainter`. A future WARNING/ERROR/
SUCCESS eye treatment has two possible homes, and the choice is per
treatment: one shared by every embodiment belongs in the embodiment path
(`EmbodimentIntent` → `ExpressionKey`/frame, through the one controller); a
notch-only treatment belongs at paint time, derived from
`NotchPresentation.content`/`notice` and applied by the notch's own painter,
so `FaceWidget` never sees it. Either must keep the motion-envelope safety
rule (`visual_config`) true for the new shapes. Deferred to a later tranche.

**Input level (not implemented).** No audio subsystem exists, so there is no
real microphone level to show. The intended interface is a domain value such
as `AudioInputLevel(amplitude: float in [0, 1], timestamp_ms: int)`, produced
by a future audio adapter and fed to the presenter like activity (pulled or
pushed on the UI thread), never a synthetic waveform. LISTENING's symbol is
static until that source exists.

## 10. Camera input

An optional camera foundation exists: acquiring frames and reporting camera
status. **No perception exists yet.** Nothing detects faces, people or objects,
and camera frames do not influence the face or any semantic state.

```text
webcam (/dev/video*, index or path)
  → OpenCVCameraSource        adapters; the only module that imports cv2
  → CameraSource port         domain: open() → CameraInfo, read() → CameraFrame | None, close()
  → CameraCapture             core; one dedicated capture thread
      ├── latest_frame() / wait_for_frame()   frame data plane (pull)
      └── status: CameraStatus               disabled · stopped · opening · active · unavailable · failed
```

- **Boundary** (`mira/domain/camera.py`). `CameraFrame` is an immutable
  capture: sequence, monotonic timestamp, width, height, `PixelFormat.BGR8`,
  and `pixels`, a read-only flat byte view that owns its buffer. It carries no
  OpenCV or numpy type, and a consumer can wrap it without copying. Devices
  are opaque strings. `CameraError` is the single open-failure type.
- **Adapter** (`mira/adapters/opencv_camera.py`). `OpenCVCameraSource`
  imports OpenCV lazily on `open()`. OpenCV is the optional `camera` extra
  (`opencv-python-headless`; the regular wheel bundles Qt plugins that clash
  with PySide6). Without it, the camera reports itself unavailable. Requested
  size and rate are best effort, and `CameraInfo` reports what the device
  negotiated. Each read allocates a new image, which the frame wraps
  without a copy.
- **Runtime** (`mira/core/camera_capture.py`). `CameraCapture` owns one daemon
  thread, `mira-camera`, which is the only thread that touches the source: it
  opens, reads, and always releases the device on exit. The `Scheduler` port is
  not used. It carries short turn work whose results return to the serialized
  context, while a camera is a continuous producer whose frames must never be
  marshalled onto the UI thread. The capture thread never touches Qt, the
  EventBus or application state.
- **Frame transport.** Frames do not travel on the EventBus. The bus is a
  synchronous fan-out for small semantic events on the serialized context;
  30 images a second through it would either run every subscriber on the
  capture thread or queue images on the UI thread. The capture keeps only the
  newest frame and consumers pull it: `latest_frame()` never blocks, and
  `wait_for_frame(newer_than=...)` lets a consumer thread sleep until something
  newer arrives. A slow consumer sees fewer frames, never a backlog, and no
  consumer code runs on the capture thread. Nothing is published after a stop.
- **Failure.** A missing, busy or not-permitted device, or missing OpenCV, is
  `unavailable`, with the reason. OpenCV cannot tell those cases apart, so the
  reason says so. An unexpected open error, or five consecutive failed reads,
  is `failed`. Failures stay inside the capture thread and become status,
  which is readable from any thread, never raises, and keeps a failure reason
  visible after a stop. The rest of MIRA runs identically without a camera.
- **Diagnostics.** The debug drawer shows one read-only status line, polled
  every 500 ms while the drawer is visible. It receives a status reader, not
  the capture, and there is no image preview.

Future perception is expected to consume frames and produce semantic
observations. None of this is implemented:

```text
CameraSource → CameraFrame
  → future PersonDetector → future PersonObservation
  → future AttentionTarget → MicroMotions (existing gaze input)

CameraFrame
  → future ObjectDetector → DetectedEntity[]
  → future RelateAnything adapter → SceneRelation[]
```

## 11. Runtime resources and lifecycle

`QApplication` owns the process event loop; the main window owns its widget
tree, and a `FaceAnimator` owns the frame timer, the only clock that steps the
face: each tick advances its controller once and then notifies the views, which
only repaint. A standalone `FaceWidget` creates and parents its own animator;
views sharing one embodiment share one animator. In the application that
animator and its controller belong to `DesktopShell`, which outlives every
surface showing them. Each `FaceController` owns one `MicroMotions` instance (injectable for
tests); it holds no timer and has nothing to shut down. `Application` owns the
shared scheduler boundary and the camera capture. `QApplication.aboutToQuit`
first calls `DesktopShell.shutdown()`, which stops the animator and then
closes the surfaces, and then `Application.shutdown()`, which first shuts down the camera (stopping
capture, joining the capture thread for up to two seconds, and releasing the
device) and then the scheduler. Both are idempotent, and a shut-down camera
refuses to start again. A read that outlasts the join still releases the
device when it returns, and no second reader starts meanwhile; the capture
thread is a daemon, so it cannot keep the process alive.

Ctrl+C takes the same path. `mira.main` installs a SIGINT handler that asks
the event loop to quit and watches the signal's wakeup fd, so the handler runs
promptly from inside the Qt loop instead of raising `KeyboardInterrupt` in
whichever slot happens to run next; `aboutToQuit` then shuts everything down
in the order above. A second Ctrl+C falls back to the default action.

Ctrl+C takes the same path. `mira.main` installs a SIGINT handler that asks Qt
to quit, and watches the signal's wakeup fd from the event loop so the handler
runs at once; without it Python raised `KeyboardInterrupt` inside whichever
slot ran next (the animator tick), PySide swallowed it, and the application
kept running. A second Ctrl+C restores the default action, for a shutdown that
hangs. Signal handling lives only at that entry point.

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

## 12. Paths

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

## 13. Enforcement

Two standard-library checkers turn the boundaries above into CI failures:

- `scripts/check_layering.py` — import direction (section 2), Qt containment,
  and a declared layer for every package under `mira/`.
- `scripts/check_state_authority.py` — only `mira/core/activity_authority.py`
  commits state, and only declared modules may reference `StateManager`.

Both declare zero exceptions, and both are exercised by the test suite
(`tests/test_layering.py`, `tests/test_activity_state_authority.py`). The full
validation baseline is in [development.md](development.md#validation).

## 14. Intended future boundaries (not implemented)

The following are extension directions, not existing APIs:

- Perception may consume camera frames and produce semantic observations,
  and attention derived from them may reach the gaze path the cursor uses
  today:

  ```text
  CameraFrame (exists)
        ↓
  PersonDetector → PersonObservation
        ↓
  AttentionTarget (exists)
        ↓
  MicroMotions / GazeAttention (exists)
        ↓
  EmbodimentFrame → renderer
  ```

  Camera capture (section 10), `AttentionTarget` and the gaze behavior
  (section 9) exist.
  No detector, observation model, or wiring from the camera into the face
  exists yet. Relation perception (object detection feeding a RelateAnything
  adapter) is a further direction with no code.
- Voice input may enter the existing input/cognition pipeline, and voice output
  may consume responses; no audio subsystem exists.
- Persistent memory may replace or complement `SessionMemory` behind an explicit
  persistence boundary; current memory is session-only.
- A physical renderer or actuator may consume renderer-independent
  `EmbodimentFrame` output; no hardware abstraction or robot control exists.

These capabilities should extend a concrete requirement when introduced. They
are not prerequisites for starting feature work on the Foundation v1 baseline.
