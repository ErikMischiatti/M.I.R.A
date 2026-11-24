"""Nonverbal voice: decide when MIRA babbles, and keep it civil.

`VoiceController` sits between whoever wants a vocalization and the
`AudioOutput` that plays it. It owns the policy, none of the sound:

- **Opt-in.** A disabled controller, or one with no output, accepts nothing and
  subscribes to nothing. That is the default, so a build without
  `MIRA_VOICE=1` behaves exactly as before the voice existed.
- **One voice at a time.** Clips never overlap. While one is rendering or
  sounding, at most ``max_queue`` further requests wait; beyond that, new
  requests are dropped, as is a request whose intent is already waiting.
- **Cooldown.** Requests closer together than ``cooldown_ms`` are dropped, so a
  burst of events produces one reaction, not a stutter.
- **Failure is silence.** A render error, a missing device or a backend that
  refuses a clip is logged and skipped; the rest of MIRA never sees it.

Threading follows the turn lifecycle (`mira/domain/scheduler.py`). Every public
method runs on the serialized context. Rendering — the only heavy part — goes
through `Scheduler.submit`, whose work is a pure function of an immutable
request and identity and touches nothing here. Playback starts on the
serialized context, and its end is tracked with `Scheduler.call_later` from
the clip's known duration, so the controller needs no callback from the audio
backend's thread.

The Brain does not vocalize. `attach` subscribes this controller to the
existing `response_ready` and `action_failed` events, so it observes a turn the
same way `EmbodiedBehavior` does, and either can exist without the other.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from mira.domain.embodiment import AffectState
from mira.domain.models import BrainResponse
from mira.domain.scheduler import Scheduler, TimerHandle
from mira.domain.vocal_synthesis import synthesize as default_synthesize
from mira.domain.vocalization import (
    DEFAULT_INTENSITY,
    AudioOutput,
    PcmClip,
    VocalIntent,
    VocalizationRequest,
    VoiceIdentity,
)
from mira.messaging.events import EventBus

Synthesizer = Callable[[VocalizationRequest, VoiceIdentity], PcmClip]

STARTUP_GREETING_INTENSITY = 0.7


def vocal_intent_for_response(
    response: BrainResponse,
    *,
    action_failed: bool = False,
) -> VocalIntent:
    """Map a committed response onto the intent its babble should carry.

    An explicit allowlist, in precedence order: a failed action is an error
    whatever else the response says; a greeting is answered in kind; otherwise
    the response's own affect decides, and a plain reply is acknowledged.
    """
    if action_failed:
        return VocalIntent.ERROR
    if response.metadata.get("intent") == "greeting":
        return VocalIntent.GREETING
    if response.embodiment.affect is AffectState.CONFUSED:
        return VocalIntent.CONFUSED
    if response.embodiment.affect is AffectState.HAPPY:
        return VocalIntent.HAPPY
    return VocalIntent.ACKNOWLEDGEMENT


@dataclass(frozen=True, slots=True)
class _Rendered:
    clip: PcmClip | None
    error: str = ""


class VoiceController:
    """Serialize, rate-limit and play vocalizations on one `AudioOutput`."""

    def __init__(
        self,
        scheduler: Scheduler,
        output: AudioOutput | None,
        *,
        enabled: bool = False,
        identity: VoiceIdentity = VoiceIdentity(),
        cooldown_ms: int = 400,
        max_queue: int = 1,
        gap_ms: int = 60,
        clock: Callable[[], float] = time.monotonic,
        synthesizer: Synthesizer = default_synthesize,
    ) -> None:
        self._scheduler = scheduler
        self._output = output
        self._enabled = enabled and output is not None
        self._identity = identity.bounded()
        self._cooldown_s = max(0, cooldown_ms) / 1000.0
        self._max_queue = max(0, max_queue)
        self._gap_ms = max(0, gap_ms)
        self._clock = clock
        self._synthesizer = synthesizer

        self._queue: deque[VocalizationRequest] = deque()
        self._busy = False
        self._last_accepted_s: float | None = None
        self._playback: TimerHandle | None = None
        self._shut_down = False
        self._output_unavailable_reported = False
        self._action_failed_this_turn = False

        self.played = 0
        self.dropped = 0

    # --- state --------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._shut_down

    @property
    def identity(self) -> VoiceIdentity:
        return self._identity

    @property
    def busy(self) -> bool:
        """True while a clip is rendering or sounding."""
        return self._busy

    @property
    def queued(self) -> int:
        return len(self._queue)

    # --- requests -----------------------------------------------------------

    def vocalize(
        self,
        intent: VocalIntent,
        intensity: float = DEFAULT_INTENSITY,
        seed: int | None = None,
    ) -> bool:
        """Ask for one vocalization. True if it was accepted, not that it played."""
        if not self.enabled:
            return False
        request = VocalizationRequest(intent, min(1.0, max(0.0, intensity)), seed)

        now = self._clock()
        if self._last_accepted_s is not None and now - self._last_accepted_s < self._cooldown_s:
            self.dropped += 1
            return False

        if self._busy:
            if len(self._queue) >= self._max_queue or any(q.intent is intent for q in self._queue):
                self.dropped += 1
                return False
            self._queue.append(request)
        else:
            self._queue.append(request)
            self._start_next()

        self._last_accepted_s = now
        return True

    def start(self) -> None:
        """Application start: say hello, if the voice is on."""
        self.vocalize(VocalIntent.GREETING, STARTUP_GREETING_INTENSITY)

    def shutdown(self) -> None:
        """Drop pending work, stop playback and release the output; idempotent."""
        if self._shut_down:
            return
        self._shut_down = True
        self._queue.clear()
        if self._playback is not None:
            self._playback.cancel()
            self._playback = None
        self._busy = False
        if self._output is not None:
            try:
                self._output.close()
            except Exception as error:  # noqa: BLE001 - shutdown must finish
                print(f"[Voice] error while closing output: {error!r}")

    # --- event wiring -------------------------------------------------------

    def attach(self, event_bus: EventBus) -> None:
        """Observe turns on ``event_bus``. Composition calls this only when enabled."""
        event_bus.subscribe("action_failed", self.on_action_failed)
        event_bus.subscribe("response_ready", self.on_response_ready)

    def on_action_failed(self, payload=None) -> None:
        # `ActionExecutor` emits this before the turn's `response_ready`.
        self._action_failed_this_turn = True

    def on_response_ready(self, response) -> None:
        failed, self._action_failed_this_turn = self._action_failed_this_turn, False
        if not isinstance(response, BrainResponse):
            return
        self.vocalize(vocal_intent_for_response(response, action_failed=failed))

    # --- pipeline -----------------------------------------------------------

    def _start_next(self) -> None:
        if self._shut_down or not self._queue:
            self._busy = False
            return
        self._busy = True
        request = self._queue.popleft()
        identity = self._identity
        synthesizer = self._synthesizer
        self._scheduler.submit(
            lambda: _render(synthesizer, request, identity),
            self._on_rendered,
        )

    def _on_rendered(self, rendered: _Rendered) -> None:
        if self._shut_down:
            return
        if rendered.clip is None:
            print(f"[Voice] synthesis failed: {rendered.error}")
            self._start_next()
            return

        output = self._output
        if output is None or not output.available:
            if not self._output_unavailable_reported:
                print("[Voice] no audio output available; vocalizations are skipped")
                self._output_unavailable_reported = True
            self._queue.clear()
            self._busy = False
            return

        try:
            output.play(rendered.clip)
        except Exception as error:  # noqa: BLE001 - a backend failure is silence
            print(f"[Voice] playback failed: {error!r}")
            self._start_next()
            return

        self.played += 1
        self._playback = self._scheduler.call_later(
            rendered.clip.duration_ms + self._gap_ms, self._on_playback_finished
        )

    def _on_playback_finished(self) -> None:
        self._playback = None
        self._start_next()


def _render(synthesizer: Synthesizer, request: VocalizationRequest, identity: VoiceIdentity) -> _Rendered:
    """Worker-side: pure, and never raises (the `Scheduler.submit` contract)."""
    try:
        return _Rendered(synthesizer(request, identity))
    except Exception as error:  # noqa: BLE001 - converted into a result
        return _Rendered(None, repr(error))
