from __future__ import annotations

from mira.core.events import EventBus
from mira.domain.scheduler import Scheduler, TimerHandle
from mira.domain.models import BrainResponse, IntentResult
from mira.core.state_manager import StateManager
from mira.domain.state import FaceState


class EmbodiedBehavior:
    """
    Lightweight embodied behavior layer.

    Responsibilities:
    - keep expressive face states visible for a short time
    - add simple reaction behaviors based on inferred intent
    - decay expressive states back to neutral states over time

    This layer does not decide what N.E.R.O says.
    It only shapes how the current behavior remains visible.
    """

    def __init__(
        self,
        event_bus: EventBus,
        state_manager: StateManager,
        *,
        scheduler: Scheduler,
    ):
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.scheduler = scheduler

        self._decay_handle: TimerHandle | None = None

        self.last_response_state: FaceState | None = None
        self.decay_active = False
        self.input_has_focus = False
        self.input_has_text = False

        self._register_subscribers()

    def _register_subscribers(self) -> None:
        self.event_bus.subscribe("intent_inferred", self.on_intent_inferred)
        self.event_bus.subscribe("response_ready", self.on_response_ready)

        self.event_bus.subscribe("input_focused", self.on_input_focused)
        self.event_bus.subscribe("input_unfocused", self.on_input_unfocused)
        self.event_bus.subscribe("input_text_changed", self.on_input_text_changed)

        

    def on_intent_inferred(self, intent: IntentResult) -> None:
        """
        Optional pre-response micro-reaction.
        Keep this subtle and short.
        """
        if intent.intent == "unknown":
            self.state_manager.set_state(FaceState.CONFUSED)

        elif intent.intent == "greeting":
            self.state_manager.set_state(FaceState.HAPPY)

    def on_response_ready(self, response: BrainResponse) -> None:
        """
        When a response becomes ready, keep the expressive state visible
        for a short period before decaying to a neutral state.
        """
        self.last_response_state = response.face_state

        delay_ms = self._get_decay_delay(response.face_state)
        self.decay_active = True

        if self._decay_handle is not None:
            self._decay_handle.cancel()

        self._decay_handle = self.scheduler.call_later(delay_ms, self._decay_to_neutral)

    def _get_decay_delay(self, state: FaceState) -> int:
        if state == FaceState.HAPPY:
            return 2200
        if state == FaceState.CONFUSED:
            return 1600
        if state == FaceState.SPEAKING:
            return 1800
        if state == FaceState.THINKING:
            return 1200
        return 1500

    def _decay_to_neutral(self) -> None:
        self.decay_active = False

        current_state = self.state_manager.get_state()

        # Decay only if we are still in the expressive state that was being held.
        if self.last_response_state is not None and current_state != self.last_response_state:
            return

        if self._should_return_to_listening():
            self.state_manager.set_state(FaceState.LISTENING)
        else:
            self.state_manager.set_state(FaceState.IDLE)

    def on_input_focused(self, payload=None) -> None:
        self.input_has_focus = True

    def on_input_unfocused(self, payload=None) -> None:
        self.input_has_focus = False

    def on_input_text_changed(self, text) -> None:
        text = text or ""
        self.input_has_text = bool(text.strip())

    def _should_return_to_listening(self) -> bool:
        return self.input_has_focus or self.input_has_text