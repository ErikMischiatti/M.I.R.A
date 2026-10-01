"""The eyes' whole motion envelope stays inside the notch for valid configurations.

Three layers are checked: the domain envelope bounds every frame the real
controller produces; the visual-config rule accepts exactly the
configurations whose envelope fits the notch's safe region; and the painter,
given extreme frames, draws inside that region.
"""

from __future__ import annotations

import ast
import itertools
import pathlib
import random

import pytest

from mira.domain.attention import AttentionTarget
from mira.domain.embodiment import ActivityState, EmbodimentIntent, ExpressionKey
from mira.domain.embodiment_frame import (
    FACE_HEIGHT_UNITS,
    FACE_WIDTH_UNITS,
    EmbodimentFrame,
    ExpressionDefinition,
    EyeFrame,
)
from mira.domain.gaze import gaze_reach
from mira.domain.micro_motion import MicroMotions, MotionSettings
from mira.domain.motion_envelope import embodiment_envelope, motion_offset_bounds
from mira.ui.face.expression_store import load_expression_library
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.eye_layout import (
    BASE_EYE_HEIGHT_SHARE,
    MOTION_GAIN,
    NotchEyeStyle,
    library_envelope,
    production_envelope,
)
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchShape, NotchSize, Rect
from mira.ui.notch.visual_config import (
    SAFE_MARGIN_PX,
    InvalidVisualConfig,
    NotchVisualConfig,
    eye_layout_for,
    nominal_window,
    overflow,
    safe_region,
    validate_visual_config,
    visual_config_problems,
)


ENVELOPE = production_envelope()


def config(**eyes) -> NotchVisualConfig:
    return NotchVisualConfig(eye_style=NotchEyeStyle(**eyes))


def with_geometry(**geometry) -> NotchVisualConfig:
    return NotchVisualConfig(geometry=NotchGeometry(**geometry))


# --- The domain envelope -----------------------------------------------------------


def test_an_expression_without_motion_is_bounded_by_its_pose_and_gaze():
    definition = ExpressionDefinition("still", offset_x=10.0, offset_y=-4.0)
    envelope = embodiment_envelope([(definition, MotionSettings(gaze_weight=0.0))])

    # Bounded by its own pose and the neutral initial pose it blends from.
    assert (envelope.offset_x_min, envelope.offset_x_max) == (0.0, 10.0)
    assert (envelope.offset_y_min, envelope.offset_y_max) == (-4.0, 0.0)
    assert envelope.width_scale_max == 1.0


def test_idle_wander_drift_and_gaze_each_widen_the_offset_range():
    assert motion_offset_bounds(MotionSettings(gaze_weight=0.0)) == ((0.0, 0.0), (0.0, 0.0))
    idle = MotionSettings(idle_gaze=True, idle_amplitude_x=30.0, idle_amplitude_y=20.0, gaze_weight=0.0)
    assert motion_offset_bounds(idle) == ((-30.0, 30.0), (-20.0, 20.0))
    drift = MotionSettings(thinking_drift=True, gaze_weight=0.0)
    assert motion_offset_bounds(drift) == ((-16.0, 0.0), (0.0, 0.0))
    reach_x, reach_y = gaze_reach(1.0)
    assert motion_offset_bounds(MotionSettings()) == ((-reach_x, reach_x), (-reach_y, reach_y))


def test_the_speaking_pulse_and_upward_gaze_raise_the_height_bound():
    definition = ExpressionDefinition("speaking")
    calm = embodiment_envelope([(definition, MotionSettings(gaze_weight=0.0))])
    pulsing = embodiment_envelope([(definition, MotionSettings(gaze_weight=0.0, speaking_pulse=True))])
    gazing = embodiment_envelope([(definition, MotionSettings(gaze_weight=1.0))])

    assert pulsing.height_scale_max == pytest.approx(calm.height_scale_max + 0.16)
    assert gazing.height_scale_max == pytest.approx(calm.height_scale_max + 0.04)


