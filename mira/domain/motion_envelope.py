"""The full range of poses an embodiment's eyes can take, as a bounding box.

A renderer that must keep the eyes inside a fixed area — the notch — needs the
extreme poses, not the resting ones. `embodiment_envelope` derives them from
the expression definitions and motion settings alone, without running any
motion, so the bound is deterministic and covers every reachable frame:

- each expression's target pose is its definition plus one micro-motion
  offset: none, idle wander (±amplitude), the thinking drift, or attention
  gaze (always possible, at the expression's gaze weight), together with the
  gaze deformation that offset implies and the speaking pulse;
- `EmbodimentPlayback` only ever moves the current pose part of the way
  towards a target (its blend factor lies in (0, 1]), so every pose it
  reaches lies inside the bounding box of the targets and its initial pose,
  across expression changes too;
- per-eye asymmetry is applied after blending and switches with the
  expression at once, so any expression's asymmetry can meet any blended
  pose; the bound combines them all.

The box is a conservative bound: every quantity is bounded independently, and
each eye's extent is monotonic in each of them. Units are those of
`PlaybackPose`: offsets in face units, scales as factors of the resting eye.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from mira.domain.embodiment_frame import ExpressionDefinition
from mira.domain.embodiment_playback import PlaybackPose
from mira.domain.gaze import gaze_reach
from mira.domain.micro_motion import (
    SPEAKING_PULSE_MAX,
    THINKING_DRIFT_RANGE,
    MotionSettings,
    gaze_deformation,
)


@dataclass(frozen=True, slots=True)
class PoseEnvelope:
    """Bounds on one eye's resolved pose over every reachable frame.

    ``offset_*`` bound the eye's displacement from its resting position, per
    eye asymmetry included; ``width_scale_max`` and ``height_scale_max`` bound
    its size relative to the resting eye. An eye's rectangle grows right and
    down from its displaced top-left corner, so these four numbers and the
    resting rectangle bound everything drawn.
    """

    offset_x_min: float
    offset_x_max: float
    offset_y_min: float
    offset_y_max: float
    width_scale_max: float
    height_scale_max: float


@dataclass(frozen=True, slots=True)
class _Box:
    offset_x: tuple[float, float]
    offset_y: tuple[float, float]
    width_scale_max: float
    height_scale_max: float

    def union(self, other: _Box) -> _Box:
        return _Box(
            offset_x=(min(self.offset_x[0], other.offset_x[0]), max(self.offset_x[1], other.offset_x[1])),
            offset_y=(min(self.offset_y[0], other.offset_y[0]), max(self.offset_y[1], other.offset_y[1])),
            width_scale_max=max(self.width_scale_max, other.width_scale_max),
            height_scale_max=max(self.height_scale_max, other.height_scale_max),
        )


def motion_offset_bounds(settings: MotionSettings) -> tuple[tuple[float, float], tuple[float, float]]:
    """The range of the micro-motion offset under ``settings``, per axis.

    The offset is always one of: neutral, the idle wander, the thinking drift
    or the attention gaze; whichever was set last persists until replaced.
    """

    xs, ys = [0.0], [0.0]
    if settings.idle_gaze:
        xs += [-abs(settings.idle_amplitude_x), abs(settings.idle_amplitude_x)]
        ys += [-abs(settings.idle_amplitude_y), abs(settings.idle_amplitude_y)]
    if settings.thinking_drift:
        xs += list(THINKING_DRIFT_RANGE)
    reach_x, reach_y = gaze_reach(settings.gaze_weight)
    xs += [-reach_x, reach_x]
    ys += [-reach_y, reach_y]
    return (min(xs), max(xs)), (min(ys), max(ys))


def target_pose_bounds(definition: ExpressionDefinition, settings: MotionSettings) -> _Box:
    """Bounds on the targets an expression's micro-motions can set."""

    (x_low, x_high), (y_low, y_high) = motion_offset_bounds(settings)
    widest = max(abs(x_low), abs(x_high))
    # Deformation widens with any horizontal offset; height grows only when
    # looking up, and shrinks with horizontal and downward offsets.
    width_delta_max = gaze_deformation(widest, 0.0)[0]
    height_delta_max = gaze_deformation(0.0, min(y_low, 0.0))[1]
    if settings.speaking_pulse:
        height_delta_max += SPEAKING_PULSE_MAX
    return _Box(
        offset_x=(definition.offset_x + x_low, definition.offset_x + x_high),
        offset_y=(definition.offset_y + y_low, definition.offset_y + y_high),
        width_scale_max=definition.width_scale + width_delta_max,
        height_scale_max=definition.height_scale + height_delta_max,
    )


def embodiment_envelope(
    expressions: Iterable[tuple[ExpressionDefinition, MotionSettings]],
    initial_pose: PlaybackPose = PlaybackPose(),
) -> PoseEnvelope:
    """Bound every frame an embodiment showing ``expressions`` can render."""

    expressions = list(expressions)
    if not expressions:
        raise ValueError("an envelope needs at least one expression")

    box = _Box(
        offset_x=(initial_pose.offset_x, initial_pose.offset_x),
        offset_y=(initial_pose.offset_y, initial_pose.offset_y),
        width_scale_max=initial_pose.width_scale,
        height_scale_max=initial_pose.height_scale,
    )
    asymmetry_offsets: list[float] = []
    asymmetry_heights: list[float] = []
    for definition, settings in expressions:
        box = box.union(target_pose_bounds(definition, settings))
        asymmetry_offsets += [definition.asymmetry_offset_y_left, definition.asymmetry_offset_y_right]
        asymmetry_heights += [definition.asymmetry_height_left, definition.asymmetry_height_right]

    if box.height_scale_max <= 0 or min(asymmetry_heights) <= 0:
        raise ValueError("eye heights must stay positive")
    return PoseEnvelope(
        offset_x_min=box.offset_x[0],
        offset_x_max=box.offset_x[1],
        offset_y_min=box.offset_y[0] + min(asymmetry_offsets),
        offset_y_max=box.offset_y[1] + max(asymmetry_offsets),
        width_scale_max=box.width_scale_max,
        height_scale_max=box.height_scale_max * max(asymmetry_heights),
    )
