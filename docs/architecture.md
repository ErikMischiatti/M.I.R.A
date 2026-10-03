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
mira.main [--notch]                     MIRA: main window · MIRA-NOTCH: notch
  → claim_or_forward(runtime_directory(), SurfaceRequest)
      another runtime answers → forward the request, exit 0 (no Qt started)
      runtime lock taken      → this process is the runtime:
  → QApplication
  → build_application(system_event_sources=desktop_system_event_sources())
      → EventBus
      → StateManager → ActivityAuthority
      → QtScheduler
      → Brain (→ ActionExecutor: run ids)
      → InteractionManager
      → EmbodiedBehavior
      → CameraCapture(OpenCVCameraSource if MIRA_CAMERA is set, else none)
      → TaskActivity                     (running actions → "tasks_changed")
      → TaskNoticeProducer               (action results → "notice")
      → SystemNoticeProducer(sources)    (system changes → "notice")
  → DesktopShell(application, initial_surface, load_notch_session)
      → FaceController → FaceAnimator   (the one embodiment and its clock)
      → the initial surface only; any other on its first show_surface:
          MainWindow(application, animator)
          NotchWindow + NotchPresenter   (notch configuration read here)
  → InstanceServer(runtime.sock) → DesktopShell.show_surface
  → aboutToQuit: desktop, application, server, lock — in that order
  → quit_on_interrupt; application.start(); desktop.start(); server.listen()
  → Qt event loop
```

### Single runtime

There is one logical desktop runtime per OS user. `MIRA` and `MIRA-NOTCH`
are requests for a surface (`main window`, `notch`), not programs of their
own (`mira/application/instance.py`, `mira/ui/instance_server.py`):

- **Election.** The runtime holds an exclusive `flock` on `runtime.lock` in
  its runtime directory for its whole life. Taking it is atomic, so of two
  launchers started at once exactly one wins; the kernel drops it when the
  process ends, normally, by a crash or by SIGKILL. The fd is not inherited
  by programs MIRA launches.
- **Forwarding.** A launcher first tries to deliver its request on
  `runtime.sock` (one JSON line, `{"show": …, "notch_config": …}`; one
  `{"ok": true, "pid": …}` back once the surface is shown) and exits 0 if
  answered — before Qt is ever created. Otherwise it tries the lock;
  otherwise someone is still starting up, so it waits 0.1 s and tries again,
  for at most 15 s, after which it reports the unresponsive runtime and
  exits 1 rather than becoming a second one.
- **Identity.** `$XDG_RUNTIME_DIR/mira` (per user, 0700, tmpfs, cleared at
  logout and reboot), else `/tmp/mira-<uid>` created 0700 and refused unless
  it is this user's and private. Never derived from the working directory.
  Other users never share it.
- **Stale state.** A crash leaves `runtime.sock` behind but not the lock.
  Connecting to it is refused, the next launcher takes the lock and removes
  the file before listening. Recovery needs no cleanup step and no timeout.
- **Surfaces.** Every request — the starting launcher's, a forwarded one,
  the notch's double click and menu — goes through
  `DesktopShell.show_surface`, which builds a surface once and brings it
  forward afterwards. Building one never builds the other; the notch's
  configuration is read when the notch is first built, from the forwarded
  `--config` if any. Both surfaces share the one `Application`,
  `FaceController` and animator, and the notch never builds `MainWindow`.
- **Shutdown.** Closing the main window ends the runtime only while no notch
  exists (as `MIRA` always behaved); once a notch exists it just hides the
  window. The notch's Quit and Ctrl+C end the whole runtime: the shell, then
  the application (producers first), then the server (the socket file is
  removed), then the lock. A forwarding launcher exits as soon as its
  request is answered.
- **SIGTERM** is not handled: the process ends without the orderly shutdown
  and leaves the socket file, which the next launcher removes (as after a
  crash). Use Quit or Ctrl+C.
- **Not here:** no network listener, no generic remote control. Requests are
  limited to showing a surface. `MIRA-TUNE` is a separate development tool,
  not part of the runtime.

`mira.application` must not import `mira.ui`, and the face's runtime is a UI
object, so the UI has its own composition owner: `DesktopShell` creates one
`FaceController` stepped by one `FaceAnimator`, renders `state_changed` onto
it, and hands the animator to every surface that shows MIRA — the main window,
the notch, or both once the notch has opened the window. Surfaces only repaint on its ticks; none creates, steps or drives
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

### Semantic cues

A cue (`EmbodimentCue`: SUCCESS, WARNING, ERROR — `mira/domain/embodiment_cue.py`)
is MIRA itself reacting to a result or condition for a moment. Five concepts
stay distinct: *activity* (what MIRA does), *expression* (the face resolved
from activity and affect), *notice* (something worth a glance, with a
`NoticeTone`), *presence* (how much of the notch shows) and *cue*.

```text
Notice ── cue_for_notice (tone) ──▶ EmbodimentCue
                                         │  FaceController.show_cue
                                         ▼
                CuePlayback (frame time: ease in · hold · ease out)
                                         │
