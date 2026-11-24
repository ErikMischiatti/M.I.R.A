"""Audio input: levels, smoothing, waveform reduction and the runtime service.

Every signal here is synthetic PCM with known values, and every backend a
fake: no test opens a microphone.
"""

from __future__ import annotations

import math
import struct
from array import array

import pytest

from mira.core.audio_input import AUDIO_STATUS_EVENT, AudioInputService
from mira.domain.audio import (
    FLOOR_DB,
    AudioFrame,
    AudioInputInfo,
    AudioInputLevel,
    CapturePurpose,
    CaptureStatus,
    LevelEnvelope,
    WaveformReducer,
    measure,
    to_display,
)
from mira.domain.scheduler import ManualScheduler
from mira.messaging.events import EventBus

RATE = 16000


def pcm(values) -> bytes:
    return array("h", [int(v) for v in values]).tobytes()


def frame(values, t=0.0, rate=RATE) -> AudioFrame:
    return AudioFrame(pcm(values), rate, t)


def sine(amplitude, count=1600, period=40):
    return [amplitude * math.sin(2 * math.pi * i / period) for i in range(count)]


# --- Levels -------------------------------------------------------------------------------


def test_silence_measures_zero():
    level = measure(frame([0] * 800, t=1.5))

    assert level == AudioInputLevel(0.0, 0.0, 1.5)
    assert measure(frame([], t=2.0)).rms == 0.0


def test_a_constant_amplitude_has_equal_rms_and_peak():
    level = measure(frame([16384] * 800))

    assert level.rms == pytest.approx(0.5) and level.peak == pytest.approx(0.5)


def test_a_symmetric_wave_measures_by_magnitude():
    square = [8192, -8192] * 400
    level = measure(frame(square))

    assert level.rms == pytest.approx(0.25) and level.peak == pytest.approx(0.25)
    sine_level = measure(frame(sine(32000)))
    assert sine_level.rms == pytest.approx(32000 / 32768 / math.sqrt(2), rel=1e-3)


def test_near_clipping_stays_within_full_scale():
    level = measure(frame([32767, -32768] * 400))

    assert level.peak == 1.0 and level.rms == pytest.approx(1.0, abs=1e-4)
    assert 0.0 <= level.rms <= 1.0


def test_the_display_scale_is_logarithmic_and_bounded():
    assert to_display(0.0) == 0.0
    assert to_display(1.0) == 1.0
    assert to_display(10 ** (FLOOR_DB / 20)) == pytest.approx(0.0)
    assert to_display(0.1) == pytest.approx(2 / 3)  # -20 dBFS
    # Ordinary speech is far from the bottom, unlike raw magnitude.
    assert to_display(0.05) > 0.5


def test_frames_reject_impossible_values():
    with pytest.raises(ValueError):
        AudioFrame(b"\x00\x01\x02", RATE, 0.0)
    with pytest.raises(ValueError):
        AudioFrame(b"", 0, 0.0)


# --- Smoothing ----------------------------------------------------------------------------


def run_envelope(envelope, amplitude, seconds, start, step=0.02):
    t = start
    values = []
    while t < start + seconds - 1e-9:
        t += step
        values.append(envelope.update(AudioInputLevel(amplitude, amplitude, t)))
    return values, t


def test_speech_rises_fast_and_silence_falls_slowly():
    envelope = LevelEnvelope(attack_s=0.03, release_s=0.35)
    run_envelope(envelope, 0.0, 0.2, 0.0)
    target = to_display(0.1)

    rising, t = run_envelope(envelope, 0.1, 0.15, 0.2)  # 5 attack time constants
    assert rising[1] > 0.6 * target  # within 40 ms most of the way up
    assert rising[-1] == pytest.approx(target, rel=0.02)

    falling, _ = run_envelope(envelope, 0.0, 1.0, t)
    assert falling[1] > 0.85 * target  # 40 ms later still nearly all there
    assert falling[-1] < 0.1 * target  # but gone within a second
    assert all(a >= b for a, b in zip(falling, falling[1:]))


def test_the_envelope_follows_timestamps_not_call_count():
    coarse, fine = LevelEnvelope(), LevelEnvelope()
    run_envelope(coarse, 0.2, 0.2, 0.0, step=0.1)
    run_envelope(fine, 0.2, 0.2, 0.0, step=0.01)

    assert coarse.level == pytest.approx(fine.level, rel=0.05)


