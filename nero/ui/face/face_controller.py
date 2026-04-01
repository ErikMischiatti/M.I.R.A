import math
import random

from nero.ui.face.expression_store import load_expression_library, reset_expression
from nero.ui.face.face_state import FaceState


class FaceController:
    def __init__(self):
        self.expression_library = load_expression_library()

        self.state = FaceState.IDLE
        self.profile = self.expression_library[self.state]

        self.left_eye_closed = False
        self.right_eye_closed = False

        self.current_offset_x = 0.0
        self.current_offset_y = 0.0
        self.target_offset_x = 0.0
        self.target_offset_y = 0.0

        self.current_height_scale = 1.0
        self.target_height_scale = 1.0

        self.current_width_scale = 1.0
        self.target_width_scale = 1.0

        self.current_corner_radius = 28.0
        self.target_corner_radius = 28.0

        self.current_eyelid_tired = 0.0
        self.target_eyelid_tired = 0.0

        self.current_eyelid_angry = 0.0
        self.target_eyelid_angry = 0.0

        self.current_eyelid_happy = 0.0
        self.target_eyelid_happy = 0.0

        self.speaking_phase = 0.0

        self.blink_interval_frames = 100
        self.blink_frame_counter = 0
        self.blink_duration_frames = 5
        self.blink_active_frames = 0

        self.idle_change_interval_frames = 60
        self.idle_frame_counter = 0

        self.apply_profile()

    def random_blink_interval(self) -> int:
        min_frames = min(
            self.profile.blink_min_interval_frames,
            self.profile.blink_max_interval_frames,
        )
        max_frames = max(
            self.profile.blink_min_interval_frames,
            self.profile.blink_max_interval_frames,
        )
        return random.randint(min_frames, max_frames)

    def set_state(self, new_state: FaceState):
        self.state = new_state
        self.profile = self.expression_library[self.state]
        self.apply_profile()

    def apply_profile(self):
        self.target_width_scale = self.profile.width_scale
        self.target_height_scale = self.profile.height_scale
        self.target_offset_x = self.profile.offset_x
        self.target_offset_y = self.profile.offset_y
        self.target_corner_radius = self.profile.corner_radius

        self.target_eyelid_tired = self.profile.eyelid_tired
        self.target_eyelid_angry = self.profile.eyelid_angry
        self.target_eyelid_happy = self.profile.eyelid_happy

        self.blink_duration_frames = self.profile.blink_duration_frames
        self.blink_interval_frames = self.random_blink_interval()

    def choose_idle_target(self):
        if not self.profile.idle_enabled:
            return

        self.target_offset_x = self.profile.offset_x + random.uniform(
            -self.profile.idle_amplitude_x,
            self.profile.idle_amplitude_x,
        )
        self.target_offset_y = self.profile.offset_y + random.uniform(
            -self.profile.idle_amplitude_y,
            self.profile.idle_amplitude_y,
        )

    def lerp(self, current: float, target: float, alpha: float) -> float:
        return current + (target - current) * alpha

    def update_blink(self):
        if not self.profile.blink_enabled:
            self.left_eye_closed = False
            self.right_eye_closed = False
            return

        if self.blink_active_frames > 0:
            self.blink_active_frames -= 1
            self.left_eye_closed = True
            self.right_eye_closed = True

            if self.blink_active_frames == 0:
                self.left_eye_closed = False
                self.right_eye_closed = False
                self.blink_frame_counter = 0
                self.blink_interval_frames = self.random_blink_interval()
            return

        self.blink_frame_counter += 1
        if self.blink_frame_counter >= self.blink_interval_frames:
            self.blink_active_frames = self.blink_duration_frames

    def update_idle_behavior(self):
        if not self.profile.idle_enabled:
            return

        self.idle_frame_counter += 1
        if self.idle_frame_counter >= self.idle_change_interval_frames:
            self.idle_frame_counter = 0
            self.choose_idle_target()

    def update_state_animation(self):
        if self.profile.thinking_drift:
            self.target_offset_x = self.profile.offset_x + (-8.0 + 8.0 * math.sin(self.speaking_phase * 0.35))
            self.target_offset_y = self.profile.offset_y

        if self.profile.speaking_pulse:
            self.speaking_phase += 0.22
            self.target_height_scale = self.profile.height_scale + 0.16 * abs(math.sin(self.speaking_phase))
        else:
            self.target_height_scale = self.profile.height_scale

        self.speaking_phase += 0.05

    def update_interpolation(self):
        self.current_offset_x = self.lerp(self.current_offset_x, self.target_offset_x, 0.08)
        self.current_offset_y = self.lerp(self.current_offset_y, self.target_offset_y, 0.08)
        self.current_height_scale = self.lerp(self.current_height_scale, self.target_height_scale, 0.10)
        self.current_width_scale = self.lerp(self.current_width_scale, self.target_width_scale, 0.10)
        self.current_corner_radius = self.lerp(self.current_corner_radius, self.target_corner_radius, 0.10)

        self.current_eyelid_tired = self.lerp(self.current_eyelid_tired, self.target_eyelid_tired, 0.10)
        self.current_eyelid_angry = self.lerp(self.current_eyelid_angry, self.target_eyelid_angry, 0.10)
        self.current_eyelid_happy = self.lerp(self.current_eyelid_happy, self.target_eyelid_happy, 0.10)

    def update(self):
        self.update_blink()
        self.update_idle_behavior()
        self.update_state_animation()
        self.update_interpolation()

    def get_profile(self):
        return self.profile

    def refresh_profile_targets(self):
        self.apply_profile()

    def save_profiles(self):
        from nero.ui.face.expression_store import save_expression_library
        save_expression_library(self.expression_library)

    def reload_profiles(self):
        self.expression_library = load_expression_library()
        self.profile = self.expression_library[self.state]
        self.apply_profile()

    def reset_current_profile(self):
        self.expression_library[self.state] = reset_expression(self.state)
        self.profile = self.expression_library[self.state]
        self.apply_profile()