EmbodimentIntent → expression → frame ──┴─▶ EmbodimentFrame
                                          EyeFrame.shape / shape_amount
                                                   ▼
                                     EyePainter: chevron · triangle · cross
                              (notch, main-window face, any future renderer)
```

- **Overlay, not expression.** A cue never changes the intent, the expression
  or the micro-motions. While it plays, each `EyeFrame` carries `shape`
  (`CHEVRON` ^ for success, `TRIANGLE` for warning, `CROSS` X for error) and
  `shape_amount`: up to 0.5 the natural eye closes, from 0.5 the shape opens.
  When it ends the frame is the current expression again — THINKING, a
  WARNING cue, then THINKING; an expression change during the cue is what
  returns.
- **Lifetime** is counted in the controller's frame time (`update(dt)`), so it
  needs no timer: 0.2 s in, hold (success 0.9 s, warning 1.6 s, error 2.2 s),
  0.2 s out. Nothing loops or flashes.
- **Priority** is deterministic: ERROR > WARNING > SUCCESS. Equal or higher
  replaces (the same cue just holds again; a different one continues from the
  shut eye), lower is dropped, never queued.
- **Shared.** The cue lives in the one controller, so every surface showing
  MIRA shows it; the notch's symbol row is surface decoration, not a cue. The
  notch never owns cue state.
- **Blinks and eyelids** do not apply to a cued eye; offsets (gaze, wander)
  still move it.
- **Safety.** A cue is drawn inside the eye's own rectangle (strokes inset by
  half their width, round caps included), and that rectangle is what the
  motion envelope already bounds, so the notch's eye-safety rule and the
  content placement hold for cue shapes unchanged.
- **Not cues:** ongoing states. Those are *conditions*, below.

### Persistent conditions and running tasks

`EmbodimentCondition.TASK_RUNNING` is the persistent layer between the
expression and transient cues. These stay distinct:

| | lasts | example |
|---|---|---|
| activity / expression | until the activity authority changes it | THINKING |
| condition | exactly as long as it holds, no timeout | TASK_RUNNING |
| cue | a fixed moment | SUCCESS, WARNING, ERROR |
| notice | `notice_duration_ms` on the notch | "Failed" |
| presence | while an expansion reason holds | EXPANDED |

- **Source.** `ActionExecutor` gives each execution that actually starts a
  `run_id`, on `action_started` and on its result; a request refused first
  (invalid, unknown, blocked, awaiting confirmation) has none and never
  runs. `TaskActivity` (`mira/core/task_activity.py`) keeps the set of
  running ids from those events alone and emits `"tasks_changed"`
  (`RunningTasks`) on every change. Overlapping runs keep it active until the
  last ends; unknown or never-started results change nothing.
- **Order.** `TaskActivity` subscribes before the notice producer, so a run
  ends (TASK_RUNNING clears) before its SUCCESS/ERROR notice; confirmation
  never starts a run and produces only the WARNING notice.
- **Resolution** in `FaceController.get_frame`: a showing cue, else the
  strongest condition, else the expression. An ERROR shows over working eyes
  and the working eyes come back when it ends if work still runs; if the
  work ended meanwhile, the plain expression returns. No layer writes into
  another.
- **Treatment.** `FOCUSED`: the natural eye narrowed by up to 40 % of its
  height, breathing slowly (2.4 s, between 80 % and 100 % of the
  treatment), eased in and out over 0.3 s, and still blinking. Inside the
  eye's own rectangle, so eye safety holds.
- **Surfaces.** The condition lives in the shared controller: the main
  window's face and the notch show it alike. The notch additionally shows
  "Working" as its content when open (notice > running work > activity) and
  never expands for it.
- **Today** actions execute synchronously on the UI thread, so a real run
  opens and closes within one call and no frame shows it. The semantics are
  complete and become visible once any action runs longer; MIRA-TUNE's
  "Task running" preview exercises the same path meanwhile.

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
  (why it matters — alert, device, task, connectivity; what preferences are
  keyed on), a `NoticeTone` (info, success, warning, error) and optional short
  text. A producer never says how a notice is shown. Every notice travels as
  the bus event `"notice"`; the shell hands it to `deliver_notice`, which
  gives it to the presenter and lets the shared eyes react with its cue.
- **Presence** is resolved, never set from several places. The owner picks a
  base (hidden or shown); the notch is EXPANDED while any `ExpansionReason`
  holds — HOVER (if `expand_on_hover`), PINNED (tuning preview), NOTICE (a
  notice whose category expands automatically), CONTROLS (the integrated
  controls are open), LISTENING (MIRA is listening) — and COMPACT otherwise.
  The content band shows, in this order of precedence: controls > notice >
  listening (the real waveform) > task running > activity.
- **Integrated controls.** Right click toggles the notch's own controls
  (Settings, Quit M.I.R.A.), drawn as pills in the content band and clicked
  in place; there is no native menu. Opening the main window is not a
  control: the double click is that one gesture. Controls close after use,
  on another right click, on hide, or 1.5 s after the pointer leaves
  (re-entering in time keeps them), on the Scheduler. The notch accepts
  mouse presses, which is what makes X11 deliver the releases its controls
  act on.
  Activity alone never expands; it only chooses the content shown while open.
  A notice of a category that does not expand still becomes the content (seen
  on hover) and still makes the eyes react.
- **Lifetime and replacement.** A notice shows for `notice_duration_ms`, on one
  timer from the injected `Scheduler` (the application's own in production).
  The latest notice replaces the showing one and gets the full duration; there
  is no queue.
- **Rendering.** COMPACT shows only the eyes. EXPANDED keeps the eyes where
  the compact notch places them and draws a vector symbol (`symbol_painter`,
  Qt primitives only) and an optional elided label below them (by default a
  28 px symbol and 20 px text, with a 12 px readable floor), laid out by
  `NotchContentStyle` (`expanded_layout`): symbol size, text size,
  symbol–text spacing and a gap measured from the bottom of the eyes' whole
  motion envelope. The group is centred between the rounded corners. Content
  that would leave the expanded notch, or whose symbol does not fit between
  the corners, makes the configuration invalid like clipping eyes do, so the
  notch keeps its last valid configuration. Mid-transition the content is
  revealed by the outline and faded with the reveal, by design.
- **Actions.** The window emits `NotchAction` values (double click:
  OPEN_MAIN_WINDOW; integrated controls: OPEN_SETTINGS, QUIT) and only those
  its presentation offers.
  `DesktopShell` honours them; the notch never holds `MainWindow`. The
  standalone tools offer none.
- **Workspaces.** The notch is present on every virtual workspace. Before
  every map, `NotchWindow.showEvent` asks its `WorkspacePresence` adapter
  (`mira/ui/desktop/workspaces.py`); on X11 that sets the EWMH
  `_NET_WM_DESKTOP` property to `0xFFFFFFFF` ("all desktops") through a
  short-lived, synchronised libX11 connection (ctypes; no new dependency).
  Mutter honours it and adds `_NET_WM_STATE_STICKY` itself. The request is
  repeated on every show because a window manager drops it on withdraw; no
  workspace is polled and the window is never moved or recreated. Other
  platforms (Wayland, offscreen) get a no-op: the window stays where the
  compositor puts it, and Wayland offers clients no equivalent.

### Notice producers

```text
action_completed/failed ──▶ TaskNoticeProducer ───────────────┐
                                                               ├─▶ "notice" ─▶ DesktopShell
