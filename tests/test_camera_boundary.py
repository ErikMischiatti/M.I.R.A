"""The technology-independent camera boundary."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mira.domain.camera as camera_module
from mira.domain.camera import (
    CameraError,
    CameraFrame,
    CameraInfo,
    CameraSource,
    PixelFormat,
)

from camera_doubles import FakeCameraSource, make_frame


def frame(**overrides) -> CameraFrame:
    values = dict(
        sequence=3,
        timestamp_s=12.5,
        width=4,
        height=2,
        pixel_format=PixelFormat.BGR8,
        pixels=bytes(range(24)),
    )
    values.update(overrides)
    return CameraFrame(**values)


# --- CameraFrame -------------------------------------------------------------


def test_frame_carries_metadata_and_a_flat_read_only_pixel_view():
    captured = frame()

    assert (captured.sequence, captured.timestamp_s) == (3, 12.5)
    assert (captured.width, captured.height, captured.row_stride) == (4, 2, 12)
    assert captured.pixel_format.channels == 3
    assert captured.pixels.readonly
    assert (captured.pixels.ndim, captured.pixels.format, captured.pixels.nbytes) == (1, "B", 24)
    assert captured.pixels[5] == 5


def test_frame_pixels_cannot_be_written_through_the_frame():
    buffer = bytearray(24)
    captured = frame(pixels=buffer)

    with pytest.raises(TypeError):
        captured.pixels[0] = 255


def test_frame_views_a_writable_buffer_without_copying_it():
    buffer = bytearray(24)
    captured = frame(pixels=memoryview(buffer))

    buffer[0] = 42

    assert captured.pixels[0] == 42


def test_frame_accepts_a_contiguous_multi_dimensional_buffer():
    shaped = memoryview(bytearray(24)).cast("B", (2, 4, 3))

    captured = frame(pixels=shaped)

    assert (captured.pixels.ndim, captured.pixels.nbytes) == (1, 24)


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"width": 0}, "dimensions must be positive"),
        ({"height": -2}, "dimensions must be positive"),
        ({"sequence": -1}, "sequence"),
        ({"timestamp_s": float("nan")}, "timestamp"),
        ({"pixels": bytes(23)}, "needs 24"),
        ({"pixels": bytes(25)}, "needs 24"),
    ],
)
def test_frame_rejects_inconsistent_metadata(overrides, message):
    with pytest.raises(ValueError, match=message):
        frame(**overrides)


def test_frame_rejects_a_non_contiguous_buffer():
    strided = memoryview(bytearray(48))[::2]

    with pytest.raises(ValueError, match="contiguous"):
        frame(pixels=strided)


def test_frame_is_immutable_and_compares_by_identity():
    first, second = frame(), frame()

    assert first != second
    assert first == first
    with pytest.raises((FrozenInstanceError, AttributeError)):
        first.width = 8  # type: ignore[misc]


# --- CameraInfo --------------------------------------------------------------


def test_camera_info_is_a_value():
    assert CameraInfo("cam", 640, 480, 30.0) == CameraInfo("cam", 640, 480, 30.0)
    assert CameraInfo("cam", 640, 480).fps is None


@pytest.mark.parametrize(
    "width,height,fps",
    [(0, 480, None), (640, -1, None), (640, 480, 0.0), (640, 480, float("inf"))],
)
def test_camera_info_rejects_impossible_capabilities(width, height, fps):
    with pytest.raises(ValueError):
        CameraInfo("cam", width, height, fps)


# --- CameraSource contract ----------------------------------------------------


def exercise(source: CameraSource) -> list:
    info = source.open()
    frames = [source.read(), source.read(), source.read()]
    source.close()
    source.close()
    return [info, *frames]


def test_a_source_opens_reads_and_closes_idempotently():
    frames = [make_frame(0), None, make_frame(1)]
    source = FakeCameraSource(frames)

    info, *read = exercise(source)

    assert info == source.info
    assert read == frames
    assert source.calls == ["open", "read", "read", "read", "close", "close"]
    assert not source.is_open


def test_a_source_reports_an_unopenable_device_as_camera_error():
    source = FakeCameraSource(open_error=CameraError("no device"))

    with pytest.raises(CameraError, match="no device"):
        source.open()


def test_camera_boundary_imports_no_capture_library_or_toolkit():
    tree = ast.parse(Path(camera_module.__file__).read_text(encoding="utf-8"))
    names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}

    assert not any(name.split(".")[0] in {"cv2", "numpy", "PySide6"} for name in names)
    assert not any(name.startswith("mira.") for name in names)
