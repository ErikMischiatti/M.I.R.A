import dataclasses

from mira.ui.face.expression_store import load_expression_library, reset_expression
from mira.domain.attention import AttentionTarget
from mira.domain.embodiment import (
    ActivityState,
    EmbodimentIntent,
    ExpressionKey,
    resolve_expression_key,
)
from mira.domain.embodiment_cue import ConditionPlayback, CuePlayback, EmbodimentCondition, EmbodimentCue
from mira.domain.embodiment_frame import EmbodimentFrame, frame_from_definition
from mira.domain.embodiment_playback import (
    EmbodimentPlayback,
    EyeAsymmetry,
    PlaybackPose,
)
from mira.domain.gaze import gaze_weight_for
from mira.domain.micro_motion import MicroMotions
from mira.domain.state import FaceState


class FaceController:
    """Present one expression at a time and hand renderers its current frame.

    Owns the editable expression library and the choice of profile for the
    current intent, then coordinates the renderer-independent parts: the
    profile's pose is the playback base, `MicroMotions` layers blink, idle,
    drift, pulse and attention gaze on top, and `EmbodimentPlayback`
    interpolates toward the result. Attention arrives as `AttentionTarget`
    values; the controller does not know which input produced them.

    Each controller owns its `MicroMotions`; pass one seeded with a
    `random.Random` for reproducible motion. An instance must not be shared
    between controllers, since each steps it once per frame.

    A semantic cue (`show_cue`, `mira.domain.embodiment_cue`) overlays the
    frame for a moment without touching the intent, the expression or the
    motions: when it ends the frame is the current expression again. Its
    lifetime advances with `update`, so it needs no timer of its own.

    A condition (`set_condition`) is the persistent layer between the two:
    shown for as long as it holds. A showing cue wins over it; the expression
    lies under both (see `mira.domain.embodiment_cue`).
    """

    def __init__(self, motions: MicroMotions | None = None):
        self.expression_library = load_expression_library()

        self.state = FaceState.IDLE
        self.intent = EmbodimentIntent(ActivityState.IDLE)
        self.expression_key = ExpressionKey.IDLE
        self.profile = self.expression_library[self.state]

        self._motions = MicroMotions() if motions is None else motions

        self._playback = EmbodimentPlayback()
        self._base_pose = PlaybackPose()
        self._asymmetry = EyeAsymmetry()
        self._cue = CuePlayback()
        self._conditions = ConditionPlayback()

        self.apply_profile()

    def set_state(self, new_state: FaceState):
        """Legacy presentation entry point for debug and compatibility callers."""
        self.set_intent(
            EmbodimentIntent(
                activity=ActivityState.IDLE,
                expression=ExpressionKey[new_state.name],
            )
        )

    def set_intent(self, intent: EmbodimentIntent):
        self.intent = intent
        self.expression_key = resolve_expression_key(intent)
        self.state = FaceState[self.expression_key.name]
        self.profile = self.expression_library[self.state]
        self.apply_profile()

    def apply_profile(self):
        definition = self.profile.to_definition()
        self.base_frame = frame_from_definition(definition)
        self._base_pose = PlaybackPose.from_definition(definition)
        self._asymmetry = EyeAsymmetry.from_definition(definition)

        self._motions.configure(
            self.profile.to_motion_settings(
                gaze_weight=gaze_weight_for(self.expression_key)
            )
        )

    @property
    def attention_target(self) -> AttentionTarget | None:
        return self._motions.attention_target

    def set_attention_target(self, target: AttentionTarget):
        self._motions.set_attention_target(target)

    def clear_attention_target(self):
        self._motions.clear_attention_target()

    @property
    def active_cue(self) -> EmbodimentCue | None:
        return self._cue.cue

    def show_cue(self, cue: EmbodimentCue) -> bool:
        """Overlay ``cue``; False if a higher-priority cue is playing."""
        return self._cue.show(cue)

    def clear_cue(self):
        self._cue.clear()

    @property
    def conditions(self) -> frozenset[EmbodimentCondition]:
        return self._conditions.active

    def set_condition(self, condition: EmbodimentCondition, active: bool):
        self._conditions.set(condition, active)

    def update(self, dt_seconds: float):
        self._cue.advance(dt_seconds)
        self._conditions.advance(dt_seconds)
        motion = self._motions.step()
        self._playback.advance(
            motion.apply_to(self._base_pose),
            dt_seconds,
            asymmetry=self._asymmetry,
            left_eye_closed=motion.eyes_closed,
            right_eye_closed=motion.eyes_closed,
        )

    def get_profile(self):
        return self.profile

    @property
    def target_pose(self) -> PlaybackPose:
        return self._motions.state.apply_to(self._base_pose)

    @property
    def current_pose(self) -> PlaybackPose:
        return self._playback.current_pose

    @property
    def eyes_closed(self) -> bool:
        return self._motions.state.eyes_closed

    def get_frame(self) -> EmbodimentFrame:
        """Snapshot the fully resolved instantaneous pose for any renderer."""

        frame = self._playback.get_frame(
            asymmetry=self._asymmetry,
            left_eye_closed=self.eyes_closed,
            right_eye_closed=self.eyes_closed,
        )
        # A showing cue, else the strongest condition, else the expression.
        if self._cue.amount > 0.0:
            layer = self._cue
        elif self._conditions.amount > 0.0:
            layer = self._conditions
        else:
            return frame
        cue = {"shape": layer.shape, "shape_amount": layer.amount}
        return EmbodimentFrame(
            left_eye=dataclasses.replace(frame.left_eye, **cue),
            right_eye=dataclasses.replace(frame.right_eye, **cue),
        )

    def refresh_profile_targets(self):
        self.apply_profile()

    def save_profiles(self):
        from mira.ui.face.expression_store import save_expression_library
        save_expression_library(self.expression_library)

    def reload_profiles(self):
        self.expression_library = load_expression_library()
        self.profile = self.expression_library[self.state]
        self.apply_profile()

    def reset_current_profile(self):
        self.expression_library[self.state] = reset_expression(self.state)
        self.profile = self.expression_library[self.state]
        self.apply_profile()
