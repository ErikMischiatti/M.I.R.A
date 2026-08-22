"""Renderer-independent description of MIRA's resolved visual pose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mira.domain.embodiment import EmbodimentIntent, ExpressionKey, resolve_expression_key


# Offsets and radii are normalized from the current face's design coordinate
# space. Adapters may scale them to pixels, millimetres, or another output unit.
FACE_WIDTH_UNITS = 700.0
FACE_HEIGHT_UNITS = 450.0


@dataclass(frozen=True, slots=True)
class EyeFrame:
    """One eye's resolved geometry and eyelid pose at an instant."""

    offset_x: float = 0.0
    offset_y: float = 0.0
    width_scale: float = 1.0
    height_scale: float = 1.0
    corner_radius: float = 28.0 / FACE_WIDTH_UNITS
    closed: bool = False
    tired_lid: float = 0.0
    angry_lid: float = 0.0
    happy_lid: float = 0.0


@dataclass(frozen=True, slots=True)
class EmbodimentFrame:
    """The complete visual data required by the current two-eye renderer."""

    left_eye: EyeFrame
    right_eye: EyeFrame


@dataclass(frozen=True, slots=True)
class ExpressionDefinition:
    """Pure geometry from which an expression's unanimated frame is derived."""

    name: str
    width_scale: float = 1.0
    height_scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    corner_radius: float = 28.0
    asymmetry_offset_y_left: float = 0.0
    asymmetry_offset_y_right: float = 0.0
    asymmetry_height_left: float = 1.0
    asymmetry_height_right: float = 1.0
    eyelid_tired: float = 0.0
    eyelid_angry: float = 0.0
    eyelid_happy: float = 0.0


def frame_from_definition(definition: ExpressionDefinition) -> EmbodimentFrame:
    """Resolve a definition into its deterministic, unanimated base pose."""

    common = {
        "offset_x": definition.offset_x / FACE_WIDTH_UNITS,
        "width_scale": definition.width_scale,
        "corner_radius": definition.corner_radius / FACE_WIDTH_UNITS,
        "tired_lid": definition.eyelid_tired,
        "angry_lid": definition.eyelid_angry,
        "happy_lid": definition.eyelid_happy,
    }
    return EmbodimentFrame(
        left_eye=EyeFrame(
            **common,
            offset_y=(definition.offset_y + definition.asymmetry_offset_y_left)
            / FACE_HEIGHT_UNITS,
            height_scale=definition.height_scale * definition.asymmetry_height_left,
        ),
        right_eye=EyeFrame(
            **common,
            offset_y=(definition.offset_y + definition.asymmetry_offset_y_right)
            / FACE_HEIGHT_UNITS,
            height_scale=definition.height_scale * definition.asymmetry_height_right,
        ),
    )


def resolve_embodiment_frame(
    intent: EmbodimentIntent,
    definitions: Mapping[ExpressionKey, ExpressionDefinition],
) -> EmbodimentFrame:
    """Resolve semantic intent deterministically; performs no I/O or mutation."""

    return frame_from_definition(definitions[resolve_expression_key(intent)])
