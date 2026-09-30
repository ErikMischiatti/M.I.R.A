"""The OpenCV camera adapter, driven through a fake `cv2.VideoCapture`.

No test here opens a real device or needs OpenCV installed.
"""

from __future__ import annotations

import sys

import pytest

from mira.adapters.opencv_camera import OpenCVCameraSource, open_video_capture
from mira.domain.camera import CameraError, CameraSettings, PixelFormat


WIDTH, HEIGHT, FPS = 3, 4, 5


def image(height: int = 2, width: int = 4, fill: int = 7) -> memoryview:
    """An image with OpenCV's layout: rows × columns × BGR bytes."""
    return memoryview(bytearray([fill]) * (height * width * 3)).cast("B", (height, width, 3))


class FakeCapture:
    def __init__(self, device, *, opened=True, reads=(), properties=None, read_error=None):
        self.device = device
        self.opened = opened
        self.reads = list(reads)
        self.read_error = read_error
        self.properties = {WIDTH: 4.0, HEIGHT: 2.0, FPS: 30.0}
        self.properties.update(properties or {})
        self.requested: dict[int, float] = {}
        self.releases = 0

    def isOpened(self):  # noqa: N802 - OpenCV's API
        return self.opened

    def set(self, prop, value):
        self.requested[prop] = value
        return True

    def get(self, prop):
        return self.properties.get(prop, 0.0)

    def read(self):
        if self.read_error is not None:
            raise self.read_error
        if not self.reads:
            return False, None
        return self.reads.pop(0)

    def release(self):
        self.releases += 1


class Factory:
    def __init__(self, **capture_options):
        self.options = capture_options
        self.captures: list[FakeCapture] = []

    def __call__(self, device):
        capture = FakeCapture(device, **self.options)
        self.captures.append(capture)
        return capture


def source_with(settings=CameraSettings(), clock=None, **capture_options):
    factory = Factory(**capture_options)
    ticks = iter(range(100, 200))
    source = OpenCVCameraSource(
        settings,
        capture_factory=factory,
        clock=clock or (lambda: float(next(ticks))),
    )
    return source, factory


# --- opening -----------------------------------------------------------------


def test_opening_reports_what_the_device_negotiated():
    source, factory = source_with(properties={WIDTH: 640.0, HEIGHT: 480.0, FPS: 30.0})

    info = source.open()

    assert (info.device, info.width, info.height, info.fps) == ("0", 640, 480, 30.0)
    assert factory.captures[0].device == 0
    assert source.is_open


def test_a_numeric_device_is_an_index_and_anything_else_a_path():
    source, factory = source_with(CameraSettings(device="/dev/video2"))
    source.open()

    assert factory.captures[0].device == "/dev/video2"


def test_requested_properties_are_applied_before_reading_back():
    source, factory = source_with(
        CameraSettings(width=1280, height=720, fps=15.0),
        properties={WIDTH: 640.0, HEIGHT: 480.0, FPS: 0.0},
    )

    info = source.open()

    assert factory.captures[0].requested == {WIDTH: 1280.0, HEIGHT: 720.0, FPS: 15.0}
    assert (info.width, info.height, info.fps) == (640, 480, None)


def test_unset_properties_leave_device_defaults_alone():
    source, factory = source_with()
    source.open()

    assert factory.captures[0].requested == {}


def test_opening_twice_reuses_the_device():
    source, factory = source_with()

    assert source.open() == source.open()
    assert len(factory.captures) == 1


def test_a_device_that_does_not_open_is_released_and_reported():
    source, factory = source_with(opened=False)

    with pytest.raises(CameraError, match="could not be opened"):
        source.open()

    assert factory.captures[0].releases == 1
    assert not source.is_open
    assert source.read() is None


def test_a_device_reporting_no_size_is_released_and_reported():
    source, factory = source_with(properties={WIDTH: 0.0})

    with pytest.raises(CameraError, match="unusable properties"):
        source.open()

    assert factory.captures[0].releases == 1
    assert not source.is_open


