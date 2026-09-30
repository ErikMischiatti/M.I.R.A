from mira.ui.face.expression_store import load_expression_library, reset_expression
from mira.domain.attention import AttentionTarget
from mira.domain.embodiment import (
    ActivityState,
    EmbodimentIntent,
    ExpressionKey,
    resolve_expression_key,
)
from mira.domain.embodiment_frame import (
    EmbodimentFrame,
    resolve_embodiment_frame,
)
from mira.domain.embodiment_playback import (
    EmbodimentPlayback,
    EyeAsymmetry,
    PlaybackPose,
)
from mira.domain.gaze import gaze_weight_for
from mira.domain.micro_motion import MicroMotions
from mira.domain.state import FaceState


class FaceController:
    def __init__(self):
        self.expression_library = load_expression_library()

        self.state = FaceState.IDLE
        self.intent = EmbodimentIntent(ActivityState.IDLE)
        self.expression_key = ExpressionKey.IDLE
        self.profile = self.expression_library[self.state]

        self._motions = MicroMotions()

        self._playback = EmbodimentPlayback()
        self._base_pose = PlaybackPose()
        self._target_pose = PlaybackPose()
        self._asymmetry = EyeAsymmetry()

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
        definitions = {
            ExpressionKey[state.name]: profile.to_definition()
            for state, profile in self.expression_library.items()
        }
        self.base_frame = resolve_embodiment_frame(self.intent, definitions)
        definition = definitions[self.expression_key]
        self._base_pose = PlaybackPose.from_definition(definition)
        self._asymmetry = EyeAsymmetry.from_definition(definition)

        self._motions.configure(
            self.profile.to_motion_settings(
                gaze_weight=gaze_weight_for(self.expression_key)
            )
        )
        self._target_pose = self._motions.state.apply_to(self._base_pose)

    @property
    def attention_target(self) -> AttentionTarget | None:
        return self._motions.attention_target

    def set_attention_target(self, target: AttentionTarget):
        self._motions.set_attention_target(target)

    def clear_attention_target(self):
        self._motions.clear_attention_target()

    def update(self, dt_seconds: float):
        motion = self._motions.step()
        self._target_pose = motion.apply_to(self._base_pose)
        self._playback.advance(
            self._target_pose,
            dt_seconds,
            asymmetry=self._asymmetry,
            left_eye_closed=motion.eyes_closed,
            right_eye_closed=motion.eyes_closed,
        )

    def get_profile(self):
        return self.profile

    @property
    def target_pose(self) -> PlaybackPose:
        return self._target_pose

    @property
    def current_pose(self) -> PlaybackPose:
        return self._playback.current_pose

    @property
    def eyes_closed(self) -> bool:
        return self._motions.state.eyes_closed

    def get_frame(self) -> EmbodimentFrame:
        """Snapshot the fully resolved instantaneous pose for any renderer."""

        return self._playback.get_frame(
            asymmetry=self._asymmetry,
            left_eye_closed=self.eyes_closed,
            right_eye_closed=self.eyes_closed,
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