def test_any_asymmetry_can_meet_any_blended_pose():
    tall = ExpressionDefinition("tall", height_scale=2.0)
    lopsided = ExpressionDefinition("lopsided", asymmetry_offset_y_left=-6.0, asymmetry_height_left=1.05)
    still = MotionSettings(gaze_weight=0.0)

    envelope = embodiment_envelope([(tall, still), (lopsided, still)])

    # Switching from tall to lopsided applies the asymmetry to the tall pose.
    assert envelope.height_scale_max == pytest.approx(2.0 * 1.05)
    assert envelope.offset_y_min == pytest.approx(-6.0)


def test_an_empty_library_has_no_envelope():
    with pytest.raises(ValueError):
        embodiment_envelope([])


def eye_extent(frame_eye: EyeFrame) -> tuple[float, float, float, float]:
    """An eye's displacement and size in PlaybackPose units, as painted."""
    return (
        frame_eye.offset_x * FACE_WIDTH_UNITS,
        frame_eye.offset_y * FACE_HEIGHT_UNITS,
        frame_eye.width_scale,
        frame_eye.height_scale,
    )


def test_every_frame_of_the_real_controller_lies_inside_the_envelope():
    rng = random.Random(7)
    controller = FaceController(MicroMotions(random.Random(11)))
    envelope = library_envelope(controller.expression_library)
    keys = list(ExpressionKey)
    extremes = [AttentionTarget(x, y) for x, y in itertools.product((-1.0, 0.0, 1.0), repeat=2)]
    observed = []

    def run(steps: int):
        for step in range(steps):
            if step % 150 == 0:
                if rng.random() < 0.7:
                    controller.set_attention_target(rng.choice(extremes))
                else:
                    controller.clear_attention_target()
            controller.update(rng.choice((0.016, 0.030, 0.050)))
            frame = controller.get_frame()
            observed.extend(eye_extent(eye) for eye in (frame.left_eye, frame.right_eye))

    for key in keys:
        controller.set_intent(EmbodimentIntent(ActivityState.IDLE, expression=key))
        run(1500)
    for start, end in itertools.permutations(keys, 2):
        controller.set_intent(EmbodimentIntent(ActivityState.IDLE, expression=start))
        run(120)
        controller.set_intent(EmbodimentIntent(ActivityState.IDLE, expression=end))
        run(60)

    epsilon = 1e-9
    for offset_x, offset_y, width_scale, height_scale in observed:
        assert envelope.offset_x_min - epsilon <= offset_x <= envelope.offset_x_max + epsilon
        assert envelope.offset_y_min - epsilon <= offset_y <= envelope.offset_y_max + epsilon
        assert width_scale <= envelope.width_scale_max + epsilon
        assert height_scale <= envelope.height_scale_max + epsilon
    # The simulation really explores the range, so the test has teeth.
    assert min(o[1] for o in observed) < 0.75 * envelope.offset_y_min
    assert max(o[3] for o in observed) > 0.9 * envelope.height_scale_max


def test_the_production_envelope_is_the_packaged_libraries():
    assert ENVELOPE == library_envelope(load_expression_library())


# --- The safe-region geometry -------------------------------------------------------


def test_the_safe_region_is_the_outline_inset_by_the_margin():
    region = safe_region(NotchShape(Rect(0, 0, 200, 32), 14.0))

    assert region.bounds == Rect(SAFE_MARGIN_PX, SAFE_MARGIN_PX, 200 - 2 * SAFE_MARGIN_PX, 32 - 2 * SAFE_MARGIN_PX)
    assert region.corner_radius == 14.0 - SAFE_MARGIN_PX


def test_a_rectangle_in_a_rounded_corner_overflows_only_past_the_arc():
    region = NotchShape(Rect(0, 0, 100, 40), 20.0)

    assert overflow(Rect(10, 0, 80, 20), region) == []  # above the corners
    # The corner point (5, 35) is 21.2 px from the arc centre (20, 20): outside.
    assert any("lower-left corner" in problem for problem in overflow(Rect(5, 0, 20, 35), region))
    # (8, 30) is 15.6 px from it: inside the arc.
    assert overflow(Rect(8, 0, 20, 30), region) == []
    assert any("top edge" in problem for problem in overflow(Rect(10, -1, 10, 10), region))


# --- The rule on configurations -----------------------------------------------------


