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
      → CountdownTimer                   (the one countdown → "timer_changed", "timer_finished")
      → SoundCues(sound_output)          ("timer_finished" → the timer-done chime)
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
- **SIGTERM** (a logout, a service manager) takes the same orderly path as
  Ctrl+C: `quit_on_interrupt` handles both, its Python handler only posts
  `app.quit` (the wakeup fd wakes the loop), and everything else runs in
  `aboutToQuit` — the shell, then the application: listening and audio,
  device queries (their child processes killed, `DeviceInfoService`
  cancels its providers), the system watchers (the USB socket, `pw-dump`),
  the camera, the scheduler and its timers — then the server (socket file
  removed) and the lock. A second signal of the same kind ends a shutdown
  that hangs. SIGKILL cannot be handled: the kernel still closes every
  descriptor and the lock, and the next launcher removes the stale socket
  file; an orphaned `pw-dump` ends at its next write (SIGPIPE).
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
  controls are open), LISTENING (MIRA is listening), TIMER (the timer panel
  is open), TIMER_DONE (a finished countdown awaits OK) — and COMPACT
  otherwise.
  The content band shows, in this order of precedence: controls > timer
  panel > notice > listening (the real waveform) > countdown > task running >
  activity — where which notice shows, and when, is decided by priority:
  none below INTERACTION during a turn of conversation, and none below
  CRITICAL while the controls or a timer panel have the band (see "Notice
  priority").
- **Integrated controls.** Right click toggles the notch's own controls
  (Timer, Settings, Quit M.I.R.A.), drawn as pills in the content band and clicked
  in place; there is no native menu. Opening the main window is not a
  control: the double click is that one gesture. Controls close after use,
  on another right click, on hide, or 1.5 s after the pointer leaves
  (re-entering in time keeps them), on the Scheduler. The notch accepts
  mouse presses, which is what makes X11 deliver the releases its controls
  act on.
- **Timer.** The Timer control replaces the controls with the timer panel
  in the same band: the time, then `TimerButton` pills — a duration to
  choose (−/+ by a minute up to ten, by five above, 1–180 min; presets 5,
  10, 15, 25) and Start, or Pause/Resume and Reset while a countdown is
  under way. The chosen duration is the presenter's; the countdown is the
  application's `CountdownTimer` (`mira/core/countdown_timer.py`), which
  keeps a deadline on an injected monotonic clock, arms one Scheduler
  callback for the time left (re-armed if it fires early) and ignores
  callbacks of an earlier generation, so it never drifts and finishes
  exactly once. Commands leave the window as `timer_requested`;
  `DesktopShell` sends them to the timer and its `"timer_changed"`
  snapshots to the presenter. The time left is pulled from the timer on
  every repaint, like the waveform. The panel closes like the controls and
  when the countdown finishes; a running countdown never expands the notch,
  is shown only while it is open for another reason, and keeps counting
  while compact.
- **Timer completion.** A finished countdown is state, not a notice: the
  timer stays FINISHED until `CountdownTimer.acknowledge()` returns it to
  idle (a plain `"timer_changed"`; `"timer_finished"` is emitted once, at
  the end, and never again). For exactly that long `deliver_timer` holds the
  TIMER_FINISHED embodiment condition — the success cue's CHEVRON eyes,
  held and breathing slightly instead of flashed, above TASK_RUNNING and
  below any cue — and the presenter holds TIMER_DONE: "Timer done!" and an
  OK button in the band, which no timeout, pointer movement or right click
  ends. Right click opens the controls over it; closing them shows it
  again. OK leaves the window as `timer_requested`, the shell calls
  `acknowledge()`, and the resulting IDLE snapshot releases both.
- **Timer chime.** The end is also heard, once. `SoundCues`
  (`mira/core/sound_cues.py`) is the one listener to `"timer_finished"` and
  asks the `SoundOutput` port (`mira/domain/sound.py`) for
  `Sound.TIMER_DONE`; nothing else — redraws, hover, acknowledgement,
  pause or reset — emits that event, so nothing else plays it. The desktop
  output is `QtSoundOutput` (`mira/adapters/qt_sound.py`): a `QSoundEffect`
  per sound, loaded by `prepare` at startup next to the capture backend's
  enumeration and played asynchronously, so the GUI thread never waits. A
  missing file or device, or a failing backend, is logged once and
  ignored, and `SoundCues` swallows anything an output raises, since it is
  called from inside the timer's finish. The sound is an original 0.8 s
  three-note FM arpeggio at -7 dBFS (`mira/sounds/timer_done.wav`),
  rendered with the standard library by `scripts/generate_sounds.py`; a
  test keeps the asset equal to what the script renders. Without a
  `sound_output`, `build_application` is silent.
  Activity alone never expands; it only chooses the content shown while open.
  A notice of a category that does not expand still becomes the content (seen
  on hover) and still makes the eyes react.
- **Lifetime and priority.** A notice shows for `notice_duration_ms`, on a
  timer from the injected `Scheduler` (the application's own in production).
  Whether it shows at once, replaces the showing one or waits its turn is
  decided by priority, in "Notice priority".
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
  OPEN_MAIN_WINDOW; integrated controls: TIMER, OPEN_SETTINGS, QUIT) and only those
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
| USB device connected / disconnected | real (kernel uevents) | device / info, with the device's kind (see "USB device notices") |
| audio output or input connected / disconnected | real (PipeWire) | device / info |
| default audio input or output changed | real (PipeWire) | device / info |
| network lost / limited / back online | real (NetworkManager) | connectivity / warning, success |
| "Low battery", "Something failed" | preview only (MIRA-TUNE) | alert |

- **Adapters** (`mira/adapters`) translate one facility into semantic events
  (`mira/domain/system_events.py`) and know nothing about notices. All are
  event-driven on the Qt loop: the kernel's `NETLINK_KOBJECT_UEVENT` socket
  under a `QSocketNotifier` (whole USB devices only, never hubs, named and
  classified from sysfs once configured); one `pw-dump --monitor` child process (audio nodes and the
  effective defaults; the first dump is the baseline); NetworkManager's
  `Connectivity` property over QtDBus. A missing facility makes the source
  report itself unavailable at start; none is required.
- **Producers** (`mira/core/notice_producers.py`) own the noise policy:
  device changes settle for 800 ms per device (a connect and disconnect that
  cancel announce nothing; a new device becoming the default announces only
  its arrival; a USB unplug waits 2.5 s for a re-enumeration, see "USB device
  notices"), unchanged repeats within 3 s are dropped, and connectivity is
  debounced for 3 s with the first state as baseline, so a short drop or
  flapping says nothing. Timers run on the `Scheduler` port.
- **Ownership.** The one runtime owns one `SystemNoticeProducer` with one
  instance of each source, built in `build_application`, started by
  `Application.start()` (idempotent) and stopped first by
  `Application.shutdown()`. Surface requests never create producers.

| Producer | created / started / stopped by | on failure |
|---|---|---|
| `TaskActivity`, `TaskNoticeProducer` | composition; live with the graph | — |
| USB (`LinuxUsbDeviceSource`) | producer | ENOBUFS (dropped messages) resynchronises the known devices with sysfs; any other socket error closes the socket and reopens it after 1 s, doubling to 30 s, back to 1 s after a minute of stable running, then resynchronises; never a busy loop; an exception handling one message drops that message only |
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

### Notice priority

`NotchPresenter` is the one authority over what the notch shows, so it is
also where notices are arbitrated; there is no second controller. Every
notice has a `NoticePriority` (`mira/domain/notice.py`, `notice_priority`):

| Priority | Notices | During a turn (listening, thinking, speaking) |
|---|---|---|
| CRITICAL | errors (any category), alerts | shown at once |
| INTERACTION | — (a turn of conversation) | — |
| FEEDBACK | task results | wait |
| PASSIVE | device and connectivity changes | wait |

The *floor* is the highest of: the showing notice's priority; INTERACTION
during a turn — the LISTENING reason, or activity LISTENING, THINKING or
SPEAKING (`INTERACTION_ACTIVITIES`), which the activity authority commits
for one exchange (attend → deliberate → conclude), so nothing slips in
between its steps; and CRITICAL while something else has the content band
(`_band_taken`), since a notice drawn under it could not be seen. Running
tasks are not a turn.

**The content band.** Owners are of two kinds:

| Owner | Examples | A notice below CRITICAL | A critical notice |
|---|---|---|---|
| Transient (`_transient_band`) | the integrated controls; the timer panel | waits | arriving: closes the owner and shows — an error must be seen and acted on; the timer panel's chosen duration is kept |
| Persistent (`_persistent_band`) | a finished countdown awaiting OK ("Timer done!") | waits — even across the end of a conversation — until OK | shows; the done panel is *suspended* while it shows (`_band_suspended`: not drawn, the countdown stays FINISHED, TIMER_DONE keeps the notch expanded, the eyes keep TIMER_FINISHED under the error cue) and returns when it ends |

Opening a transient owner is the user's own gesture: it pushes any showing
notice aside, critical or not, and that notice returns when the owner
closes. No notice is ever drawn, timed or reacted to underneath an owner.

**The unified notch, state by state.** What a notice of each kind does in
each situation (the band column wins over the turn column when both hold):

| Situation | Device, connectivity (PASSIVE) | Task result (FEEDBACK) | Error, alert (CRITICAL) | Device information |
|---|---|---|---|---|
| Idle, countdown running | shown; the countdown returns after it | shown | shown | refines the device's notice in place; shown alone only if announced |
| Listening, thinking, speaking (a turn) | waits for the turn's end | waits | shown over the waveform | joins the waiting notice |
| Controls or timer panel open | waits | waits | closes the panel and shows | joins the waiting notice |
| "Timer done!" awaiting OK | waits until OK | waits until OK | shows; "Timer done!" returns after it | joins the waiting notice |

A device that leaves while its arrival waits cancels it; information about
a device whose latest notice is its departure is dropped. The eyes react
when a notice is shown — never while it waits, never for a refinement —
and cue priority keeps a greeting from replacing a stronger cue.

| Rule | Behaviour |
|---|---|
| Preemption | Above the floor: shown at once, full duration. At the floor and about the same subject (same `NoticeDevice.key`, else same category): replaces (a device leaving replaces its arrival). A preempted notice of another subject goes back to wait, without a second reaction. |
| Deferral | Below the floor, or at it about another subject: waits. At most 3 wait; when full, the lowest-priority, oldest is dropped. |
| Coalescing | A newer notice about the same subject replaces the waiting one; an arrival and the same device's departure while waiting cancel out. The same notice as the one showing only restarts its time. |
| Expiration | Validity, eligible waiting and display are kept apart. *Validity*: a notice no longer true is gone at once (superseded by a newer one about its subject; an arrival cancelled by the departure); nothing waits longer than `MAX_NOTICE_AGE_MS` (60 s) in all, whatever holds it back. *Eligible waiting*: time spent waiting although only the queue holds it back — not counted during a turn or under an owner it cannot pass; it may last as long as every notice ahead needs at the configured pace plus `ELIGIBLE_GRACE_MS` (3 × 4 s + 2 s = 14 s by default), counted in 500 ms steps on the scheduler. *Display*: `notice_duration_ms` once shown. |
| Yielding | When a turn starts, a showing notice below INTERACTION goes back to wait: the conversation has the band from its first moment. When a persistent owner appears, a showing notice below CRITICAL does. When the user opens a transient owner, any showing notice does. Either way it returns later with its full time and no second reaction. |
| Restoration | When the floor drops (expiry, dismissal, the turn ends, an owner closes or is answered) the best waiting notice (highest priority, oldest among equals) is shown in the same resolution step, so the notch stays expanded from one to the next instead of collapsing between them. One at a time. |
| Refinement | Device information (`refine`) updates the notice about its device in place, showing or waiting; the first information or a charging change restarts a showing notice's time, a moving percentage does not. Shown on its own only if announced, never with a reaction; dropped if the device's latest notice is its departure. |

- **Reactions.** The eyes react when a notice *starts showing*
  (`subscribe_reactions`, bound with the controller by `bind_presenter`):
  not while it waits, not when refined, not for a repeat. So MIRA does not
  greet a device over its listening eyes, greets it when the notice appears
  after the conversation, and never greets twice. Cue priority still applies
  on top (ERROR > WARNING > SUCCESS > GREETING). Without a notch (main window
  only) `deliver_notice` applies the same rule to the eyes' own activity.
- **Expansion.** Unchanged and reason-based: a notice ending while LISTENING
  holds never collapses the notch, and a restored notice takes over the
  NOTICE reason in the same step.
- **Lifecycle.** Notice, age, eligibility and controls timers all run on
  the injected `Scheduler`, guarded by generations; `shutdown` cancels
  every one and drops the waiting notices, and nothing is published or
  reacted to afterwards. Device information arrives on the UI thread (the service's
  `Scheduler.submit` completion), so no Qt object is touched off it.
- **Not covered.** A turn longer than `MAX_NOTICE_AGE_MS` (60 s; a voice
  turn lasts at most about 42 s) drops what waited for it. The waiting
  queue is not persisted. Priorities are fixed, not user-configurable.

### USB device notices

```text
kernel uevent (add … bind / remove)
  └▶ LinuxUsbDeviceSource (adapters)   one event per physical device, read from sysfs:
       │                                name, manufacturer, DeviceCategory, identity;
       │                                never the serial
       ▼ DeviceEvent(kind=USB, change, name, key=devpath, category, manufacturer, identity)
  SystemNoticeProducer (core)          settle 800 ms · unplug waits 2.5 s (re-enumeration)
       │                                · bounce · mode switch · repeat 3 s
       ▼ "notice": Notice(DEVICE, INFO, "Pixel 8 connected", NoticeDevice(SMARTPHONE, connected))
  DesktopShell ─▶ deliver_notice ─┬▶ NotchPresenter: expands (device switch), phone symbol + label,
                                  │                  back after notice_duration_ms
                                  └▶ FaceController: GREETING cue on arrival only
```

- **One connection, one event.** A composite device produces one uevent per
  interface plus its own `add` and `bind`; only the device's are read. The
  kernel sends `add` before it configures the device, so the adapter
  announces when the device is configured: at `add` if sysfs already lists
  every interface of the active configuration (`bNumInterfaces`), otherwise
  at `bind`. A device removed before that announces nothing, and a device is
  never announced twice. A *configuration* change without re-enumeration
  (usbmuxd switching an iPhone to configuration 4) only replaces interfaces
  and announces nothing.
- **Identification.** `product` and `manufacturer` strings, `idVendor` and
  `idProduct` (debug log only) and each interface's class triple and
  description; nothing else, and never `serial`. `key` is the sysfs devpath
  (the port), used to correlate arrival and removal; `identity` is
  `usb:<idVendor>:<manufacturer>:<product>`, which a phone keeps in every USB
  mode (its `idProduct` changes), used to tell a re-enumeration from another
  device. A device that names itself nothing is called by its category
  ("USB storage connected"), never by a guess.
- **Classification** (`classify_usb_device`) prefers UNKNOWN to a guess.
  SMARTPHONE needs a known phone maker's vendor id (`PHONE_VENDORS`: Google,
  Samsung, Xiaomi, OnePlus, Motorola, Huawei, OPPO/realme, Sony Mobile, LG,
  HTC, ZTE, TCL/Alcatel) *and* a phone function — MTP (interface named
  "MTP"), PTP (`06`), Android debugging or fastboot (`ff/42/01`,
  `ff/42/03`) — or Apple's vendor id with a product named "iPhone". Then
  STORAGE (`08`), CAMERA (`0e` video; `06` still image only from a camera
  maker in `CAMERA_VENDORS`), AUDIO (`01`), INPUT (`03`), else UNKNOWN. So a
  phone in PTP mode is a smartphone, MTP or ADB from anyone else (a watch, a
  media player, a VR headset, a development board) is unknown, and a phone in
  charge-only mode that exposes no phone function is unknown, named by its
  own strings. `DeviceCategory` is domain vocabulary
  (`mira/domain/devices.py`); the USB rules and vendor lists stay in the
  adapter.
- **USB mode switching** is decided by the producer. Android re-enumerates
  on every mode switch (charging, file transfer, PTP, debugging): the kernel
  removes the phone and adds it again, usually 1–2 s later, with another
  `idProduct`. A USB disconnect therefore waits `USB_REENUMERATION_MS`
  (2.5 s) before it is announced, and within that window:

  | then arrives | announced |
  |---|---|
  | the same device (same `identity`), any port, same or a less specific category | nothing: a mode switch, or replugged at once |
  | the same device, now identifiable (category became known, or changed) | its arrival, e.g. charge-only → file transfer: "Pixel 8 connected" with the phone symbol |
  | a different device in the same port | the first one's disconnect at once, then the newcomer as usual |
  | nothing | the disconnect, 2.5 s after the unplug |

  A connect and disconnect within the 800 ms settle still cancel out, and
  flapping announces only the state it ends in. Audio devices keep the
  800 ms settle.
- **Failure and recovery.** A socket that cannot be opened at start makes
  the source unavailable (logged once). A persistent socket error closes it
  and reopens it after 1 s, doubling to 30 s, back to 1 s after a minute of
  stable running; one socket at a time, and shutdown cancels the retry. When
  messages may have been lost — the kernel dropped some (`ENOBUFS`) or the
  socket was reopened — the known devices are resynchronised with sysfs:
  devices that left are reported disconnected, devices that arrived
  connected, devices still there say nothing. An exception while handling
  one message, or in a listener, is logged and costs that message only.
- **Startup.** The socket is opened before sysfs is scanned, so no arrival
  falls between the two. Devices present then are the baseline and announce
  nothing; one still being configured is announced at its `bind`, as it is
  arriving.
- **Presentation.** `Notice.device` (`NoticeDevice`: category, connected)
  lets each embodiment show the kind of device. The notch maps it to a
  symbol — phone, USB stick, camera, headphones, keyboard, USB trident for
  unknown — and keeps the generic device symbol for unclassified notices
  (audio routes). A tone other than INFO still decides the symbol. The eyes
  play `EmbodimentCue.GREETING` (the success smile, 0.6 s, lowest priority:
  it never interrupts another cue) for an arrival, when its notice is
  shown; a removal gets none. A removal while the arrival still shows
  replaces it; a device arriving during a turn of conversation waits (see
  "Notice priority").
- **Threading and resources.** The uevent socket is read by a
  `QSocketNotifier` on the Qt main thread, at most 64 messages per wake-up
  (the notifier fires again for the rest), and every step after it is
  synchronous on that thread, so widgets are only touched from the UI thread
  and nothing blocks: the sysfs reads are a handful of small virtual files
  per arrival. No thread is started. One socket, one notifier and at most
  one retry timer exist; the producer holds one pending change per device.
  The socket asks for a 1 MiB receive buffer (capped by
  `net.core.rmem_max`). Ordinary device events are logged only with
  `MIRA_DEVICE_MONITOR_DEBUG=1`.
- **Configuration.** `MIRA_DEVICE_MONITOR=0` leaves the USB source out of
  `desktop_system_event_sources` (audio and connectivity are unaffected);
  `MIRA_DEVICE_MONITOR_DEBUG=1` logs the devices present at start and each
  arrival and removal with its classification. Expansion and duration are
  the notch's existing `automatic_expansion.device` and
  `notice_duration_ms`.
- **Limitations.** A real unplug is announced 2.5 s after it happens. Two
  devices with the same vendor, manufacturer and product strings (two
  identical sticks) swapped within 2.5 s count as one. A phone whose maker
  is not listed, or an iPad, is unknown (named by its strings); a phone
  maker's tablet is a "smartphone". A phone whose mode switch takes longer
  than 2.5 s is announced as disconnected and connected again. A device
  whose product string changes between modes is treated as another device.
  Names come
  from the device's own strings; no vendor database is consulted.
- **Next.** M2 — device information providers behind their own source or
  enrichment step (ADB for model and battery, `usbmuxd` for iPhones, UPower,
  storage capacity) — done for adb, UPower and sysfs storage, see "Device
  information"; nothing is shown that was not read. M3 — notice priority,
  deferral and several devices in turn, done (see "Notice priority");
  device-specific cues and user-configurable reactions remain.

### Device information

```text
SystemNoticeProducer ── "device_changed" (announced arrivals/departures, silent re-enumerations)
        ▼
DeviceInfoService (core)  registry: one generation per connection, ≤ 1 query in flight per device
        │ Scheduler.submit (worker thread)            on_complete (UI thread)
        ▼                                                   │
DeviceInfoProvider.query  (adapters)                        │ stale generation → discarded
  AdbDeviceInfoProvider   "adb" in services   adb devices -l; adb -t <id> shell getprop …; dumpsys battery
  UPowerPhoneProvider     "usbmux" in services upower --dump (kind "phone" only)
  UsbStorageInfoProvider  category STORAGE     sysfs …/block/<disk>/size
        ▼ InfoResult(DeviceInfo | problem, retry)
"device_info" (DeviceInfoUpdate: device, info, announce)
        ▼
DesktopShell ─▶ NotchPresenter.refine: replaces the arrival notice of the same device (NoticeDevice.key),
                                         or shows it alone if announce; never an eye cue
```

- **Lifecycle.** The service follows `"device_changed"`, i.e. the devices
  the user was told about: nothing is asked while an arrival settles or for
  a bounce. On an arrival it picks the first provider whose `supports`
  accepts the device and submits one query. Every connection gets a new
  generation; an answer for an older one (the device left or came back
  meanwhile) and anything after `shutdown` is discarded. A disconnect
  cancels the device's timers; a query already running answers to nobody.
- **What is published.** Only information that was read, only when it
  differs from what was last published for that device. `announce` is True
  for a connection's first information and for a change of charging state;
  a moving percentage only refines a notice that is already showing. A
  device that re-enumerates as itself (same `identity`) keeps what was
  known, so a USB mode switch announces nothing new.
- **Retries and refresh.** A retryable problem (phone not authorized yet,
  adb not listing it yet, a timeout) is asked again after 2, 4, 8, 16 and
  30 s, then left until the device reconnects. With
  `MIRA_DEVICE_INFO_REFRESH_S` (30–3600 s, default off) a device that
  reported a battery is asked again at that interval, one query at a time,
  only after the previous answer.
- **Text.** The notice's text is the device's name — the model a provider
  read, else the USB name, without its maker next to a battery reading
  ("iPhone", "Pixel 8") — and its `detail` the readings that exist:
  "84% · Charging", "100% · Full", "64 GB" (`device_info_name`,
  `device_info_detail`; `device_info_text` joins them for a one-line
  embodiment). The notch draws the detail after the name in a quieter tone
  on the same row, so every valid geometry stays valid;
  `expanded_layout.label_room` gives the detail its width first and the name
  the rest (never under three text sizes while the detail could give way),
  and both are elided from the end, so a long name shortens before the
  reading does.
- **Android (adb).** Needs `adb` installed, *USB debugging* enabled and the
  computer *authorized* on the phone; only a device whose descriptors offer
  the debug interface (`ff/42/01`) is asked, so no command runs otherwise.
  The phone is matched by its USB port (adb's `usb:3-2`) and addressed by
  `transport_id`; its serial (first column of `adb devices -l`) is dropped
  while parsing and never used, stored or logged, and adb's own messages are
  never logged. The battery comes from `dumpsys battery` on the phone
  (`level`/`scale`, `status`, `present`), so the computer's battery cannot be
  read by mistake. States: not installed, not listed (retry), unauthorized
  (retry), offline (retry), no permissions (udev), recovery and other modes,
  no `transport_id` (adb too old), several entries on one port, timeout
  (retry), gone. Timeouts: 8 s to list (it may start adb's server), 5 s for
  the shell command. The first query starts adb's server if needed, as any
  adb use does; nothing on the phone is changed.
- **iPhone (UPower).** UPower reads a *paired* iPhone's battery through
  libimobiledevice and usbmuxd and lists it as kind `phone`; the provider
  reads `upower --dump` and nothing else, so MIRA links no iOS library.
  Only `phone` blocks are read — never `battery` (this computer's),
  `line-power` or the display device — and a percentage counts only with a
  determined state (charging, discharging, fully-charged, pending-charge).
  Matching is by elimination: asked only for a device offering Apple's
  multiplexer, and only if UPower lists exactly one phone. Untrusted phone:
  UPower lists none, retried while the user may still accept the prompt.
- **USB storage (sysfs).** The disk under the USB device's own sysfs tree
  and its `size` in 512-byte sectors; nothing mounted, opened or listed, so
  no file, name or label is seen. No disk yet: retried; size 0 (card reader
  without a card) or several disks: nothing claimed.
- **Threading.** Queries run on Qt's global thread pool through
  `Scheduler.submit` and are bounded by their timeouts; answers, bus events
  and every widget change happen on the UI thread. Providers never raise
  (`_safe_query` turns an exception into a problem).
- **Cancellation.** `DeviceInfoService.shutdown` calls every provider's
  `cancel` first. The adb and UPower providers run their commands through
  `CancellableRunner` (`mira/adapters/subprocesses.py`), which kills
  exactly the child processes it started — never adb's server or another
  program's process — and refuses new ones, so a query in progress ends at
  once ("MIRA is shutting down") instead of waiting out its timeout (up to
  13 s for a hung adb), and the workers are done before the scheduler
  stops. Starting a child and cancelling share a lock; a child finishing as
  it is killed is harmless; every child is reaped.
- **Configuration.** `MIRA_DEVICE_INFO=0` builds no providers;
  `MIRA_DEVICE_INFO_REFRESH_S` as above; problems are logged once per
  connection with `MIRA_DEVICE_MONITOR_DEBUG=1` (`[devices] adb: no
  information on 'Google Pixel 8': …`).
- **Limitations.** Android was validated only with scripted adb output;
  the UPower iPhone path only with scripted output and against this
  computer's real UPower (which correctly lists no phone and never the host
  battery). Devices present when MIRA starts are not asked (no arrival was
  announced). With two iPhones connected neither gets a battery. A phone
  model that adb or UPower does not report falls back to the USB name.
  Peripherals UPower knows (wireless mice, headsets) are not matched to USB
  devices. Long lines are elided.
- **Next.** A persistent device panel in the notch (battery visible on
  hover, not only in a notice); low-battery alerts; Bluetooth devices
  through UPower or BlueZ; libimobiledevice directly if UPower proves too
  coarse; ADB over Wi-Fi is out of scope (no USB arrival).

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
| Implemented | one runtime per user with surface requests; lazy main window and notch; all-workspace presence on X11; readable expanded content by default; integrated notch controls; USB, audio-topology and connectivity notices with supervised PipeWire monitoring; USB device classification with per-kind notch symbols and an arrival greeting; device information providers; notice priority with deferral, coalescing and restoration; the countdown timer as a content-band owner; per-category expansion; SUCCESS/WARNING/ERROR cues and the TASK_RUNNING condition; keyboard listening activation; the runtime's microphone capture with real level and waveform in the notch |
| Prepared | `SystemEventSource` port; `EyeShape` in `EmbodimentFrame`; run ids for asynchronous actions; `ListeningActivationMode.WAKE_WORD`, activation sources and capture leases for a wake-word detector; `AudioFrame` for speech processing |
| Future | wake word; voice activity detection; speech-to-text and text-to-speech; asynchronous (long) actions; desktop-notification source with a filter policy; Wayland workspace presence |

### Audio input and listening

```text
keyboard (in-window M, global Ctrl+Alt+M)          wake word (see "Voice activation")
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

### Voice activation and utterances

```text
AudioInputService (one capture, leases: WAKE_MONITORING, CONVERSATION, DIAGNOSTICS)
        │ frames (main thread)
        ▼
ListeningController ──▶ VoiceProcessor (worker via Scheduler.submit; one batch at a
        │                │               time, ≥4 frames, ≤64 waiting, oldest dropped)
        │                ├── WakeWordDetector   (while monitoring and no session)
        │                └── VoiceActivityDetector (while a session runs)
        │ results (main thread)
        ├── wake word heard ──▶ activation ──┐
        ├── keyboard toggle ──▶ activation ──┤
        │                                    ▼
        │                ListeningSession (pure, sample-clocked)
        │                WAITING_FOR_SPEECH ─onset─▶ SPEECH_ACTIVE ─silence─▶ utterance
        │                    │ timeout                  │ max duration
        ▼
"listening_changed" (listening, speech_active)   "utterance_ready" (Utterance)  → [future STT]
```

Four things stay distinct, and none implies another:

| | means | shown as |
|---|---|---|
| audio capture | samples exist | nothing by itself (the system's microphone indicator) |
| wake monitoring | samples are inspected for the wake phrase | nothing: MIRA stays idle, the notch compact |
| listening session | MIRA waits for, then records, one utterance | LISTENING on every surface; the notch opens with the real waveform |
| speech active | the user is speaking inside a session | `ListeningStatus.speech_active`; an orthogonal signal, not another activity |

And: an `UtteranceReady` is audio, not a transcription. Nothing consumes it
yet beyond a metadata log line.

- **Ports** (`mira/domain/voice.py`): `WakeWordDetector.process(frame) →
  [WakeWordDetection]`, `VoiceActivityDetector.process(frame) →
  [SpeechWindow]`. Nothing outside the adapters sees an engine object.
  `WakeWordConfig` holds the phrase as text and a 0–1 sensitivity, so a new
  name is a setting, not code.
- **Engines** (`mira/adapters/sherpa_voice.py`, optional `voice` extra):
  sherpa-onnx, Apache-2.0, offline, its own ONNX runtime, x86-64 and ARM
  wheels. The keyword spotter is an open-vocabulary streaming zipformer
  (English gigaspeech, 3.3 M parameters, int8): the phrase is spelled into
  its BPE tokens with sentencepiece at load, no training. Speech detection
  is Silero, one decision per 512-sample window through the bare `VadModel`
  (sherpa's buffered detector was not used: its buffer kept doubling).
  Without the extra or the models, wake word is unavailable (and said so)
  and speech is found by `EnergyVoiceActivityDetector`: 12 dB above the
  quietest window of the last 2 s, a floor that outlives sessions.
  Considered and not chosen: Porcupine (needs an online-validated access
  key), openWakeWord (non-commercial pretrained models, a fixed set of
  words, scipy and scikit-learn), Vosk (a full recogniser for one word),
  PocketSphinx (older, less accurate), silero-vad from PyPI (needs torch).
- **Session policy** (`SegmentationPolicy`, clocked by sample count): onset
  needs 3 speech windows among the last 5 (about 100 ms), so one noisy
  window does not start speech; the end needs 0.8 s without speech
  (configurable), so pauses between words do not end it; 0.25 s of tail is
  kept; 0.3 s of pre-roll before the onset is included (never audio from
  before the session); waiting times out quietly after 8 s (configurable);
  an utterance is capped at 30 s, where it is discarded as a stuck detector
  or constant noise. Memory is bounded in every state.
- **Activation semantics.** One session at a time. The keyboard toggles: it
  starts a session or cancels the running one (no utterance). A wake word
  starts a session; it is ignored while one runs and for 1.5 s after one
  ends, so one spoken phrase never starts two. The spotter's stream is reset
  after each hit.
- **Wake-word exclusion.** A wake-started session begins with the frame
  after the one the spotter reported the phrase in. Frames captured after
  it — later in the same worker batch or still queued — are re-routed to
  the VAD in order, with one reset, so the command's first words are kept.
  The boundary is the spotter's own frame, not a guessed duration; part of
  the phrase's last syllable can remain in the next frame.
- **Off the UI thread.** Detection and segmentation inference run in
  `VoiceProcessor` on the thread pool, one batch at a time (the engines'
  streaming state is never shared between threads), bound per batch so a
  settings change never swaps an engine mid-batch. Results come back on
  the main thread, where the session and all events live.
- **Lifecycle.** Monitoring holds its own lease, so capture runs in wake
  mode while MIRA is idle; a session adds the conversation lease on the same
  capture. A lost microphone ends the session (one notice) and stops
  monitoring (reported in diagnostics; restarting the runtime or changing
  the setting resumes it). Shutdown ends the session, stops the worker and
  releases every lease.
- **Privacy.** Wake-word spotting and speech detection run locally; no
  audio leaves the machine and none is written anywhere — not monitoring
  audio, not utterances. Speech-to-text is not implemented and its provider
  and privacy policy are not chosen.
- **Measured** on the development laptop (one core): capture alone 1.6%,
  wake monitoring about 10% in process (12% for the runtime with UI), a
  waiting session 5%, keyboard idle 3%. Startup to ready about 1 s; the
  audio backend's first enumeration after boot takes about 1.7 s, later
  ones about 0.4 s. On the model's bundled recordings the spotter fired
  exactly on the spoken keywords and never for "mira"/"nero"; with a live
  Italian-spoken "mira" it fired about 4 times in 5 at sensitivity 0.8 (the
  default) and once at 0.5, with no false wake in a few minutes of room
  audio and speech. These are smoke observations, not benchmarks.

**Not implemented:** speech-to-text, text-to-speech, spoken responses,
speaker identification, recording.

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