adapters (mira/adapters)            SystemNoticeProducer       │               └▶ deliver_notice
  LinuxUsbDeviceSource   ─┐  Device/  (mira/core)              │                  ├▶ NotchPresenter
  PipeWireAudioSource    ─┼▶ Connectivity ─▶ settle · repeat ──┘                  └▶ eye cue
  NetworkManagerConn.…   ─┘  Event         · debounce
```

| Notice source | Real or preview | Category / tone |
|---|---|---|
| action completed | real | task / success |
| action failed | real | task / error, "Failed" |
| action needs confirmation | real | task / warning |
| USB device connected / disconnected | real (kernel uevents) | device / info |
| audio output or input connected / disconnected | real (PipeWire) | device / info |
| default audio input or output changed | real (PipeWire) | device / info |
| network lost / limited / back online | real (NetworkManager) | connectivity / warning, success |
| "Low battery", "Something failed" | preview only (MIRA-TUNE) | alert |

- **Adapters** (`mira/adapters`) translate one facility into semantic events
  (`mira/domain/system_events.py`) and know nothing about notices. All are
  event-driven on the Qt loop: the kernel's `NETLINK_KOBJECT_UEVENT` socket
  under a `QSocketNotifier` (whole USB devices only, never hubs, named from
  sysfs); one `pw-dump --monitor` child process (audio nodes and the
  effective defaults; the first dump is the baseline); NetworkManager's
  `Connectivity` property over QtDBus. A missing facility makes the source
  report itself unavailable at start; none is required.
- **Producers** (`mira/core/notice_producers.py`) own the noise policy:
  device changes settle for 800 ms per device (a connect and disconnect that
  cancel announce nothing; a new device becoming the default announces only
  its arrival), unchanged repeats within 3 s are dropped, and connectivity is
  debounced for 3 s with the first state as baseline, so a short drop or
  flapping says nothing. Timers run on the `Scheduler` port.
- **Ownership.** The one runtime owns one `SystemNoticeProducer` with one
  instance of each source, built in `build_application`, started by
  `Application.start()` (idempotent) and stopped first by
  `Application.shutdown()`. Surface requests never create producers.

| Producer | created / started / stopped by | on failure |
|---|---|---|
| `TaskActivity`, `TaskNoticeProducer` | composition; live with the graph | — |
| USB (`LinuxUsbDeviceSource`) | producer | ENOBUFS (dropped messages) is skipped; any other socket error stops USB watching with one log line, never a busy loop |
| PipeWire (`PipeWireAudioSource`) | producer | `pw-dump` exiting or failing to start is retried after 1 s, doubling to 30 s, back to 1 s after a minute of stable running; one child at a time; the restarted monitor's first dump is reconciled by node name, so only real changes are announced; an intentional shutdown never restarts |
| NetworkManager | producer | unavailable if NetworkManager is absent at start (not retried); a restart of NetworkManager itself relies on QtDBus following the service name (not exercised) |

Exits, retries and recoveries are logged once each (`[notices] …`);
ordinary device events are not.
- **Filtering** stays the per-category automatic-expansion preference: one
  switch each for alerts, device changes, task results and connectivity
  changes. A disabled category still shows its notice when the notch is open
  and still makes the eyes react; it only does not expand the notch.
- **Embodiment independence.** Nothing in the producers or the presenter
  assumes a Linux desktop, a microphone, a camera, a pointer or a network:
  another runtime passes other sources or none, and renders `NotchPresentation`
  and cues its own way.

**Desktop application notifications (investigated, not implemented).**
GNOME's notification server (gnome-shell, `org.freedesktop.Notifications` on
the session bus) can be observed passively: a D-Bus monitor connection
(`BecomeMonitor`, what `dbus-monitor` uses) sees every `Notify` call with the
application name, summary and body, without replacing the daemon — verified
on this system. It is deliberately not built yet: such a connection reads
*every* application's notifications, including private message content;
GTK applications using `GNotification`/the portal reach the shell through
`org.gtk.Notifications` instead, so coverage would be partial; and mirroring
without a policy would be noise. When built it fits as one more
`SystemEventSource` producing an application-notification event, with an
allow-list/filter policy in `mira.core` deciding which become notices.

**Implemented, prepared, future.**

| | |
|---|---|
| Implemented | one runtime per user with surface requests; lazy main window and notch; all-workspace presence on X11; readable expanded content by default; integrated notch controls; USB, audio-topology and connectivity notices with supervised PipeWire monitoring; per-category expansion; SUCCESS/WARNING/ERROR cues and the TASK_RUNNING condition; keyboard listening activation; the runtime's microphone capture with real level and waveform in the notch |
| Prepared | `SystemEventSource` port; `EyeShape` in `EmbodimentFrame`; run ids for asynchronous actions; `ListeningActivationMode.WAKE_WORD`, activation sources and capture leases for a wake-word detector; `AudioFrame` for speech processing |
| Future | wake word; voice activity detection; speech-to-text and text-to-speech; asynchronous (long) actions; desktop-notification source with a filter policy; Wayland workspace presence |

### Audio input and listening

```text
keyboard (in-window M, global Ctrl+Alt+M)          future: wake-word detector
        │  "listening_toggle_requested" (ActivationSource)        │
        ▼                                                         ▼