def test_the_production_defaults_are_valid():
    assert visual_config_problems(NotchVisualConfig()) == []
    assert NotchEyeStyle().height_share == BASE_EYE_HEIGHT_SHARE
    assert NotchEyeStyle().motion_gain == MOTION_GAIN


@pytest.mark.parametrize("presence", [NotchPresence.COMPACT, NotchPresence.EXPANDED])
def test_the_defaults_keep_every_eye_inside_the_safe_region(presence):
    defaults = NotchVisualConfig()
    window = nominal_window(defaults.geometry)
    shape = defaults.geometry.shape_for(window, presence)
    shape = NotchShape(
        Rect(window.width / 2 - shape.bounds.width / 2, 0.0, shape.bounds.width, shape.bounds.height),
        shape.corner_radius,
    )
    layout = eye_layout_for(defaults, ENVELOPE)

    for side in ("left", "right"):
        assert overflow(layout.motion_bounds(side, ENVELOPE), safe_region(shape)) == []


def test_the_previous_defaults_are_rejected_as_clipping():
    # The layout that clipped: eyes sized for the resting pose, full motion.
    problems = visual_config_problems(config(height_share=0.375, motion_gain=1.0))

    assert problems and all(problem.startswith("COMPACT") for problem in problems)


def test_the_largest_valid_eye_scale_is_a_sharp_boundary():
    # At the default gain the limit lies between 0.291 and 0.292.
    assert visual_config_problems(config(height_share=0.291)) == []
    assert visual_config_problems(config(height_share=0.293)) != []


def test_a_compact_notch_too_short_for_the_eyes_is_invalid():
    # The eye scale follows the compact height, so the envelope shrinks with
    # it; the fixed safe margin does not, and a short enough notch fails.
    assert visual_config_problems(with_geometry(compact=NotchSize(200.0, 24.0, 10.0))) == []
    assert visual_config_problems(with_geometry(compact=NotchSize(200.0, 16.0, 8.0))) != []


def test_an_eye_scale_too_large_is_invalid():
    assert visual_config_problems(config(height_share=0.4)) != []


@pytest.mark.parametrize("eyes", [{"top_offset": 2.0}, {"top_offset": -2.0}, {"center_offset": 90.0}, {"spacing": 2000.0}])
def test_eyes_placed_too_close_to_an_edge_are_invalid(eyes):
    assert visual_config_problems(config(**eyes)) != []


def test_a_small_shift_within_the_slack_stays_valid():
    assert visual_config_problems(config(top_offset=0.4)) == []
    assert visual_config_problems(config(center_offset=40.0)) == []


def test_an_expanded_notch_whose_corners_reach_the_eyes_is_invalid():
    # Square compact corners leave room the expanded notch's large lower
    # radius takes away again: the expanded check catches it.
    geometry = NotchGeometry(
        compact=NotchSize(200.0, 32.0, 0.0),
        expanded=NotchSize(200.0, 40.0, 20.0),
    )
    # The right eye's envelope corner ends at about (196.8, 30.4), 19.8 px from
    # the expanded safe arc's centre (180, 20): outside its 19 px radius.
    style = NotchEyeStyle(spacing=1860.0)
    compact_only = NotchVisualConfig(
        geometry=NotchGeometry(compact=geometry.compact, expanded=geometry.compact), eye_style=style
    )
    assert visual_config_problems(compact_only) == []

    problems = visual_config_problems(NotchVisualConfig(geometry=geometry, eye_style=style))

    assert problems and problems[0].startswith("EXPANDED") and "corner" in problems[0]


def test_impossible_geometry_never_reaches_the_rule():
    with pytest.raises(ValueError):
        NotchSize(200.0, 32.0, 20.0)  # radius larger than half the height
    with pytest.raises(ValueError):
        NotchGeometry(compact=NotchSize(200.0, 32.0, 14.0), expanded=NotchSize(100.0, 96.0, 24.0))
    with pytest.raises(ValueError):
        NotchVisualConfig(transition_duration_ms=-1)
    with pytest.raises(ValueError):
        NotchVisualConfig(transition_duration_ms=1.5)


