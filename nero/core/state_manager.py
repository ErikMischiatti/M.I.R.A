from nero.ui.face.face_state import FaceState
from nero.core.events import EventBus


class StateManager:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.current_state = FaceState.IDLE

    def set_state(self, new_state: FaceState) -> None:
        if new_state == self.current_state:
            return

        previous_state = self.current_state
        self.current_state = new_state

        self.event_bus.emit(
            "state_changed",
            {
                "previous_state": previous_state,
                "new_state": new_state,
            },
        )

    def get_state(self) -> FaceState:
        return self.current_state

    def reset(self) -> None:
        self.set_state(FaceState.IDLE)