def test_dynamics_stay_visible():
    envelope = LevelEnvelope()
    loud, t = run_envelope(envelope, 0.3, 0.2, 0.0)
    quiet, _ = run_envelope(envelope, 0.02, 0.4, t)

    assert loud[-1] - quiet[-1] > 0.25


# --- Waveform -----------------------------------------------------------------------------


def test_no_samples_no_waveform():
    reducer = WaveformReducer()

    assert reducer.waveform() is None
    reducer.add(frame([1000] * 10))  # less than one bucket
    assert reducer.waveform() is None


def test_buckets_hold_real_peaks_on_the_display_scale():
    reducer = WaveformReducer(bucket_s=0.025, size=40)
    per_bucket = int(RATE * 0.025)
    reducer.add(frame([0] * per_bucket + [3277] * per_bucket + [-32767] * per_bucket, t=1.0))

    waveform = reducer.waveform()
    assert waveform.buckets == pytest.approx((0.0, to_display(3277 / 32768), to_display(32767 / 32768)))
    assert waveform.timestamp_s == 1.0


def test_the_history_is_bounded_whatever_the_capture_time():
    reducer = WaveformReducer(bucket_s=0.025, size=40)
    for second in range(30):
        reducer.add(frame(sine(20000, count=RATE), t=float(second)))

    assert len(reducer.waveform().buckets) == 40
    assert all(0.0 <= value <= 1.0 for value in reducer.waveform().buckets)


def test_varying_input_varies_the_waveform():
    reducer = WaveformReducer()
    loudness = [200, 2000, 20000, 2000, 200]
    reducer.add(frame([a for amplitude in loudness for a in sine(amplitude, count=400)]))

    buckets = reducer.waveform().buckets
    assert max(buckets) - min(buckets) > 0.4
    assert buckets.index(max(buckets)) == 2


# --- The service -----------------------------------------------------------------------------


class FakeBackend:
    name = "fake"

    def __init__(self, *, opens=True):
        self.opens = opens
        self.starts = 0
        self.stops = 0
        self.on_frame = self.on_failure = None

    @property
    def info(self):
        return AudioInputInfo("fake", "Fake Mic", RATE if self.on_frame else None, 1)

    def start(self, on_frame, on_failure):
        self.starts += 1
        if not self.opens:
            return False
        self.on_frame, self.on_failure = on_frame, on_failure
        return True

    def stop(self):
        self.stops += 1
        self.on_frame = self.on_failure = None

    @property
    def running(self):
        return self.on_frame is not None

    def deliver(self, values, t):
        self.on_frame(frame(values, t))


@pytest.fixture
def service():
    bus = EventBus()
    backend = FakeBackend()
    audio = AudioInputService(bus, backend)
    statuses = []
    bus.subscribe(AUDIO_STATUS_EVENT, statuses.append)
    audio.backend, audio.statuses = backend, statuses
    return audio


def test_the_service_is_idle_until_someone_needs_the_microphone(service):
    assert service.status is CaptureStatus.IDLE and service.backend.starts == 0
    assert service.waveform is None and service.level == 0.0


def test_a_lease_starts_capture_and_frames_become_telemetry(service):
    lease = service.acquire(CapturePurpose.CONVERSATION)

    assert lease is not None and service.capturing and service.backend.starts == 1
    service.backend.deliver(sine(16000, count=1600), t=0.1)
    assert service.raw_level.peak == pytest.approx(16000 / 32768, rel=1e-3)
    assert service.level > 0 and service.waveform is not None
    assert [status.status for status in service.statuses] == [CaptureStatus.CAPTURING]


def test_the_last_release_stops_capture_and_clears_telemetry(service):
    first = service.acquire(CapturePurpose.CONVERSATION)
    second = service.acquire(CapturePurpose.CONVERSATION)
    service.backend.deliver(sine(16000), t=0.1)

    first.release()
    assert service.capturing  # someone still needs it
    second.release()
    second.release()

    assert service.status is CaptureStatus.IDLE and service.backend.stops == 1
    assert service.waveform is None and service.raw_level is None and service.level == 0.0
    assert service.backend.starts == 1  # one owner, one capture