def test_a_failed_open_can_be_retried():
    source, factory = source_with(opened=False)
    with pytest.raises(CameraError):
        source.open()

    factory.options["opened"] = True

    assert source.open().width == 4


def test_missing_opencv_is_reported_as_an_unavailable_camera(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)

    with pytest.raises(CameraError, match="OpenCV is not installed"):
        open_video_capture(0)
    with pytest.raises(CameraError, match="OpenCV is not installed"):
        OpenCVCameraSource().open()


@pytest.mark.parametrize(
    "overrides",
    [{"device": ""}, {"width": 0}, {"height": -1}, {"fps": 0.0}, {"fps": float("nan")}],
)
def test_invalid_camera_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        CameraSettings(**overrides)


# --- reading -----------------------------------------------------------------


def test_reads_become_sequenced_timestamped_frames_without_copying():
    first, second = image(fill=1), image(fill=2)
    source, _ = source_with(reads=[(True, first), (True, second)])
    source.open()

    frames = [source.read(), source.read()]

    assert [(f.sequence, f.timestamp_s) for f in frames] == [(0, 100.0), (1, 101.0)]
    assert all((f.width, f.height, f.pixel_format) == (4, 2, PixelFormat.BGR8) for f in frames)
    assert frames[0].pixels.obj is first.obj
    assert frames[1].pixels[0] == 2


def test_a_failed_read_returns_none_and_does_not_advance_the_sequence():
    source, _ = source_with(reads=[(False, None), (True, None), (True, image())])
    source.open()

    assert source.read() is None
    assert source.read() is None
    assert source.read().sequence == 0


def test_a_backend_error_during_read_is_a_failed_read():
    source, _ = source_with(read_error=RuntimeError("cv2.error"))
    source.open()

    assert source.read() is None


@pytest.mark.parametrize(
    "unsupported",
    [
        memoryview(bytearray(8)).cast("B", (2, 4)),  # grayscale
        memoryview(bytearray(32)).cast("B", (2, 4, 4)),  # BGRA
        memoryview(bytearray(48)).cast("H", (2, 4, 3)),  # 16-bit
        "not an image",
    ],
)
def test_images_outside_the_boundary_format_are_failed_reads(unsupported):
    source, _ = source_with(reads=[(True, unsupported)])
    source.open()

    assert source.read() is None


def test_reading_before_open_returns_none():
    source, factory = source_with()

    assert source.read() is None
    assert factory.captures == []


# --- releasing ---------------------------------------------------------------


def test_close_releases_once_and_is_idempotent():
    source, factory = source_with()
    source.open()

    source.close()
    source.close()

    assert factory.captures[0].releases == 1
    assert not source.is_open
    assert source.read() is None


def test_close_before_open_is_harmless():
    source, factory = source_with()

    source.close()

    assert factory.captures == []


def test_a_release_error_does_not_escape_close():
    source, factory = source_with()
    source.open()

    def broken_release():
        raise RuntimeError("driver")

    factory.captures[0].release = broken_release
    source.close()

    assert not source.is_open


def test_reopening_after_close_restarts_the_sequence():
    source, factory = source_with(reads=[(True, image())])
    source.open()
    assert source.read().sequence == 0
    source.close()

    source.open()
    factory.captures[-1].reads = [(True, image())]

    assert source.read().sequence == 0


def test_a_numpy_image_is_wrapped_without_a_copy():
    numpy = pytest.importorskip("numpy")
    array = numpy.zeros((2, 4, 3), dtype=numpy.uint8)
    array[1, 3] = (9, 8, 7)
    source, _ = source_with(reads=[(True, array)])
    source.open()

    frame = source.read()

    assert frame.pixels.obj is array
    assert bytes(frame.pixels[-3:]) == bytes([9, 8, 7])
