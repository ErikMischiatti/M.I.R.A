"""The OpenCV cascade person detector, driven through a fake backend.

Only the last two tests touch real OpenCV, and they skip when it is absent.
None needs a camera, a download or a GPU.
"""

from __future__ import annotations

import sys

import pytest

from mira.adapters.opencv_person_detector import (
    DEFAULT_MIN_CONFIDENCE,
    OpenCVCascadeBackend,
    OpenCVCascadePersonDetector,
)
from mira.domain.perception import BoundingBox, PerceptionError

from camera_doubles import make_frame


FRAME = make_frame(5, width=200, height=100, timestamp_s=42.0)


class FakeBackend:
    def __init__(self, *, results=((), ()), load_error=None, run_error=None):
        self.results = results
        self.load_error = load_error
        self.run_error = run_error
        self.loaded_paths: list = []
        self.min_sizes: list[int] = []

    def load(self, model_path):
        self.loaded_paths.append(model_path)
        if self.load_error is not None:
            raise self.load_error
        return "classifier"

    def prepare(self, frame):
        return ("gray", frame.sequence)

    def run(self, classifier, image, min_size):
        assert classifier == "classifier"
        self.min_sizes.append(min_size)
        if self.run_error is not None:
            raise self.run_error
        return self.results


def detector_with(rectangles=(), neighbours=(), **options):
    backend = FakeBackend(results=(rectangles, neighbours))
    detector = OpenCVCascadePersonDetector(backend=backend, **options)
    detector.load()
    return detector, backend


def test_no_person_yields_no_observations():
    detector, _ = detector_with()

    assert detector.detect(FRAME) == ()


def test_one_person_is_normalized_into_image_space():
    detector, _ = detector_with([(50, 20, 40, 60)], [12])

    (seen,) = detector.detect(FRAME)

    assert seen.bounds == BoundingBox(0.25, 0.2, 0.45, 0.8)
    assert (seen.center.x, seen.center.y) == pytest.approx((0.35, 0.5))
    assert seen.confidence == 1.0
    assert (seen.frame_sequence, seen.observed_at) == (5, 42.0)


def test_multiple_people_are_ordered_by_confidence_then_position():
    detector, _ = detector_with(
        [(150, 10, 20, 20), (10, 10, 20, 20), (80, 10, 20, 20)],
        [6, 9, 6],
    )

    found = detector.detect(FRAME)

    assert [round(seen.bounds.left, 3) for seen in found] == [0.05, 0.4, 0.75]
    assert [seen.confidence for seen in found] == pytest.approx([0.75, 0.5, 0.5])


def test_weak_detections_are_filtered_by_the_threshold():
    detector, _ = detector_with([(10, 10, 20, 20), (60, 10, 20, 20)], [4, 5], min_confidence=0.4)

    found = detector.detect(FRAME)

    assert [seen.confidence for seen in found] == pytest.approx([5 / 12])


def test_a_zero_threshold_keeps_every_detection():
    detector, _ = detector_with([(10, 10, 20, 20)], [1], min_confidence=0.0)

    assert len(detector.detect(FRAME)) == 1


def test_rectangles_reaching_past_the_frame_are_clipped():
    detector, _ = detector_with([(-10, 90, 30, 40)], [12])

    (seen,) = detector.detect(FRAME)

    assert seen.bounds == BoundingBox(0.0, 0.9, 0.1, 1.0)


@pytest.mark.parametrize(
    "rectangle,agreeing",
    [
        ((10, 10, 0, 20), 12),  # zero width
        ((10, 10, 20, -5), 12),  # negative height
        ((10, 10, 20), 12),  # too few values
        (("x", 10, 20, 20), 12),  # not a number
        ((float("nan"), 10, 20, 20), 12),
        ((250, 10, 20, 20), 12),  # wholly outside the frame
        ((10, 10, 20, 20), "many"),
    ],
)
def test_malformed_detector_results_are_dropped(rectangle, agreeing):
    detector, _ = detector_with([rectangle, (60, 10, 20, 20)], [agreeing, 12])

    found = detector.detect(FRAME)

    assert [seen.bounds.left for seen in found] == [0.3]


def test_minimum_size_scales_with_the_frame():
    detector, backend = detector_with()

    detector.detect(FRAME)
    detector.detect(make_frame(0, width=640, height=480))

    assert backend.min_sizes == [8, 38]


def test_the_default_model_is_the_bundled_cascade_unless_a_path_is_given():
    default = FakeBackend()
    OpenCVCascadePersonDetector(backend=default).load()
    custom = FakeBackend()
    OpenCVCascadePersonDetector("/models/upper.xml", backend=custom).load()

    assert default.loaded_paths == [None]
    assert custom.loaded_paths == ["/models/upper.xml"]


def test_loading_is_done_once():
    detector, backend = detector_with()

    detector.load()

    assert backend.loaded_paths == [None]


def test_an_unavailable_model_is_a_perception_error():
    detector = OpenCVCascadePersonDetector(
        backend=FakeBackend(load_error=PerceptionError("model not found"))
    )

    with pytest.raises(PerceptionError, match="model not found"):
        detector.load()
    with pytest.raises(PerceptionError, match="before load"):
        detector.detect(FRAME)


def test_an_inference_failure_propagates_to_the_caller():
    backend = FakeBackend(run_error=RuntimeError("cv2.error"))
    detector = OpenCVCascadePersonDetector(backend=backend)
    detector.load()

    with pytest.raises(RuntimeError, match="cv2.error"):
        detector.detect(FRAME)


@pytest.mark.parametrize("threshold", [-0.1, 1.5, float("nan")])
def test_an_invalid_threshold_is_rejected(threshold):
    with pytest.raises(ValueError):
        OpenCVCascadePersonDetector(min_confidence=threshold, backend=FakeBackend())


def test_the_default_threshold_is_a_valid_confidence():
    assert 0.0 < DEFAULT_MIN_CONFIDENCE < 1.0


def test_missing_opencv_is_reported_as_an_unavailable_detector(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)

    with pytest.raises(PerceptionError, match="OpenCV is not installed"):
        OpenCVCascadeBackend().load(None)


def test_a_missing_model_file_is_reported(tmp_path):
    pytest.importorskip("cv2", exc_type=ImportError)

    with pytest.raises(PerceptionError, match="not found"):
        OpenCVCascadeBackend().load(str(tmp_path / "absent.xml"))


def test_a_corrupt_model_file_is_reported(tmp_path):
    pytest.importorskip("cv2", exc_type=ImportError)
    corrupt = tmp_path / "corrupt.xml"
    corrupt.write_text("<not a cascade/>", encoding="utf-8")

    with pytest.raises(PerceptionError, match="could not be loaded"):
        OpenCVCascadeBackend().load(str(corrupt))


def test_the_bundled_cascade_runs_on_a_real_frame_and_finds_nobody_in_it():
    pytest.importorskip("cv2", exc_type=ImportError)
    detector = OpenCVCascadePersonDetector()
    detector.load()

    assert detector.detect(make_frame(1, width=320, height=240)) == ()
