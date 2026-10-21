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

Camera capture is optional and not part of that install or of CI. To develop
or try it, add the `camera` extra; `constraints.txt` records the tested OpenCV
and numpy versions:

```bash
venv/bin/python -m pip install --constraint constraints.txt --editable ".[dev,camera]"
```

Local voice activation (wake word, Silero speech detection) is the optional
`voice` extra, also outside CI; its models are separate downloads
([configuration.md](configuration.md#voice-engine-and-models)). Tests never
need it: they use fake engines, and the few that exercise the real models
skip when they are not installed.

```bash
venv/bin/python -m pip install --constraint constraints.txt --editable ".[dev,voice]"
```

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

### Notch launchers

`MIRA` and `MIRA-NOTCH` are two ways into one M.I.R.A. runtime per user.
The first one started becomes the runtime; a later one asks it to show its
surface and exits immediately (`[mira] connected to the running M.I.R.A.
(pid …)`), so starting `MIRA-NOTCH` while `MIRA` runs adds the notch to it,
and the other way round. `MIRA-TUNE` is the standalone tuning tool and is
not part of that runtime. All use the same `venv` and symlink resolution:

| Command | Script | Runs |
|---|---|---|
| `MIRA` | `bin/mira` | `python -m mira.main`: show the main window (starting the runtime if needed). Closing the window ends the runtime unless a notch is open. |
| `MIRA-NOTCH` | `bin/mira-notch` | `python -m mira.main --notch`: show the notch (starting the runtime if needed), with the saved configuration and real task, device and connectivity notices; double-click opens the main window, closing it leaves the notch running. Quit from the notch's own controls (right click) or Ctrl+C. |
| `MIRA-TUNE` | `bin/mira-tune` | `python -m mira.ui.notch --tune`: a standalone notch (no application behind it) with its tuning window, which changes it live, previews notices and eye cues, and can Save, Reload and Reset defaults. Closing the window quits. |

In a running M.I.R.A., `M` (in its windows, outside text fields) or
`Ctrl+Alt+M` (anywhere) toggles listening: the notch opens and shows the
microphone's real waveform. With wake-word activation chosen in the
settings, saying the wake phrase does the same. A finished utterance is
logged as `[listening] off: utterance ready (duration, samples, source)`;
nothing transcribes it yet. Automated tests never open a microphone; they
use fake capture backends.

`python -m mira.ui.notch --notch-only` still shows the standalone notch
without the tuning window, for development; it has no application and so no
real notices.

```bash
ln -s "$PWD/bin/mira-notch" ~/.local/bin/MIRA-NOTCH
ln -s "$PWD/bin/mira-tune" ~/.local/bin/MIRA-TUNE
MIRA-TUNE --config /tmp/notch-experiment.json   # any other configuration file
```

The configuration file and its rules are in
[configuration.md](configuration.md#notch).

The runtime's lock and socket live in `$XDG_RUNTIME_DIR/mira` (else a
private `/tmp/mira-<uid>`); a crash needs no cleanup. Tests point
`XDG_RUNTIME_DIR` at a fresh directory each (`tests/conftest.py`), so they
never reach a MIRA you are running.

The system notices need, at run time: the kernel's
uevent netlink socket (USB), `pw-dump` from PipeWire (audio devices) and
NetworkManager on the system bus (connectivity). Each is optional. Tests use
fakes for all of them and never need a device, an audio server, a bus or an
X server.

## Repository layout

```text
mira/
├── domain/       # technology-independent vocabulary, scheduler and camera ports, micro-motions, playback
├── memory/       # session retention
├── messaging/    # synchronous event bus
├── actions/      # contracts, execution policy, registry, executor, handlers
├── cognition/    # rule and optional local-LLM inference, response building
├── core/         # turn, activity, interaction, and behavior orchestration; camera capture
├── adapters/     # Qt scheduler and OpenCV camera implementations
├── application/  # construction and wiring
├── ui/           # Qt window, chat panel, debug drawer, face/ rendering
├── config/       # packaged expression profiles
└── main.py       # process entry point
bin/mira          # launcher script; bin/mira-notch (ambient notch), bin/mira-tune (tuning)
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
| Embodiment | `test_embodiment_*.py`, `test_micro_motion*.py`, `test_face_*.py`, `test_expression_store.py` |
| Camera | `test_camera_*.py`, `test_opencv_camera.py` |
| System events and notices | `test_system_event_adapters.py`, `test_notice_producers.py`, `test_usb_device_notices.py` |
| Memory and messaging | `test_memory_contracts.py`, `test_messaging_contracts.py` |

Shared test doubles live in `tests/doubles.py` (covered by
`test_shared_fixtures.py`); `tests/layering_harness.py` supports the checker
tests. `ManualScheduler` from `mira.domain.scheduler` drives full turns
deterministically without a Qt event loop. No test contacts a real Ollama
server, and no test opens a webcam or needs OpenCV: `tests/camera_doubles.py`
provides a scripted `CameraSource`, and the adapter tests replace
`cv2.VideoCapture` with a fake. The camera lifecycle tests use real threads,
synchronized on events rather than sleeps.

A physical camera needs a manual check after camera changes: the app starts
with `MIRA_CAMERA=1`, the debug drawer reports `active` with a sane size,
quitting releases the device (another application can open it afterwards), and
an invalid `MIRA_CAMERA_DEVICE` or a camera held by another application reports
`unavailable` while the rest of the app keeps working.

USB device notices need a manual check after changes to the USB source,
the producers or the notch's notice path. The tests use a fake kernel
(`tests/usb_doubles.py`: a sysfs tree and a socket pair), so only a real
plug proves the kernel side. Quit any running MIRA first — a second launch
only forwards to the running runtime, whose environment is the one that
counts — then start it with the USB debug log, keeping only MIRA's own
device and notice lines:

```bash
MIRA_DEVICE_MONITOR_DEBUG=1 venv/bin/python -m mira.main --notch 2>&1 \
  | grep --line-buffered -E '^\[(devices|notices)\]' | tee usb-check.log
```

Ctrl+C quits through the orderly shutdown. Expected timings: an arrival
shows about 0.8 s after the device is ready, an unplug about 2.5 s after it
happens, and a notice stays `notice_duration_ms` (4 s by default).

| # | Scenario | Do | Expect |
|---|---|---|---|
| 1 | Android smartphone | Plug in an unlocked phone; select *File transfer* | One expansion: phone symbol, "<name> connected", a brief smile; one `[devices] connected …: smartphone` line. After 4 s the notch is as before. Unplug: "<name> disconnected", same symbol, no smile. |
| 2 | USB storage | Plug in a USB stick, wait, unplug | Storage symbol, "<name> connected"; later "<name> disconnected". No disconnect from mounting or the file manager opening. |
| 3 | Rapid reconnect | Unplug and replug the phone in the same port within about 2 s; then do it 5 times in a row | Nothing at all, or — if a replug took longer than 2.5 s — a disconnect then a connect, never a burst. `[devices]` shows every remove and add. |
| 4 | USB mode switching | With the phone connected, switch *File transfer* → *PTP* → *USB tethering/No data transfer* → *File transfer*, a few seconds apart; with developer options, toggle *USB debugging* | Nothing for switches between phone modes. From *No data transfer* (shown as an unknown USB device, named) to *File transfer*: once, "<name> connected" with the phone symbol. Note the time between `disconnected` and `connected` lines; above 2.5 s the switch shows as a disconnect and a connect. |
| 5 | Connected during startup | (a) Leave the phone plugged in and start MIRA; (b) plug it in while MIRA is starting | (a) A `present at start …` line and no notice; unplugging later announces it. (b) Either the same as (a), or one arrival if it was still configuring (`configuring at start …`). Never two. |
| 6 | Unplugged during the notch animation | Plug in, and unplug while the notch is still expanding or showing "connected" | About 2.5 s after the unplug the content becomes "<name> disconnected" (or, within the first 0.8 s, nothing at all is shown); the notch collapses 4 s later. No stuck expansion, no error. |
| 7 | Monitoring disabled | Start with `MIRA_DEVICE_MONITOR=0` | `[notices] USB device monitoring disabled`; plugging shows nothing. |

Without hardware, MIRA-TUNE's *Phone connected*, *Phone disconnected* and
*Storage connected* previews go through the same presenter and eye path.

**Diagnostics without serial numbers.** MIRA's `[devices]` lines carry the
port, category, name, vendor:product id and interface classes, never a
serial. The kernel's own messages are serial-free too — use `--kernel`, not
`--udev`, whose events add `ID_SERIAL` from udev's database:

```bash
udevadm monitor --kernel --property --subsystem-match=usb | tee usb-uevents.log
```

What MIRA reads from sysfs for every connected device, without `serial`
(do not use `lsusb -v`, which prints it):

```bash
for d in /sys/bus/usb/devices/*; do
  n=${d##*/}; case $n in *:*|usb*) continue;; esac
  printf '%s %s:%s cfg=%s "%s" "%s" |' "$n" "$(cat $d/idVendor)" "$(cat $d/idProduct)" \
    "$(cat $d/bConfigurationValue)" "$(cat $d/manufacturer 2>/dev/null)" "$(cat $d/product 2>/dev/null)"
  for i in $d/$n:*; do
    printf ' %s/%s/%s(%s)' $(cat $i/bInterfaceClass $i/bInterfaceSubClass $i/bInterfaceProtocol) \
      "$(cat $i/interface 2>/dev/null)"
  done; echo
done
```

Product strings are the device's own and may name its owner (a phone
renamed "Anna's Pixel"); check a log before sharing it.

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