def test_validation_raises_with_every_problem():
    with pytest.raises(InvalidVisualConfig) as raised:
        validate_visual_config(config(height_share=0.4))

    assert raised.value.problems == visual_config_problems(config(height_share=0.4))


def test_the_rule_modules_import_no_qt():
    root = pathlib.Path(__file__).resolve().parents[1]
    for module in ("mira/ui/notch/visual_config.py", "mira/ui/notch/eye_layout.py", "mira/domain/motion_envelope.py"):
        tree = ast.parse((root / module).read_text())
        imported = {
            (node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        } | {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert "PySide6" not in imported, module


# --- Painted pixels -------------------------------------------------------------------


qt = pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QImage, QRegion  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mira.ui.face.face_animator import FaceAnimator  # noqa: E402
from mira.ui.notch.notch_window import NotchWindow  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


class FixedFrameController:
    def __init__(self, frame: EmbodimentFrame):
        self.frame = frame

    def update(self, dt_seconds: float):
        pass

    def get_frame(self) -> EmbodimentFrame:
        return self.frame


def extreme_frames():
    """Frames at every corner of the envelope, at its largest size."""
    for x, y in itertools.product(
        (ENVELOPE.offset_x_min, ENVELOPE.offset_x_max), (ENVELOPE.offset_y_min, ENVELOPE.offset_y_max)
    ):
        eye = EyeFrame(
            offset_x=x / FACE_WIDTH_UNITS,
            offset_y=y / FACE_HEIGHT_UNITS,
            width_scale=ENVELOPE.width_scale_max,
            height_scale=ENVELOPE.height_scale_max,
        )
        yield f"{x:+.0f},{y:+.0f}", EmbodimentFrame(left_eye=eye, right_eye=eye)


def lit_rows_and_columns(window: NotchWindow) -> tuple[range, range] | None:
    image = QImage(window.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painted = QRegion(window.rect())
    if not window.mask().isEmpty():
        painted &= window.mask()
    window.render(image, painted.boundingRect().topLeft(), QRegion(), QWidget.RenderFlag.DrawChildren)
    lit = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).red() > 32
    ]
    if not lit:
        return None
    return (
        range(min(y for _, y in lit), max(y for _, y in lit) + 1),
        range(min(x for x, _ in lit), max(x for x, _ in lit) + 1),
    )


@pytest.mark.parametrize("presence", [NotchPresence.COMPACT, NotchPresence.EXPANDED])
@pytest.mark.parametrize("label,frame", list(extreme_frames()))
def test_extreme_frames_are_painted_inside_the_notch_with_room_to_spare(presence, label, frame):
    notch = NotchWindow(animator=FaceAnimator(FixedFrameController(frame)))
    try:
        notch.set_presence(presence, animated=False)
        shape = notch.shape.bounds
        # The painter's own rectangle for the frame, against the safe region.
        for side, eye in (("left", notch.eye_painter.left), ("right", notch.eye_painter.right)):
            rect = notch.eye_painter.eye_rect(notch.eye_canvas, eye, frame.left_eye)
            painted = Rect(rect.x(), rect.y(), rect.width(), rect.height())
            assert overflow(painted, safe_region(notch.shape)) == [], (side, label)

        rows, columns = lit_rows_and_columns(notch)
        # Nothing lit in the outermost pixel row or column of the shape: no
        # eye pixel is cut by the outline.
        assert rows.start >= shape.y + 1 and rows.stop <= shape.bottom - 1, label
        assert columns.start >= shape.x + 1 and columns.stop <= shape.right - 1, label
    finally:
        notch.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_window_refuses_a_clipping_configuration_and_keeps_its_own():
    notch = NotchWindow()
    try:
        before = notch.visual_config
        with pytest.raises(InvalidVisualConfig):
            notch.apply_visual_config(config(height_share=0.4))
        assert notch.visual_config == before

        valid = NotchVisualConfig(eye_style=NotchEyeStyle(height_share=0.25, motion_gain=0.6), transition_duration_ms=300)
        notch.apply_visual_config(valid)
        assert notch.visual_config == valid
        assert notch.eye_painter.offset_gain == 0.6
    finally:
        notch.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
