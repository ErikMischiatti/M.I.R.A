import math

from mira.ui.face.expression_store import load_expression_library, reset_expression
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

        self.look_tracking_enabled = False
        self.look_target_x = 0.5
        self.look_target_y = 0.5

        self.look_strength_x = 82.0
        self.look_strength_y = 54.0
        self.look_deadzone = 0.035

        self.attention_target_x = 0.5
        self.attention_target_y = 0.5

        self.look_enter_boost_frames = 0
        self.look_hold_frames = 0

        self.scrutiny_phase = 0.0
        self.scrutiny_offset_x = 0.0
        self.scrutiny_offset_y = 0.0
        self.scrutiny_radius_x = 0.12
        self.scrutiny_radius_y = 0.07
        self.scrutiny_speed = 0.11
        self.scrutiny_activation = 0.0

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

        self._motions.configure(self.profile.to_motion_settings())
        self._target_pose = self._motions.state.apply_to(self._base_pose)

    def set_look_target(self, x_ratio: float, y_ratio: float):
        x_ratio = max(0.0, min(1.0, x_ratio))
        y_ratio = max(0.0, min(1.0, y_ratio))

        was_disabled = not self.look_tracking_enabled

        self.look_tracking_enabled = True
        self.look_target_x = x_ratio
        self.look_target_y = y_ratio

        if was_disabled:
            self.look_enter_boost_frames = 10

    def clear_look_target(self):
        self.look_tracking_enabled = False
        self.look_hold_frames = 10

    def lerp(self, current: float, target: float, alpha: float) -> float:
        return current + (target - current) * alpha

    def mouse_look_offset(self) -> tuple[float, float]:
        look_x = (self.attention_target_x - 0.5) * 2.0
        look_y = (self.attention_target_y - 0.5) * 2.0

        if abs(look_x) < self.look_deadzone:
            look_x = 0.0
        if abs(look_y) < self.look_deadzone:
            look_y = 0.0

        look_x = math.copysign(abs(look_x) ** 1.35, look_x)
        look_y = math.copysign(abs(look_y) ** 1.35, look_y)

        ellipse_norm = (look_x ** 2) + (look_y ** 2)
        if ellipse_norm > 1.0:
            scale = 1.0 / math.sqrt(ellipse_norm)
            look_x *= scale
            look_y *= scale

        look_x += self.scrutiny_offset_x
        look_y += self.scrutiny_offset_y

        weight = self.get_state_look_weight()

        return (
            look_x * self.look_strength_x * weight,
            look_y * self.look_strength_y * weight,
        )

    def update(self, dt_seconds: float):
        self.update_attention_target()
        self.update_scrutiny_motion()
        gaze_offset = None
        if self.look_tracking_enabled or self.look_hold_frames > 0:
            gaze_offset = self.mouse_look_offset()
        motion = self._motions.step(gaze_offset)
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

    def get_state_look_weight(self) -> float:
        if self.state == FaceState.IDLE:
            return 1.00
        if self.state == FaceState.LISTENING:
            return 0.95
        if self.state == FaceState.THINKING:
            return 0.45
        if self.state == FaceState.SPEAKING:
            return 0.35
        if self.state == FaceState.HAPPY:
            return 0.80
        if self.state == FaceState.CONFUSED:
            return 0.75
        if self.state == FaceState.TIRED:
            return 0.50
        if self.state == FaceState.ANGRY:
            return 0.65
        return 0.75

    def update_attention_target(self):
        if self.look_tracking_enabled:
            alpha = 0.18
            if self.look_enter_boost_frames > 0:
                alpha = 0.32
                self.look_enter_boost_frames -= 1

            self.attention_target_x = self.lerp(self.attention_target_x, self.look_target_x, alpha)
            self.attention_target_y = self.lerp(self.attention_target_y, self.look_target_y, alpha)
            self.look_hold_frames = 0
            return

        if self.look_hold_frames > 0:
            self.look_hold_frames -= 1
            return

        self.attention_target_x = self.lerp(self.attention_target_x, 0.5, 0.08)
        self.attention_target_y = self.lerp(self.attention_target_y, 0.5, 0.08)

    def update_scrutiny_motion(self):
        moving_x = abs(self.look_target_x - self.attention_target_x)
        moving_y = abs(self.look_target_y - self.attention_target_y)
        is_settled = (moving_x + moving_y) < 0.05

        should_scrutinize = self.look_tracking_enabled and is_settled

        target_activation = 1.0 if should_scrutinize else 0.0
        self.scrutiny_activation = self.lerp(self.scrutiny_activation, target_activation, 0.08)

        if self.scrutiny_activation < 0.01:
            self.scrutiny_offset_x = self.lerp(self.scrutiny_offset_x, 0.0, 0.20)
            self.scrutiny_offset_y = self.lerp(self.scrutiny_offset_y, 0.0, 0.20)
            return

        self.scrutiny_phase += self.scrutiny_speed

        orbit_x = math.cos(self.scrutiny_phase) * self.scrutiny_radius_x
        orbit_y = math.sin(self.scrutiny_phase) * self.scrutiny_radius_y

        self.scrutiny_offset_x = orbit_x * self.scrutiny_activation
        self.scrutiny_offset_y = orbit_y * self.scrutiny_activation
