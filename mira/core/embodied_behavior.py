from __future__ import annotations

from mira.messaging.events import EventBus
from mira.domain.scheduler import Scheduler, TimerHandle
from mira.domain.models import BrainResponse, IntentResult
from mira.core.activity_authority import ActivityAuthority
from mira.domain.embodiment import ActivityState, AffectState, EmbodimentIntent


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
        activity: ActivityAuthority,
        *,
        scheduler: Scheduler,
    ):
        self.event_bus = event_bus
        self.activity = activity
        self.scheduler = scheduler

        self._decay_handle: TimerHandle | None = None

        self.last_response_intent: EmbodimentIntent | None = None
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
            self.activity.express(AffectState.CONFUSED)

        elif intent.intent == "greeting":
            self.activity.express(AffectState.HAPPY)

    def on_response_ready(self, response: BrainResponse) -> None:
        """
        When a response becomes ready, keep the expressive state visible
        for a short period before decaying to a neutral state.
        """
        self.last_response_intent = response.embodiment

        delay_ms = self._get_decay_delay(response.embodiment)
        self.decay_active = True

        if self._decay_handle is not None:
            self._decay_handle.cancel()

        self._decay_handle = self.scheduler.call_later(delay_ms, self._decay_to_neutral)

    def _get_decay_delay(self, intent: EmbodimentIntent) -> int:
        if intent.affect is AffectState.HAPPY:
            return 2200
        if intent.affect is AffectState.CONFUSED:
            return 1600
        if intent.activity is ActivityState.SPEAKING:
            return 1800
        if intent.activity is ActivityState.THINKING:
            return 1200
        return 1500

    def _decay_to_neutral(self) -> None:
        self.decay_active = False

        # Decay only if we are still in the expressive state that was being held.
        if (
            self.last_response_intent is not None
            and not self.activity.is_presenting(self.last_response_intent)
        ):
            return

        self.activity.settle(engaged=self._should_return_to_listening())

    def on_input_focused(self, payload=None) -> None:
        self.input_has_focus = True

    def on_input_unfocused(self, payload=None) -> None:
        self.input_has_focus = False

    def on_input_text_changed(self, text) -> None:
        text = text or ""
        self.input_has_text = bool(text.strip())

    def _should_return_to_listening(self) -> bool:
        return self.input_has_focus or self.input_has_text
