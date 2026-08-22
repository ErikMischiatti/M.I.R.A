from dataclasses import asdict, dataclass

from mira.domain.embodiment_frame import ExpressionDefinition


@dataclass
class ExpressionProfile:
    """Mutable editor profile: pose definition plus existing motion settings."""

    name: str
    width_scale: float = 1.0
    height_scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    corner_radius: float = 28.0
    idle_enabled: bool = False
    idle_amplitude_x: float = 18.0
    idle_amplitude_y: float = 10.0
    blink_enabled: bool = True
    blink_min_interval_frames: int = 70
    blink_max_interval_frames: int = 170
    blink_duration_frames: int = 5
    speaking_pulse: bool = False
    thinking_drift: bool = False
    asymmetry_offset_y_left: float = 0.0
    asymmetry_offset_y_right: float = 0.0
    asymmetry_height_left: float = 1.0
    asymmetry_height_right: float = 1.0
    eyelid_tired: float = 0.0
    eyelid_angry: float = 0.0
    eyelid_happy: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_definition(self) -> ExpressionDefinition:
        return ExpressionDefinition(
            name=self.name,
            width_scale=self.width_scale,
            height_scale=self.height_scale,
            offset_x=self.offset_x,
            offset_y=self.offset_y,
            corner_radius=self.corner_radius,
            asymmetry_offset_y_left=self.asymmetry_offset_y_left,
            asymmetry_offset_y_right=self.asymmetry_offset_y_right,
            asymmetry_height_left=self.asymmetry_height_left,
            asymmetry_height_right=self.asymmetry_height_right,
            eyelid_tired=self.eyelid_tired,
            eyelid_angry=self.eyelid_angry,
            eyelid_happy=self.eyelid_happy,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "ExpressionProfile":
        return cls(**data)
