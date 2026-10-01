"""Qt-free notch visual configuration and the rule that keeps eyes unclipped.

`NotchVisualConfig` bundles every visual parameter the notch takes at run
time. Its fields already reject impossible values on construction (negative
sizes, a radius that does not fit, an expanded notch smaller than the compact
one). `visual_config_problems` adds the rule that depends on all of them
together and on the embodiment's motion:

    For COMPACT and EXPANDED, and every size between them, each eye's complete
    motion envelope lies inside the notch's safe region: the drawn outline,
    flat top and rounded lower corners, inset by `SAFE_MARGIN_PX`.

A configuration that breaks the rule is invalid, not repaired: no eye is ever
moved, shrunk or clipped to make it fit. The eyes' layout is identical for
every presence (`notch_eye_layout`), so the rule is checked once per notch
size against one fixed layout.

Showing and hiding grow the notch out of, and back into, the top edge: those
transitions cut through the eyes by design and are not covered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from mira.domain.motion_envelope import PoseEnvelope
from mira.ui.notch.eye_layout import NotchEyeLayout, NotchEyeStyle, notch_eye_layout, production_envelope
from mira.ui.notch.presence import (
    NotchGeometry,
    NotchPresence,
    NotchShape,
    NotchSize,
    Rect,
    window_shape_for_size,
)


# The eyes keep this far inside the drawn outline: their antialiased edge
# spreads about half a pixel, and the window's input mask hugs the outline.
SAFE_MARGIN_PX = 1.0

# How many sizes between COMPACT and EXPANDED are checked besides the two
# ends. The blend is linear in width, height and radius.
TRANSITION_SAMPLES = 16

MAX_TRANSITION_DURATION_MS = 5000

DEFAULT_TRANSITION_DURATION_MS = 200


@dataclass(frozen=True, slots=True)
class NotchVisualConfig:
    """Everything visual the notch can be tuned in; defaults are production."""

    geometry: NotchGeometry = field(default_factory=NotchGeometry)
    eye_style: NotchEyeStyle = field(default_factory=NotchEyeStyle)
    transition_duration_ms: int = DEFAULT_TRANSITION_DURATION_MS

    def __post_init__(self):
        duration = self.transition_duration_ms
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValueError(f"transition duration must be whole milliseconds, got {duration!r}")
        if not 0 <= duration <= MAX_TRANSITION_DURATION_MS:
            raise ValueError(
                f"transition duration must be within 0..{MAX_TRANSITION_DURATION_MS} ms, got {duration}"
            )


class InvalidVisualConfig(ValueError):
    """A configuration whose eye motion cannot fit the notch."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def nominal_window(geometry: NotchGeometry) -> Rect:
    """The window-local frame the notch is drawn in, independent of screens.

    The window is the expanded notch rounded up to whole pixels; screens
    larger than the notch never change it.
    """

    return Rect(0.0, 0.0, float(math.ceil(geometry.expanded.width)), float(math.ceil(geometry.expanded.height)))


def safe_region(shape: NotchShape, margin: float = SAFE_MARGIN_PX) -> NotchShape:
    """``shape`` inset by ``margin`` on every side; the radius shrinks with it."""

    bounds = shape.bounds
    width = max(bounds.width - 2.0 * margin, 0.0)
    height = max(bounds.height - 2.0 * margin, 0.0)
    return NotchShape(
        bounds=Rect(bounds.x + margin, bounds.y + margin, width, height),
        corner_radius=max(min(shape.corner_radius - margin, width / 2.0, height / 2.0), 0.0),
    )


def overflow(rect: Rect, region: NotchShape) -> list[str]:
    """How ``rect`` leaves ``region``, a notch outline; empty when inside.

    Exact for a rectangle in a shape with a flat top and rounded lower
    corners: within the bounds, a rectangle can only leave through a lower
    corner, and then its own nearest corner is the point furthest outside.
    """

    bounds, radius = region.bounds, region.corner_radius
    problems = []
    for name, excess in (
        ("top", bounds.y - rect.y),
        ("bottom", rect.bottom - bounds.bottom),
        ("left", bounds.x - rect.x),
        ("right", rect.right - bounds.right),
    ):
        if excess > 1e-9:
            problems.append(f"{name} edge by {excess:.2f} px")
    if problems or radius <= 0:
        return problems

    corner_y = bounds.bottom - radius
    for name, corner_x, x in (
        ("lower-left", bounds.x + radius, rect.x),
        ("lower-right", bounds.right - radius, rect.right),
    ):
        outside_x = corner_x - x if name == "lower-left" else x - corner_x
        outside_y = rect.bottom - corner_y
        if outside_x > 0 and outside_y > 0:
            excess = math.hypot(outside_x, outside_y) - radius
            if excess > 1e-9:
                problems.append(f"{name} corner by {excess:.2f} px")
    return problems


def eye_layout_for(config: NotchVisualConfig, envelope: PoseEnvelope) -> NotchEyeLayout:
    return notch_eye_layout(config.geometry, nominal_window(config.geometry), config.eye_style, envelope)


def _checked_sizes(geometry: NotchGeometry) -> list[tuple[str, NotchSize]]:
    compact, expanded = geometry.size_for(NotchPresence.COMPACT), geometry.size_for(NotchPresence.EXPANDED)
    sizes = [("COMPACT", compact), ("EXPANDED", expanded)]
    for step in range(1, TRANSITION_SAMPLES):
        progress = step / TRANSITION_SAMPLES
        sizes.append((f"COMPACT→EXPANDED at {progress:.0%}", compact.lerp(expanded, progress)))
    return sizes


def visual_config_problems(config: NotchVisualConfig, envelope: PoseEnvelope | None = None) -> list[str]:
    """Why ``config`` would clip the eyes; empty for a valid configuration.

    ``envelope`` is the embodiment's motion envelope, by default that of the
    packaged expression profiles.
    """

    envelope = production_envelope() if envelope is None else envelope
    window = nominal_window(config.geometry)
    layout = eye_layout_for(config, envelope)
    eyes = {side: layout.motion_bounds(side, envelope) for side in ("left", "right")}

    problems = []
    for label, size in _checked_sizes(config.geometry):
        region = safe_region(window_shape_for_size(window, config.geometry, size))
        for side, bounds in eyes.items():
            excess = overflow(bounds, region)
            if excess:
                problems.append(f"{label}: {side} eye motion leaves the safe area ({', '.join(excess)})")
        if problems:
            # The first size that fails says enough; the rest repeat it.
            break
    return problems


def validate_visual_config(config: NotchVisualConfig, envelope: PoseEnvelope | None = None) -> NotchVisualConfig:
    """Return ``config`` if valid; raise `InvalidVisualConfig` otherwise."""

    problems = visual_config_problems(config, envelope)
    if problems:
        raise InvalidVisualConfig(problems)
    return config
