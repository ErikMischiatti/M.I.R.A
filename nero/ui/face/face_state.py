from enum import Enum, auto


class FaceState(Enum):
    IDLE = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()
    HAPPY = auto()
    TIRED = auto()
    ANGRY = auto()
    CONFUSED = auto()