ListeningController (core) ── lease(CONVERSATION) ──▶ AudioInputService (core)
        │ "listening_changed"                          one per runtime, leases
        ▼                                                         │
InteractionManager → ActivityAuthority → LISTENING     AudioCaptureBackend port
        │                                              QtAudioCaptureBackend
        ├─▶ FaceController (every surface)             (QAudioSource, 16 kHz s16)
        └─▶ NotchPresenter: LISTENING reason                      │ AudioFrame
                 NotchWindow pulls AudioInputService.waveform ◀───┘ measure / envelope /
                 on each animator frame (~33 Hz)                     reducer (pull only)
```

Three things stay apart, and nothing assumes they coincide:

| | what | where |
|---|---|---|
| capture capability | the microphone can be opened and delivers frames | `AudioInputService` + backend |
| listening activation | something asks MIRA to start or stop listening | `ActivationSource`; keyboard today |
| conversation LISTENING | MIRA is listening to the user | `ListeningController` → activity authority |

- **Ownership.** `AudioInputService` is built once in the composition and
  shut down first (after `ListeningController` lets go). Capture runs while
  any *lease* is held — CONVERSATION while listening, DIAGNOSTICS for the
  settings' microphone test — so "the microphone is open" is never taken to
  mean "MIRA is listening". A wake-word detector will hold its own lease.
  Surfaces never own audio; showing more surfaces never adds capture.
- **Backend.** Qt Multimedia's `QAudioSource`, already part of PySide6: no
  new dependency, in process, data delivered on the Qt main thread through
  the event loop, input changes through `QMediaDevices`. 16 kHz signed
  16-bit mono is requested; other formats are converted. It follows the
  default input (moving capture when the default changes or the device
  disappears; failing, once, when no input is left). PySide6 6.11 cannot
  deliver `QAudioSource.stateChanged` to Python, so a 1 Hz watchdog detects
  an error state or a stream with no data for 3 s. The first device
  enumeration takes about 1.7 s, so the runtime prepares it at start, before
  showing anything (enumeration only, no capture). `pw-record` on a pipe was
  the alternative considered; it would add a second supervised child.
- **Values** (`mira/domain/audio.py`, pure and deterministic):
  `AudioFrame` (s16 mono PCM in memory, rate, timestamp); `measure` → RMS and
  peak as fractions of full scale; a display scale in dBFS (−60 dB → 0,
  0 dB → 1); `LevelEnvelope` with a 30 ms attack and 350 ms release driven by
  frame timestamps; `WaveformReducer` keeping the peak of each 25 ms bucket,
  at most 40 (about a second), so memory never grows with capture time.
  Nothing is written to disk.
- **Update path.** Frames never travel on the bus; renderers pull. The notch
  reads the waveform on its repaints, which the existing face animator drives
  at about 33 Hz: no new timer, nothing at audio rate, and a renderer that
  falls behind only ever sees the latest bounded state.
- **No fake waveform.** Without frames (unavailable, failed, not yet
  arrived) there is no waveform and the notch shows the listening symbol. A
  muted microphone delivers real silence, drawn as a quiet line.
- **Failure.** A microphone that cannot open refuses the toggle; one lost
  while listening ends listening. Each gives one WARNING notice and a log
  line; MIRA keeps running.
- **Priorities.** Eyes: a showing cue > TASK_RUNNING > the expression, which
  shows LISTENING while listening. Notch content: controls > notice >
  listening waveform > task running > activity. LISTENING expands the notch
  through its own reason, and the open microphone is always visible: the
  notch opens and the eyes show LISTENING on every surface, including a
  main window alone.
- **Keyboard safety.** The in-window key is ignored whenever the widget
  receiving it, or the focus widget, is an editable text field (read-only
  views do not count). The global shortcut is one `XGrabKey` of a combination
  that must carry Ctrl, Alt or Super; MIRA never sees other keys.

**Not implemented:** wake-word detection (modelled, never selectable), voice
activity detection, speech-to-text, text-to-speech, recording.

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
shared scheduler boundary, the camera capture and the system event sources.
`mira.main` owns the single-instance lock and server and releases them last
(which it stops first: the `pw-dump` child is terminated, the netlink socket
closed, the D-Bus match removed, and pending producer timers cancelled).
`QApplication.aboutToQuit`
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
in the order above, in both launch modes. A second Ctrl+C falls back to the
default action. (Without the handler, Python raised `KeyboardInterrupt` inside
whichever slot ran next, PySide swallowed it, and the application kept
running.) `MIRA-TUNE` is a development tool and simply restores the default
SIGINT action, so Ctrl+C ends it without the orderly shutdown.

`QtScheduler` owns delayed timers and completion receivers on its construction
thread. Shutdown is idempotent: it cancels pending timers, detaches completion
callbacks, rejects new work, and prevents in-flight results from re-entering the
application. Already-running Python work is not force-cancelled; the global Qt
thread pool owns its runnable until it returns. Current LLM network calls use a
configured socket/I/O timeout; this is not a wall-clock deadline for a worker.

EventBus subscriptions retain application-lifetime components, including the
single main window. There is no unsubscribe infrastructure because the runtime
creates one graph, at most one main window and one notch, and exits as a whole. Desktop applications,
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
- Voice input may enter the existing input/cognition pipeline: a wake-word
  detector as an activation source, then voice activity detection and
  speech-to-text over the frames the runtime's `AudioInputService` already
  captures while listening; voice output may consume responses. Capture,
  level and waveform exist (section 9a); no speech processing does.
- Desktop application notifications may become notices through a filtered
  `SystemEventSource` (section 9a); investigated, not built.
- Persistent memory may replace or complement `SessionMemory` behind an explicit
  persistence boundary; current memory is session-only.
- A physical renderer or actuator may consume renderer-independent
  `EmbodimentFrame` output; no hardware abstraction or robot control exists.

These capabilities should extend a concrete requirement when introduced. They
are not prerequisites for starting feature work on the Foundation v1 baseline.
