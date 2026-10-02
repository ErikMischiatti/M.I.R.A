"""Semantic cues: brief, shared visual reactions of MIRA's eyes.

Five related ideas stay apart:

- *activity* (`ActivityState`): what MIRA is doing — idle, thinking, …;
- *expression* (`ExpressionKey`): the face resolved from activity and affect;
- a *notice* (`Notice`, `NoticeTone`): something worth a glance, described
  for any embodiment;
- *presence* (`NotchPresence`): how much of one surface is visible;
- a *cue* (`EmbodimentCue`, here): MIRA itself reacting to a result or a
  condition — success, warning, error — for a moment.

A cue is an *overlay*, never a new expression. It does not change the intent,
the expression or the micro-motions underneath; while it plays, the eyes take
a semantic shape (`EyeShape`), and when it ends the frame is simply the
expression again, whatever happened to the expression meanwhile:

    THINKING ── WARNING cue ──▶ triangle eyes ──▶ cue ends ──▶ THINKING

Because the cue lives in the embodiment (`FaceController` →
`EmbodimentFrame`), every embodiment showing MIRA shows it — the notch, the
main window's face, a future small display — each drawing the shape its own
way. A surface's own decoration (the notch's symbol row) is not a cue.

Lifetime is counted in the controller's own frame time, so a cue needs no
timer: ease in (`TRANSITION_S`: the natural eye closes, then opens as the
shape), hold for its treatment's ``hold_s``, ease out the same way.

Priority is deterministic: ERROR > WARNING > SUCCESS. A cue of equal or
higher priority replaces the one playing (the same cue just holds again); a
lower one is dropped, not queued — once the reaction is over it would be
stale. Nothing ongoing is a cue: a long-running task would be an activity.

Only cues with a treatment are listed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mira.domain.notice import Notice, NoticeTone


class EmbodimentCue(StrEnum):
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class EyeShape(StrEnum):
    """How an eye is drawn; NATURAL is the expression's own rounded eye."""

    NATURAL = "natural"
    CHEVRON = "chevron"
    """An upturned ^: the closed, smiling eye."""
    TRIANGLE = "triangle"
    """An upward triangle, like a warning sign."""
    CROSS = "cross"
    """An X."""


@dataclass(frozen=True, slots=True)
class CueTreatment:
    shape: EyeShape
    hold_s: float
    priority: int


CUE_TREATMENTS = {
    EmbodimentCue.SUCCESS: CueTreatment(EyeShape.CHEVRON, hold_s=0.9, priority=1),
    EmbodimentCue.WARNING: CueTreatment(EyeShape.TRIANGLE, hold_s=1.6, priority=2),
    EmbodimentCue.ERROR: CueTreatment(EyeShape.CROSS, hold_s=2.2, priority=3),
}

# Each way: half closing the natural eye, half opening the shape.
TRANSITION_S = 0.2

_TONE_CUE = {
    NoticeTone.SUCCESS: EmbodimentCue.SUCCESS,
    NoticeTone.WARNING: EmbodimentCue.WARNING,
    NoticeTone.ERROR: EmbodimentCue.ERROR,
}


def cue_for_notice(notice: Notice) -> EmbodimentCue | None:
    """The reaction a notice deserves; plain information gets none."""
    return _TONE_CUE.get(notice.tone)


class CuePlayback:
    """The cue playing now and how far it has appeared, in [0, 1].

    ``amount`` below 0.5 is the natural eye closing (0 open, 0.5 shut); from
    0.5 the shape opens, fully drawn at 1. Advanced by elapsed seconds.
    """

    def __init__(self):
        self._cue: EmbodimentCue | None = None
        self._elapsed = 0.0

    @property
    def cue(self) -> EmbodimentCue | None:
        return self._cue

    @property
    def shape(self) -> EyeShape:
        return EyeShape.NATURAL if self._cue is None else CUE_TREATMENTS[self._cue].shape

    @property
    def amount(self) -> float:
        if self._cue is None:
            return 0.0
        hold = CUE_TREATMENTS[self._cue].hold_s
        t = self._elapsed
        if t < TRANSITION_S:
            return t / TRANSITION_S
        if t < TRANSITION_S + hold:
            return 1.0
        return max(1.0 - (t - TRANSITION_S - hold) / TRANSITION_S, 0.0)

    def show(self, cue: EmbodimentCue) -> bool:
        """Play ``cue`` unless a higher-priority one is playing; see module."""
        current = self._cue
        if current is not None and CUE_TREATMENTS[cue].priority < CUE_TREATMENTS[current].priority:
            return False
        if current is cue:
            # Hold again from wherever it is, without re-closing the eye.
            self._elapsed = TRANSITION_S * min(self.amount, 1.0)
        elif current is not None and self.amount > 0.5:
            # Another shape is showing: continue from the shut eye.
            self._elapsed = TRANSITION_S / 2.0
            self._cue = cue
        else:
            self._elapsed = TRANSITION_S * min(self.amount, 0.5) if current is not None else 0.0
            self._cue = cue
        return True

    def clear(self) -> None:
        self._cue = None
        self._elapsed = 0.0

    def advance(self, dt_seconds: float) -> None:
        if self._cue is None:
            return
        self._elapsed += max(dt_seconds, 0.0)
        if self._elapsed >= 2 * TRANSITION_S + CUE_TREATMENTS[self._cue].hold_s:
            self.clear()
