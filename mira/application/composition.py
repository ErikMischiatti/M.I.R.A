"""The one place that constructs the MIRA application graph.

Before this module, `MainWindow.__init__` built the graph: it created the event
bus, the state manager, the Qt scheduler, the brain, the interaction manager and
the embodied-behaviour layer, and only then began building widgets. A UI class
therefore decided which scheduler the turn lifecycle ran on, and the graph could
not be assembled at all without a `QMainWindow` — so nothing could inspect it,
and no test could substitute a collaborator without reaching inside the window.

`build_application` moves that decision here and changes nothing else. The
construction order below is the order `MainWindow` used, preserved deliberately
rather than incidentally, because two parts of it are observable:

  - `EventBus.emit` fans out in subscription order
    (`mira/messaging/events.py:12-14`). `InteractionManager` and
    `EmbodiedBehavior` both subscribe to `response_ready` and to the three input
    events, and they subscribe from their constructors, so the order these two
    are built in *is* the order their handlers run in.
  - `StateManager`, the bus and the scheduler are shared by aliasing, not
    rebuilt per component. A second bus or a second scheduler would leave every
    component constructing successfully while events reached nobody and timers
    sat on a scheduler nothing drives.

`scheduler`, `camera_source` and `clock` are the only injection seams, and each exists
for the same two reasons rather than for symmetry: it is an adapter choice, and
it is what lets the graph be built without the technology behind it — no Qt
event loop for the scheduler, no webcam or OpenCV for the camera, no real
time passing for the countdown timer. Passing `ManualScheduler` from
`mira.domain.scheduler` yields the real graph, fully wired, driven by an
explicit clock — which is how `tests/test_application_composition.py` exercises
a whole turn without a `QApplication`.

Deliberately plain: a frozen dataclass and one function. No container, no
registry, no service locator, no reflection. Adding a component means adding a
line here and a field below, which is the point — the graph stays readable as
source.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mira.adapters.opencv_camera import OpenCVCameraSource
from mira.application.camera_config import camera_configuration_from_environment
from mira.application.device_config import DeviceMonitorConfiguration, device_monitor_configuration_from_environment
from mira.application.scheduling import build_scheduler
from mira.core.activity_authority import ActivityAuthority
from mira.core.brain import Brain
from mira.core.camera_capture import CameraCapture
from mira.core.countdown_timer import CountdownTimer
from mira.core.sound_cues import SoundCues
from mira.core.embodied_behavior import EmbodiedBehavior
from mira.core.interaction_manager import InteractionManager
from mira.core.listening import ListeningController
from mira.core.audio_input import AudioInputService
from mira.core.device_info import DeviceInfoService
from mira.core.notice_producers import SystemNoticeProducer, TaskNoticeProducer
from mira.core.task_activity import TaskActivity
from mira.core.state_manager import StateManager
from mira.domain.audio import AudioCaptureBackend
from mira.domain.camera import CameraSource
from mira.domain.device_info import DeviceInfoProvider
from mira.domain.scheduler import Scheduler
from mira.domain.sound import SilentSoundOutput, SoundOutput
from mira.domain.system_events import SystemEventSource
from mira.messaging.events import EventBus


@dataclass(frozen=True)
class Application:
    """The constructed graph, held by the object that composed it.

    Frozen because composition happens once: rebinding a field after the fact
    would leave the components wired to the previous value and nothing else
    would notice. It is a record of what was built, not a place to look things
    up — components already hold their own collaborators, injected explicitly.

    The UI needs only `event_bus` and `brain`; the remaining fields are here so
    the graph can be inspected and driven in tests, and so ownership is stated
    in one readable place rather than inferred from constructor arguments.

    `StateManager` is deliberately *not* a field. Exposing it handed any holder
    of this record a live state manager without importing the type, which is
    the one route `scripts/check_state_authority.py` cannot see: its Rule B
    keys on the import, and an aliased receiver defeats Rule A. Reach the state
    through `activity` instead.
    """

    event_bus: EventBus
    activity: ActivityAuthority
    scheduler: Scheduler
    brain: Brain
    interaction_manager: InteractionManager
    embodied_behavior: EmbodiedBehavior
    camera: CameraCapture
    tasks: TaskActivity
    task_notices: TaskNoticeProducer
    system_notices: SystemNoticeProducer
    device_info: DeviceInfoService
    audio: AudioInputService
    listening: ListeningController
    timer: CountdownTimer
    sound: SoundOutput
    sound_cues: SoundCues

    def start(self) -> None:
        """Start the long-running resources: camera capture and system watching."""
        self.camera.start()
        # Ready before the first device is announced.
        self.device_info.start()
        self.system_notices.start()

    def shutdown(self) -> None:
        """Stop the resources owned by this application graph.

        The camera goes first and releases its device before the scheduler
        stops; the two are independent, so the order only makes the device
        free as early as possible.
        """
        # Audio first: listening lets go of the microphone, which is then
        # released before anything else.
        self.listening.shutdown()
        self.audio.shutdown()
        # Device information, the system sources and the countdown next:
        # their pending timers are cancelled while the scheduler still
        # accepts it, and a query still running answers to nobody.
        self.device_info.shutdown()
        self.system_notices.shutdown()
        self.timer.shutdown()
        # Nothing more to play; the output lets go of the audio device.
        self.sound_cues.shutdown()
        self.sound.shutdown()
        self.camera.shutdown()
        self.scheduler.shutdown()


def build_application(
    *,
    scheduler: Scheduler | None = None,
    camera_source: CameraSource | None = None,
    system_event_sources: Sequence[SystemEventSource] = (),
    audio_backend: AudioCaptureBackend | None = None,
    device_info_providers: Sequence[DeviceInfoProvider] = (),
    device_monitor: DeviceMonitorConfiguration | None = None,
    clock: Callable[[], float] | None = None,
    sound_output: SoundOutput | None = None,
) -> Application:
    """Construct and wire the application graph.

    `scheduler` defaults to the production `QtScheduler` (`build_scheduler`),
    which binds its home thread at construction (`mira/adapters/qt_scheduler.py:143`). That makes the
    *caller's* thread the serialized context, so production must call this from
    the Qt main thread — `mira.main` does, after creating the `QApplication`,
    which is where `MainWindow` used to build it.

    `camera_source` is the second adapter choice. When omitted, the
    `MIRA_CAMERA*` environment decides: an `OpenCVCameraSource` when the camera
    is enabled, otherwise no source and a disabled capture. Tests pass a fake
    source, so no test needs a webcam.

    `audio_backend` is how the runtime's one `AudioInputService` captures
    (`mira.application.audio.desktop_audio_backend` on a desktop); by default none, so the service
    reports itself unavailable and no test ever opens a microphone.

    `clock` is the monotonic clock the countdown timer reads its deadline
    from, by default `time.monotonic`; tests pass a fake one and advance it
    together with a `ManualScheduler`.

    `sound_output` plays MIRA's short sounds (`desktop_sound_output` on a
    desktop); by default a silent one, so no test ever opens a speaker.

    `system_event_sources` are the platform adapters whose changes become
    notices (`desktop_system_event_sources` for a Linux desktop); by default
    none, so a test or a headless runtime watches nothing.

    `device_info_providers` learn more about connected devices
    (`desktop_device_info_providers`); by default none, so no test runs a
    command. `device_monitor` supplies their refresh interval and debug
    logging; by default no refresh and no logging (not the environment).

    No side effects: nothing is started, no state transition is triggered, no
    timer is armed and no device is opened. The returned graph is idle until
    `Application.start()` and the UI feed it.
    """
    event_bus = EventBus()
    state_manager = StateManager(event_bus)
    # One authority over the shared face state, shared by every component
    # that used to write it. Two authorities would each commit against the
    # same StateManager and neither would be authoritative.
    activity = ActivityAuthority(state_manager)
    scheduler = build_scheduler() if scheduler is None else scheduler

    brain = Brain(event_bus, activity, scheduler=scheduler)

    # These next two lines are the order-sensitive pair. Both subscribe from
    # their own constructors, and both handle `response_ready` and the three
    # input events, so the order they are built in is the order their handlers
    # run in. `Brain` above subscribes to nothing — it only emits — so its
    # position is free. Interaction first, embodiment second, as before the move.
    interaction_manager = InteractionManager(event_bus, activity)
    embodied_behavior = EmbodiedBehavior(event_bus, activity, scheduler=scheduler)

    if camera_source is None:
        camera_configuration = camera_configuration_from_environment()
        if camera_configuration.enabled:
            camera_source = OpenCVCameraSource(camera_configuration.settings)
    camera = CameraCapture(camera_source)

    # Subscribed before the notice producer, so a finishing task is no
    # longer running by the time its result is announced.
    tasks = TaskActivity(event_bus)
    # Both only emit "notice"; how a notice looks is each embodiment's choice.
    task_notices = TaskNoticeProducer(event_bus)
    system_notices = SystemNoticeProducer(event_bus, scheduler, system_event_sources)
    # Follows the devices the producer announces; asks providers off-thread.
    device_monitor = DeviceMonitorConfiguration() if device_monitor is None else device_monitor
    device_info = DeviceInfoService(
        event_bus, scheduler, device_info_providers,
        refresh_interval_s=device_monitor.refresh_s, debug=device_monitor.debug,
    )
    # The runtime's one owner of the microphone; idle until someone leases it.
    audio = AudioInputService(event_bus, audio_backend)
    # The one decision whether MIRA listens; activation sources only ask.
    listening = ListeningController(event_bus, audio, scheduler)
    # The one countdown. A finished one is state awaiting the user, shown by
    # each embodiment from "timer_changed" — not a transient notice.
    timer = CountdownTimer(event_bus, scheduler, clock=time.monotonic if clock is None else clock)
    # Its end is also heard, once: the timer itself knows nothing of sound.
    sound = SilentSoundOutput() if sound_output is None else sound_output
    sound_cues = SoundCues(event_bus, sound)

    return Application(
        event_bus=event_bus,
        activity=activity,
        scheduler=scheduler,
        brain=brain,
        interaction_manager=interaction_manager,
        embodied_behavior=embodied_behavior,
        camera=camera,
        tasks=tasks,
        task_notices=task_notices,
        system_notices=system_notices,
        device_info=device_info,
        audio=audio,
        listening=listening,
        timer=timer,
        sound=sound,
        sound_cues=sound_cues,
    )


def desktop_system_event_sources(
    device_monitor: DeviceMonitorConfiguration | None = None,
) -> list[SystemEventSource]:
    """The system watchers for a Linux desktop; none elsewhere.

    USB arrivals (kernel uevents), audio devices (PipeWire) and network
    connectivity (NetworkManager). Each one that cannot run here reports
    itself unavailable when started; none is required. Built only when asked
    for: they need a running Qt event loop.

    `device_monitor` decides whether USB devices are watched, and with debug
    logging; by default the `MIRA_DEVICE_MONITOR*` environment does.
    """

    if not sys.platform.startswith("linux"):
        return []
    from mira.adapters.linux_usb import LinuxUsbDeviceSource
    from mira.adapters.networkmanager import NetworkManagerConnectivitySource
    from mira.adapters.pipewire_audio import PipeWireAudioSource

    if device_monitor is None:
        device_monitor = device_monitor_configuration_from_environment()
    sources: list[SystemEventSource] = []
    if device_monitor.enabled:
        sources.append(LinuxUsbDeviceSource(debug=device_monitor.debug))
    else:
        print("[notices] USB device monitoring disabled (MIRA_DEVICE_MONITOR)")
    return [*sources, PipeWireAudioSource(), NetworkManagerConnectivitySource()]


def desktop_device_info_providers(
    device_monitor: DeviceMonitorConfiguration | None = None,
) -> list[DeviceInfoProvider]:
    """The device information providers for a Linux desktop; none elsewhere.

    Android phones through adb, iPhones through UPower, USB storage through
    sysfs. Each is asked only about devices it supports and reports a
    missing tool as a problem, never an error; none is required. Off with
    `MIRA_DEVICE_INFO=0` (by default the environment decides).
    """

    if not sys.platform.startswith("linux"):
        return []
    if device_monitor is None:
        device_monitor = device_monitor_configuration_from_environment()
    if not device_monitor.info:
        print("[notices] device information disabled (MIRA_DEVICE_INFO)")
        return []
    from mira.adapters.adb import AdbDeviceInfoProvider
    from mira.adapters.upower_phone import UPowerPhoneProvider
    from mira.adapters.usb_storage import UsbStorageInfoProvider

    return [AdbDeviceInfoProvider(), UPowerPhoneProvider(), UsbStorageInfoProvider()]
