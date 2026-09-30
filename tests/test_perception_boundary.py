"""The technology-independent person perception boundary."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mira.domain.perception as perception_module
from mira.domain.perception import (
    BoundingBox,
    ImagePoint,
    PerceptionError,
    PersonDetector,
    PersonObservation,
)

from camera_doubles import make_frame
from perception_doubles import FakePersonDetector


def observation(**overrides) -> PersonObservation:
    values = dict(
        confidence=0.8,
        bounds=BoundingBox(0.2, 0.1, 0.6, 0.9),
        frame_sequence=7,
        observed_at=12.5,
    )
    values.update(overrides)
    return PersonObservation(**values)


# --- image-space geometry -------------------------------------------------------


def test_bounding_box_exposes_its_size_area_and_centre():
    box = BoundingBox(0.2, 0.1, 0.6, 0.9)

    assert (box.width, box.height) == pytest.approx((0.4, 0.8))
    assert box.area == pytest.approx(0.32)
    assert box.center == ImagePoint(pytest.approx(0.4), pytest.approx(0.5))


def test_the_whole_frame_is_a_valid_box():
    box = BoundingBox(0.0, 0.0, 1.0, 1.0)
    assert box.center == ImagePoint(0.5, 0.5)


@pytest.mark.parametrize(
    "coordinates",
    [
        (-0.1, 0.0, 0.5, 0.5),
        (0.0, 0.0, 1.2, 0.5),
        (0.0, float("nan"), 0.5, 0.5),
        (0.0, 0.0, 0.5, float("inf")),
    ],
)
def test_boxes_outside_image_space_are_rejected(coordinates):
    with pytest.raises(ValueError, match="within \\[0, 1\\]"):
        BoundingBox(*coordinates)


@pytest.mark.parametrize(
    "coordinates",
    [(0.5, 0.1, 0.5, 0.9), (0.6, 0.1, 0.2, 0.9), (0.2, 0.9, 0.6, 0.1)],
    ids=["zero-width", "inverted-x", "inverted-y"],
)
def test_boxes_without_positive_area_are_rejected(coordinates):
    with pytest.raises(ValueError, match="left < right"):
        BoundingBox(*coordinates)


@pytest.mark.parametrize("point", [(-0.01, 0.5), (0.5, 1.01), (float("nan"), 0.5)])
def test_image_points_must_lie_in_image_space(point):
    with pytest.raises(ValueError):
        ImagePoint(*point)


# --- PersonObservation ----------------------------------------------------------


def test_observation_locates_a_person_in_a_specific_frame():
    seen = observation()

    assert seen.center == seen.bounds.center
    assert (seen.frame_sequence, seen.observed_at) == (7, 12.5)


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_confidence_bounds_are_inclusive(confidence):
    assert observation(confidence=confidence).confidence == confidence


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"confidence": 1.01}, "confidence"),
        ({"confidence": -0.1}, "confidence"),
        ({"confidence": float("nan")}, "confidence"),
        ({"frame_sequence": -1}, "sequence"),
        ({"observed_at": float("inf")}, "time"),
    ],
)
def test_invalid_observations_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        observation(**overrides)


def test_observations_are_immutable_values_without_identity():
    first, second = observation(), observation()

    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        first.confidence = 0.1  # type: ignore[misc]
    assert set(PersonObservation.__dataclass_fields__) == {
        "confidence",
        "bounds",
        "frame_sequence",
        "observed_at",
    }


# --- PersonDetector contract ------------------------------------------------------


def run(detector: PersonDetector, frames):
    detector.load()
    return [detector.detect(frame) for frame in frames]


def test_a_detector_reports_zero_one_or_many_people_per_frame():
    frames = [make_frame(0), make_frame(1), make_frame(2)]
    detector = FakePersonDetector(
        script={0: [], 1: [(0.1, 0.1, 0.3, 0.5)], 2: [(0.1, 0.1, 0.3, 0.5), (0.6, 0.2, 0.9, 0.8)]}
    )

    results = run(detector, frames)

    assert [len(found) for found in results] == [0, 1, 2]
    assert all(seen.frame_sequence == 2 for seen in results[2])
    assert detector.seen == [0, 1, 2]


def test_a_detector_that_cannot_run_raises_perception_error():
    detector = FakePersonDetector(load_error=PerceptionError("model missing"))

    with pytest.raises(PerceptionError, match="model missing"):
        detector.load()


def test_perception_boundary_imports_no_detector_library_or_toolkit():
    tree = ast.parse(Path(perception_module.__file__).read_text(encoding="utf-8"))
    names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}

    assert not any(name.split(".")[0] in {"cv2", "numpy", "mediapipe", "PySide6"} for name in names)
    assert {name for name in names if name.startswith("mira.")} <= {"mira.domain.camera"}
