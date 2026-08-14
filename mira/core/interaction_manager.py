from mira.messaging.events import EventBus
from mira.core.activity_authority import ActivityAuthority
from mira.domain.models import BrainResponse


class InteractionManager:
    """
    Central coordinator for interaction-related events.

    Responsibilities:
    - reacts to UI input events
    - coordinates face state transitions
    - keeps MainWindow free from interaction logic
    - prepares the system for future multimodal arbitration
      (audio, webcam, tools, etc.)
    """

    def __init__(self, event_bus: EventBus, activity: ActivityAuthority):
        self.event_bus = event_bus
        self.activity = activity

        self.input_has_focus = False
        self.input_has_text = False
        self.is_processing = False

        self._register_subscribers()

    def _register_subscribers(self) -> None:
        self.event_bus.subscribe("input_focused", self.on_input_focused)
        self.event_bus.subscribe("input_unfocused", self.on_input_unfocused)
        self.event_bus.subscribe("input_text_changed", self.on_input_text_changed)

        self.event_bus.subscribe("user_input_received", self.on_user_input_received)
        self.event_bus.subscribe("processing_started", self.on_processing_started)
        self.event_bus.subscribe("response_ready", self.on_response_ready)

    def on_input_focused(self, payload=None) -> None:
        self.input_has_focus = True

        if self.is_processing:
            return

        self.activity.attend()

    def on_input_unfocused(self, payload=None) -> None:
        self.input_has_focus = False

        if self.is_processing:
            return

        self.activity.settle(engaged=self.input_has_text)

    def on_input_text_changed(self, text) -> None:
        text = text or ""
        self.input_has_text = bool(text.strip())

        if self.is_processing:
            return

        self.activity.settle(engaged=self.input_has_text or self.input_has_focus)

    def on_user_input_received(self, payload=None) -> None:
        # Interaction starts: user is actively engaging with the system.
        if self.is_processing:
            return

        self.activity.attend()

    def on_processing_started(self, payload=None) -> None:
        self.is_processing = True
        self.activity.deliberate()

    def on_response_ready(self, response) -> None:
        self.is_processing = False

        if isinstance(response, BrainResponse):
            self.activity.conclude(response.face_state)
            return

        # Fallback safety path
        self._restore_idle_or_listening()

    def restore_post_response_state(self) -> None:
        """
        Call this after the speaking/response phase has visually completed
        and the UI wants to settle back into a passive state.
        """
        if self.is_processing:
            return

        self._restore_idle_or_listening()

    def _restore_idle_or_listening(self) -> None:
        self.activity.settle(engaged=self.input_has_focus or self.input_has_text)