def test_repeated_start_stop_is_clean(service):
    for cycle in range(5):
        lease = service.acquire(CapturePurpose.CONVERSATION)
        service.backend.deliver([1000] * 800, t=float(cycle))
        lease.release()

    assert (service.backend.starts, service.backend.stops) == (5, 5)
    assert service.status is CaptureStatus.IDLE and service.lease_purposes == []


def test_a_microphone_that_cannot_open_gives_no_lease(service):
    service.backend.opens = False

    assert service.acquire(CapturePurpose.CONVERSATION) is None
    assert service.status is CaptureStatus.FAILED and service.snapshot.reason
    assert service.waveform is None


def test_no_backend_means_unavailable_never_fake_data():
    audio = AudioInputService(EventBus(), None)

    assert audio.status is CaptureStatus.UNAVAILABLE
    assert audio.acquire(CapturePurpose.CONVERSATION) is None
    assert audio.waveform is None and audio.level == 0.0


def test_a_lost_device_voids_every_lease_and_reports_once(service):
    lease = service.acquire(CapturePurpose.CONVERSATION)
    service.backend.deliver(sine(16000), t=0.1)
    failure = service.backend.on_failure

    failure("the microphone was disconnected")
    failure("again")

    assert service.status is CaptureStatus.FAILED and lease.released
    assert service.waveform is None
    assert [s.status for s in service.statuses] == [CaptureStatus.CAPTURING, CaptureStatus.FAILED]
    assert service.statuses[-1].reason == "the microphone was disconnected"
    # It can be tried again later.
    assert service.acquire(CapturePurpose.CONVERSATION) is not None


def test_frames_after_stopping_are_ignored(service):
    lease = service.acquire(CapturePurpose.CONVERSATION)
    deliver = service.backend.on_frame
    lease.release()

    deliver(frame(sine(30000), t=1.0))

    assert service.waveform is None and service.frames_received == 0


def test_shutdown_releases_the_microphone_for_good(service):
    service.acquire(CapturePurpose.CONVERSATION)

    service.shutdown()
    service.shutdown()

    assert not service.backend.running and service.lease_purposes == []
    assert service.acquire(CapturePurpose.CONVERSATION) is None


def test_the_application_owns_exactly_one_idle_audio_service():
    from mira.application.composition import build_application

    backend = FakeBackend()
    application = build_application(scheduler=ManualScheduler(), audio_backend=backend)
    application.start()
    try:
        assert isinstance(application.audio, AudioInputService)
        assert backend.starts == 0  # nothing captures until asked
    finally:
        application.shutdown()
    assert backend.stops >= 1


# --- Format conversion (the Qt backend's pure part) ------------------------------------------


def test_stereo_and_wider_formats_become_mono_s16():
    pytest.importorskip("PySide6")
    from mira.adapters.qt_audio import to_mono_s16

    stereo = array("h", [1000, 3000, -2000, -4000]).tobytes()
    assert array("h", to_mono_s16(stereo, 2, "int16")).tolist() == [2000, -3000]

    int32 = array("i", [65536 * 1000, 65536 * -1000]).tobytes()
    assert array("h", to_mono_s16(int32, 1, "int32")).tolist() == [1000, -1000]

    floats = struct.pack("<ff", 0.5, -1.0)
    assert array("h", to_mono_s16(floats, 1, "float")).tolist() == [round(0.5 * 32767), -32767]

    with pytest.raises(ValueError):
        to_mono_s16(b"", 1, "uint8")


class FakeQtSource:
    def __init__(self, error="NoError"):
        self._error = error

    def error(self):
        return type("E", (), {"name": self._error})()

    def stop(self):
        pass

    def deleteLater(self):
        pass


@pytest.mark.parametrize(("error", "idle_s", "lost"), [
    ("NoError", 1.0, False),   # quiet (or muted) but delivering: fine
    ("NoError", 4.0, True),    # no data at all: stalled
    ("IOError", 0.0, True),    # the source stopped with an error
])
def test_the_watchdog_tells_a_stalled_or_failed_source_from_a_quiet_one(error, idle_s, lost):
    pytest.importorskip("PySide6")
    from mira.adapters.qt_audio import QtAudioCaptureBackend

    now = [100.0]
    backend = QtAudioCaptureBackend(clock=lambda: now[0])
    failures = []
    backend._source, backend._on_failure, backend._last_data_s = FakeQtSource(error), failures.append, now[0]
    now[0] += idle_s

    backend.check()

    assert bool(failures) is lost
    assert (backend._source is None) is lost
