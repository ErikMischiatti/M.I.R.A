from dataclasses import asdict, dataclass


@dataclass
class ExpressionProfile:
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

    @classmethod
    def from_dict(cls, data: dict) -> "ExpressionProfile":
        return cls(**data)