"""Stable presence from flickering detections, under explicit timestamps."""

from __future__ import annotations

import pytest

from mira.domain.perception import (
    BoundingBox,
    HumanPresence,
    PersonDetections,
    PersonObservation,
    PresenceStabilizer,
)


LEFT = (0.1, 0.2, 0.3, 0.6)
RIGHT = (0.6, 0.2, 0.9, 0.7)


class Frames:
    """Detections at explicit times, one per call."""

    def __init__(self):
        self.version = 0

    def at(self, t: float, *boxes) -> PersonDetections:
        self.version += 1
        persons = tuple(
            PersonObservation(0.9, BoundingBox(*box), self.version, t) for box in boxes
        )
        return PersonDetections(self.version, self.version, t, persons)


def feed(stabilizer, frames, timeline):
    """timeline: (time, boxes) pairs; returns presence after each."""
    return [stabilizer.observe(frames.at(t, *boxes)).present for t, boxes in timeline]


def test_nobody_is_present_at_first():
    assert PresenceStabilizer().presence == HumanPresence()


def test_appearance_is_confirmed_after_continuous_detection():
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.3), Frames()

    trace = feed(stabilizer, frames, [(0.0, [LEFT]), (0.1, [LEFT]), (0.2, [LEFT]), (0.3, [LEFT])])

    assert trace == [False, False, False, True]
    presence = stabilizer.presence
    assert presence.changed_at == 0.3 and presence.last_seen_at == 0.3
    assert [seen.bounds for seen in presence.persons] == [BoundingBox(*LEFT)]


def test_a_single_false_detection_does_not_make_anyone_present():
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.3), Frames()

    trace = feed(stabilizer, frames, [(0.0, [LEFT]), (0.1, []), (0.2, []), (0.5, [])])

    assert trace == [False] * 4
    assert stabilizer.presence.persons == ()


def test_a_gap_while_confirming_restarts_confirmation():
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.3), Frames()

    trace = feed(
        stabilizer,
        frames,
        [(0.0, [LEFT]), (0.2, []), (0.3, [LEFT]), (0.5, [LEFT]), (0.6, [LEFT])],
    )

    assert trace == [False, False, False, False, True]


def test_a_missed_detection_while_present_keeps_the_last_sighting():
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.0, release_after_s=1.5), Frames()
    feed(stabilizer, frames, [(0.0, [LEFT])])

    presence = stabilizer.observe(frames.at(0.5))

    assert presence.present
    assert [seen.bounds for seen in presence.persons] == [BoundingBox(*LEFT)]
    assert presence.last_seen_at == 0.0


def test_sustained_disappearance_clears_presence_after_the_grace_period():
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.0, release_after_s=1.5), Frames()

    trace = feed(stabilizer, frames, [(0.0, [LEFT]), (1.0, []), (1.49, []), (1.5, [])])

    assert trace == [True, True, True, False]
    assert stabilizer.presence == HumanPresence(False, (), last_seen_at=0.0, changed_at=1.5)


def test_reappearance_is_confirmed_again():
    # Quarter-second steps keep the elapsed-time arithmetic exact.
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.25, release_after_s=0.5), Frames()

    trace = feed(
        stabilizer,
        frames,
        [(0.0, [LEFT]), (0.25, [LEFT]), (1.0, []), (1.25, [RIGHT]), (1.375, [RIGHT]), (1.5, [RIGHT])],
    )

    assert trace == [False, True, False, False, False, True]
    assert stabilizer.presence.changed_at == 1.5
    assert [seen.bounds for seen in stabilizer.presence.persons] == [BoundingBox(*RIGHT)]


def test_everyone_in_the_latest_sighting_is_kept():
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.0), Frames()

    feed(stabilizer, frames, [(0.0, [LEFT]), (0.1, [LEFT, RIGHT])])

    assert [seen.bounds for seen in stabilizer.presence.persons] == [
        BoundingBox(*LEFT),
        BoundingBox(*RIGHT),
    ]
    assert stabilizer.presence.changed_at == 0.0


def test_presence_lapses_when_no_frames_arrive_at_all():
    stabilizer, frames = PresenceStabilizer(confirm_after_s=0.0, release_after_s=1.0), Frames()
    feed(stabilizer, frames, [(10.0, [LEFT])])

    assert stabilizer.expire(10.9).present
    assert not stabilizer.expire(11.0).present
    assert not stabilizer.expire(50.0).present
    assert stabilizer.presence.changed_at == 11.0


def test_identical_timelines_give_identical_presence():
    timeline = [(t * 0.1, [LEFT] if t % 7 else []) for t in range(60)]

    def run():
        stabilizer, frames = PresenceStabilizer(), Frames()
        feed(stabilizer, frames, timeline)
        return stabilizer.presence

    assert run() == run()


@pytest.mark.parametrize(
    "options",
    [{"confirm_after_s": -0.1}, {"release_after_s": float("nan")}, {"release_after_s": float("inf")}],
)
def test_invalid_timings_are_rejected(options):
    with pytest.raises(ValueError):
        PresenceStabilizer(**options)
