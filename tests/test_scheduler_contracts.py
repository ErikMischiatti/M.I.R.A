"""Characterization tests for timing contracts held nowhere else.

Written and run green against the pre-port Qt implementation. It no longer runs
on the pre-port tree: the construction helpers take a `scheduler=` argument, and
the brain factory now comes from `tests/doubles.py`, which imports
`ManualScheduler`.

It covers only the two contracts no other file pins:

- request-id monotonicity, and that allocating a new id invalidates in-flight ones;
- the expression decay delay table and its three re-entry guards.

The staleness gates, interpretation-phase purity and commit ordering are pinned
directly by `test_brain_async_contract.py`, and again more strongly through the
production path by `test_scheduler.py`. They were duplicated here and removed: a
third copy carried no independent signal, since one mutation defeating a gate
fails every copy at once.
"""

from __future__ import annotations

from doubles import RecordingActivityAuthority, RecordingEventBus, make_recording_brain

from mira.core.embodied_behavior import EmbodiedBehavior
from mira.domain.embodiment import ActivityState, AffectState, EmbodimentIntent
from mira.domain.models import IntentResult
from mira.domain.scheduler import ManualScheduler
from mira.domain.state import FaceState


def make_embodied_behavior() -> EmbodiedBehavior:
    return EmbodiedBehavior(
        RecordingEventBus(), RecordingActivityAuthority(), scheduler=ManualScheduler()
    )


# --- request identity ---------------------------------------------------


def test_request_ids_are_monotonic_and_allocation_claims_latest():
    brain = make_recording_brain(IntentResult(intent="time_query"))
    first = brain._new_request_id()
    second = brain._new_request_id()

    assert second > first
    assert brain._latest_request_id == second


# --- decay behaviour ----------------------------------------------------


def test_decay_delay_table_is_unchanged():
    behavior = make_embodied_behavior()
    assert behavior._get_decay_delay(
        EmbodimentIntent(ActivityState.SPEAKING, AffectState.HAPPY)
    ) == 2200
    assert behavior._get_decay_delay(
        EmbodimentIntent(ActivityState.SPEAKING, AffectState.CONFUSED)
    ) == 1600
    assert behavior._get_decay_delay(EmbodimentIntent(ActivityState.SPEAKING)) == 1800
    assert behavior._get_decay_delay(EmbodimentIntent(ActivityState.THINKING)) == 1200
    assert behavior._get_decay_delay(EmbodimentIntent(ActivityState.IDLE)) == 1500


def test_decay_only_fires_while_still_in_the_held_state():
    behavior = make_embodied_behavior()
    behavior.last_response_intent = EmbodimentIntent(
        ActivityState.SPEAKING, AffectState.HAPPY
    )
    behavior.activity.current_state = FaceState.THINKING

    behavior._decay_to_neutral()

    assert behavior.activity.states == []
    assert behavior.decay_active is False


def test_decay_returns_to_listening_when_input_is_engaged():
    behavior = make_embodied_behavior()
    behavior.last_response_intent = EmbodimentIntent(ActivityState.SPEAKING)
    behavior.activity.current_state = FaceState.SPEAKING
    behavior.input_has_focus = True

    behavior._decay_to_neutral()

    assert behavior.activity.states == [FaceState.LISTENING]


def test_decay_returns_to_idle_when_input_is_not_engaged():
    behavior = make_embodied_behavior()
    behavior.last_response_intent = EmbodimentIntent(ActivityState.SPEAKING)
    behavior.activity.current_state = FaceState.SPEAKING
    behavior.input_has_focus = False
    behavior.input_has_text = False

    behavior._decay_to_neutral()

    assert behavior.activity.states == [FaceState.IDLE]
