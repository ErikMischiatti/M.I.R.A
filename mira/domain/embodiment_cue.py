"""Semantic cues: brief, shared visual reactions of MIRA's eyes.

Five related ideas stay apart:

- *activity* (`ActivityState`): what MIRA is doing — idle, thinking, …;
- *expression* (`ExpressionKey`): the face resolved from activity and affect;
- a *notice* (`Notice`, `NoticeTone`): something worth a glance, described
  for any embodiment;
- *presence* (`NotchPresence`): how much of one surface is visible;
- a *cue* (`EmbodimentCue`, here): MIRA itself reacting to a result or a
  condition — success, warning, error — or greeting a device that arrived,
  for a moment.

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

Priority is deterministic: ERROR > WARNING > SUCCESS > GREETING. A cue of
equal or higher priority replaces the one playing (the same cue just holds
again); a lower one is dropped, not queued — once the reaction is over it
would be stale.

Ongoing states are not cues but *conditions* (`EmbodimentCondition`): true
for as long as something lasts, with no timeout — TASK_RUNNING while any
action is executing. A condition has its own restrained treatment
(`CONDITION_SHAPES`), eased in and out by `ConditionPlayback`. The frame
resolves the layers in a fixed order:

    a cue that is showing      >  the strongest active condition  >  the expression

so an ERROR shows over TASK_RUNNING, and when it ends the working eyes come
back if the task still runs — or the plain expression if it ended meanwhile.
Neither layer ever writes into the one beneath.

Only cues and conditions with a treatment are listed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from mira.domain.notice import Notice, NoticeTone


class EmbodimentCue(StrEnum):
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    GREETING = "greeting"
    """A brief smile for a device that arrived; never over anything else."""


class EyeShape(StrEnum):
    """How an eye is drawn; NATURAL is the expression's own rounded eye."""

    NATURAL = "natural"
    CHEVRON = "chevron"
    """An upturned ^: the closed, smiling eye."""
    TRIANGLE = "triangle"
    """An upward triangle, like a warning sign."""
    CROSS = "cross"
    """An X."""
    FOCUSED = "focused"
    """The natural eye, narrowed: concentrating. Blinks still apply."""


@dataclass(frozen=True, slots=True)
class CueTreatment:
    shape: EyeShape
    hold_s: float
    priority: int


CUE_TREATMENTS = {
    EmbodimentCue.SUCCESS: CueTreatment(EyeShape.CHEVRON, hold_s=0.9, priority=1),
    EmbodimentCue.WARNING: CueTreatment(EyeShape.TRIANGLE, hold_s=1.6, priority=2),
    EmbodimentCue.ERROR: CueTreatment(EyeShape.CROSS, hold_s=2.2, priority=3),
    # The success smile, shorter and weaker: a glance, not a result.
    EmbodimentCue.GREETING: CueTreatment(EyeShape.CHEVRON, hold_s=0.6, priority=0),
}

# Each way: half closing the natural eye, half opening the shape.
TRANSITION_S = 0.2

_TONE_CUE = {
    NoticeTone.SUCCESS: EmbodimentCue.SUCCESS,
    NoticeTone.WARNING: EmbodimentCue.WARNING,
    NoticeTone.ERROR: EmbodimentCue.ERROR,
}


def cue_for_notice(notice: Notice) -> EmbodimentCue | None:
    """The reaction a notice deserves.

    Its tone decides first; plain information gets none, except a device
    that arrived, which MIRA greets. A device leaving gets nothing.
    """
    cue = _TONE_CUE.get(notice.tone)
    if cue is None and notice.device is not None and notice.device.connected:
        return EmbodimentCue.GREETING
    return cue


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


class EmbodimentCondition(StrEnum):
    """An ongoing state of MIRA shown for exactly as long as it holds."""

    TASK_RUNNING = "task running"


CONDITION_SHAPES = {EmbodimentCondition.TASK_RUNNING: EyeShape.FOCUSED}

# Highest first, for when several hold at once.
CONDITION_PRIORITY = (EmbodimentCondition.TASK_RUNNING,)

CONDITION_TRANSITION_S = 0.3
# The working eyes "breathe": a slow, small swing of how narrowed they are,
# between BREATH_LOW and 1 of the full treatment. Restrained on purpose.
BREATH_PERIOD_S = 2.4
BREATH_LOW = 0.8


class ConditionPlayback:
    """Which conditions hold, and how far the strongest one is drawn.

    `set` turns a condition on or off; the treatment eases in over
    `CONDITION_TRANSITION_S` and out the same way, then the shape is gone.
    `amount` already includes the breathing.
    """

    def __init__(self):
        self._active: set[EmbodimentCondition] = set()
        self._shown: EmbodimentCondition | None = None
        self._blend = 0.0
        self._phase_s = 0.0

    @property
    def active(self) -> frozenset[EmbodimentCondition]:
        return frozenset(self._active)

    def set(self, condition: EmbodimentCondition, active: bool) -> None:
        if active:
            self._active.add(condition)
        else:
            self._active.discard(condition)

    def _strongest(self) -> EmbodimentCondition | None:
        return next((c for c in CONDITION_PRIORITY if c in self._active), None)

    @property
    def shape(self) -> EyeShape:
        return EyeShape.NATURAL if self._shown is None else CONDITION_SHAPES[self._shown]

    @property
    def amount(self) -> float:
        if self._shown is None or self._blend <= 0.0:
            return 0.0
        breath = 0.5 + 0.5 * math.sin(2 * math.pi * self._phase_s / BREATH_PERIOD_S)
        return self._blend * (BREATH_LOW + (1.0 - BREATH_LOW) * breath)

    def advance(self, dt_seconds: float) -> None:
        dt = max(dt_seconds, 0.0)
        target = self._strongest()
        step = dt / CONDITION_TRANSITION_S
        if target is not None and (self._shown is None or self._shown is target):
            self._shown = target
            self._blend = min(self._blend + step, 1.0)
            self._phase_s += dt
        else:
            # Ending, or handing over to another condition: ease out first.
            self._blend = max(self._blend - step, 0.0)
            self._phase_s += dt
            if self._blend == 0.0:
                self._shown = target
                self._phase_s = 0.0
