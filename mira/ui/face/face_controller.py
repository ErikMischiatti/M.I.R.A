import math
import random

from mira.ui.face.expression_store import load_expression_library, reset_expression
from mira.domain.state import FaceState


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

        self.look_tracking_enabled = False
        self.look_target_x = 0.5
        self.look_target_y = 0.5

        self.look_strength_x = 82.0
        self.look_strength_y = 54.0
        self.look_deadzone = 0.035

        self.speaking_phase = 0.0

        self.blink_interval_frames = 100
        self.blink_frame_counter = 0
        self.blink_duration_frames = 5
        self.blink_active_frames = 0

        self.idle_change_interval_frames = 60
        self.idle_frame_counter = 0

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
            self.idle_change_interval_frames = random.randint(40, 90)
            self.choose_idle_target()

    def apply_mouse_look_target(self):
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

        self.target_offset_x = self.profile.offset_x + look_x * self.look_strength_x * weight
        self.target_offset_y = self.profile.offset_y + look_y * self.look_strength_y * weight

    def update_state_animation(self):
        self.target_width_scale = self.profile.width_scale
        self.target_height_scale = self.profile.height_scale

        if not self.profile.idle_enabled:
            self.target_offset_x = self.profile.offset_x
            self.target_offset_y = self.profile.offset_y

        if self.profile.thinking_drift:
            self.target_offset_x = self.profile.offset_x + (-8.0 + 8.0 * math.sin(self.speaking_phase * 0.35))
            self.target_offset_y = self.profile.offset_y

        if self.profile.speaking_pulse:
            self.speaking_phase += 0.22
            self.target_height_scale = self.profile.height_scale + 0.16 * abs(math.sin(self.speaking_phase))

        if self.look_tracking_enabled or self.look_hold_frames > 0:
            self.apply_mouse_look_target()

        self.apply_look_deformation()

        self.speaking_phase += 0.05

    def update_interpolation(self):
        self.current_offset_x = self.lerp(self.current_offset_x, self.target_offset_x, 0.1)
        self.current_offset_y = self.lerp(self.current_offset_y, self.target_offset_y, 0.1)
        self.current_height_scale = self.lerp(self.current_height_scale, self.target_height_scale, 0.07)
        self.current_width_scale = self.lerp(self.current_width_scale, self.target_width_scale, 0.07)
        self.current_corner_radius = self.lerp(self.current_corner_radius, self.target_corner_radius, 0.10)

        self.current_eyelid_tired = self.lerp(self.current_eyelid_tired, self.target_eyelid_tired, 0.10)
        self.current_eyelid_angry = self.lerp(self.current_eyelid_angry, self.target_eyelid_angry, 0.10)
        self.current_eyelid_happy = self.lerp(self.current_eyelid_happy, self.target_eyelid_happy, 0.10)

    def update(self):
        self.update_blink()
        self.update_idle_behavior()
        self.update_attention_target()
        self.update_scrutiny_motion()
        self.update_state_animation()
        self.update_interpolation()

    def get_profile(self):
        return self.profile

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

    def apply_look_deformation(self):
        look_dx = self.target_offset_x - self.profile.offset_x
        look_dy = self.target_offset_y - self.profile.offset_y

        horizontal_amount = min(abs(look_dx) / 25.0, 1.0)
        vertical_amount = min(abs(look_dy) / 20.0, 1.0)

        width_boost = 0.10 * horizontal_amount
        height_squash = 0.12 * horizontal_amount

        vertical_height_adjust = 0.0
        if look_dy < 0:
            vertical_height_adjust = 0.04 * vertical_amount
        elif look_dy > 0:
            vertical_height_adjust = -0.06 * vertical_amount

        self.target_width_scale += width_boost
        self.target_height_scale += vertical_height_adjust
        self.target_height_scale -= height_squash

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