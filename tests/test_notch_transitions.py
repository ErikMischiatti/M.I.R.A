"""Animated notch transitions, driven deterministically.

Every test seeks the window's one transition animation to an explicit time
instead of waiting: a paused `QVariantAnimation` given `setCurrentTime` emits
the value for that instant, and seeking to its duration finishes it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPoint
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication

from mira.ui.notch.notch_window import RETRACTED_MASK, NotchWindow
from mira.ui.notch.presence import NotchGeometry, NotchPresence, NotchSize


GEOMETRY = NotchGeometry(
    compact=NotchSize(width=200.0, height=32.0, corner_radius=14.0),
    expanded=NotchSize(width=360.0, height=96.0, corner_radius=24.0),
)
DURATION = NotchWindow.TRANSITION_DURATION_MS
SAMPLES = (0, 20, 50, 100, 150, 199, DURATION)

H, C, E = NotchPresence.HIDDEN, NotchPresence.COMPACT, NotchPresence.EXPANDED


@pytest.fixture(scope="module", autouse=True)
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window():
    notch = NotchWindow(GEOMETRY)
    yield notch
    notch.transition.stop()
    notch.hide()
    notch.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def at_rest(window: NotchWindow, presence: NotchPresence) -> None:
    window.set_presence(presence, animated=False)


def seek(window: NotchWindow, ms: int) -> None:
    transition = window.transition
    if transition.state() is QAbstractAnimation.State.Running:
        transition.pause()
    transition.setCurrentTime(ms)


def finish(window: NotchWindow) -> None:
    seek(window, window.transition.duration())


def window_geometry(window: NotchWindow) -> tuple:
    return (window.x(), window.y(), window.width(), window.height())


def between(value: float, a: float, b: float) -> bool:
    return min(a, b) - 1e-9 <= value <= max(a, b) + 1e-9


# --- Normal transitions ------------------------------------------------------


@pytest.mark.parametrize("start,end", [(H, C), (C, E), (E, C), (C, H), (E, H)])
def test_a_transition_blends_monotonically_from_start_to_target(window, start, end):
    at_rest(window, start)
    begin, target = GEOMETRY.size_for(start), GEOMETRY.size_for(end)
    fixed = window_geometry(window)

    window.set_presence(end)

    assert window.presence is end
    assert window.transition.duration() == DURATION
    sizes = []
    for ms in SAMPLES:
        seek(window, ms)
        size = window.visual_size
        sizes.append(size)
        assert between(size.width, begin.width, target.width)
        assert between(size.height, begin.height, target.height)
        assert window_geometry(window) == fixed
        bounds = window.shape.bounds
        assert bounds.center_x == window.width() / 2.0 and bounds.y == 0.0

    assert sizes[0] == begin
    assert sizes[-1] == target
    for earlier, later in zip(sizes, sizes[1:]):
        assert abs(later.height - target.height) <= abs(earlier.height - target.height)
        assert abs(later.width - target.width) <= abs(earlier.width - target.width)


def test_the_easing_is_out_cubic(window):
    at_rest(window, C)
    window.expand_notch()

    seek(window, DURATION // 2)

    # OutCubic covers 87.5% of the path by half time.
    assert window.visual_size.height == pytest.approx(32.0 + 64.0 * 0.875)


@pytest.mark.parametrize("start,end", [(H, C), (C, E), (E, C), (C, H)])
def test_the_window_never_moves_or_resizes_during_a_transition(window, start, end):
    at_rest(window, start)
    resizes = []
    window.resizeEvent = lambda event: resizes.append(event.size())
    fixed = window_geometry(window)

    window.set_presence(end)
    for ms in range(0, DURATION + 1, 10):
        seek(window, ms)
        QCoreApplication.processEvents()
        assert window_geometry(window) == fixed

    # Qt delivers the pending resize to a window on its first show; it carries
    # the same fixed size. No event may carry any other.
    assert all((size.width(), size.height()) == fixed[2:] for size in resizes)


# --- Window lifecycle --------------------------------------------------------


def test_opening_shows_the_window_before_the_notch_grows(window):
    window.show_notch()

    assert window.isVisible()
    assert window.transition.state() is QAbstractAnimation.State.Running
    assert window.visual_size.height == 0.0


def test_closing_keeps_the_window_shown_until_the_notch_has_retracted(window):
    at_rest(window, C)
    window.hide_notch()

    for ms in SAMPLES[:-1]:
        seek(window, ms)
        assert window.isVisible(), f"hidden early at {ms} ms"

    finish(window)

    assert window.visual_size.height == 0.0
    assert not window.isVisible()


def test_closing_from_expanded_hides_only_at_the_end(window):
    at_rest(window, E)
    window.hide_notch()
    seek(window, 199)
    assert window.isVisible()

    finish(window)

    assert not window.isVisible()


def test_an_instant_transition_settles_at_once(window):
    window.set_presence(E, animated=False)
    assert window.isVisible() and window.visual_size == GEOMETRY.expanded
    assert window.transition.state() is QAbstractAnimation.State.Stopped

    window.set_presence(H, animated=False)
    assert not window.isVisible() and window.visual_size.height == 0.0


# --- API semantics -----------------------------------------------------------


def test_expand_and_collapse_target_expanded_and_compact(window):
    at_rest(window, C)

    window.expand_notch()
    assert window.presence is E
    finish(window)

    window.collapse_notch()
    assert window.presence is C
    finish(window)
    assert window.visual_size == GEOMETRY.compact


def test_repeating_the_current_target_does_not_restart_the_transition(window):
    at_rest(window, C)
    window.expand_notch()
    seek(window, 60)
    size = window.visual_size

    window.expand_notch()
    window.set_presence(E)

    assert window.transition.currentTime() == 60
    assert window.visual_size == size


def test_show_while_opening_and_hide_while_hidden_change_nothing(window):
    window.show_notch()
    seek(window, 40)
    window.show_notch()
    assert window.transition.currentTime() == 40

    window.hide_notch()
    finish(window)
    window.hide_notch()
    assert window.transition.state() is QAbstractAnimation.State.Stopped
    assert not window.isVisible()


def test_show_keeps_an_expanded_target(window):
    at_rest(window, E)
    window.show_notch()

    assert window.presence is E
    assert window.transition.state() is QAbstractAnimation.State.Stopped


# --- Interruptions -----------------------------------------------------------


def test_reversing_an_expansion_starts_from_the_current_shape(window):
    at_rest(window, C)
    window.expand_notch()
    seek(window, int(DURATION * 0.4))
    reached = window.visual_size
    shape = window.shape

    window.collapse_notch()

    assert window.visual_size == reached
    assert window.shape == shape
    seek(window, 0)
    assert window.visual_size == reached

    previous = reached
    for ms in range(10, window.transition.duration() + 1, 10):
        seek(window, ms)
        assert window.visual_size.height <= previous.height
        assert window.visual_size.height >= GEOMETRY.compact.height
        previous = window.visual_size
    finish(window)
    assert window.visual_size == GEOMETRY.compact
    assert window.presence is C


def test_reversing_a_close_keeps_the_window_and_grows_back(window):
    at_rest(window, C)
    window.hide_notch()
    seek(window, DURATION // 2)
    reached = window.visual_size

    window.show_notch()

    assert window.presence is C
    assert window.isVisible()
    assert window.visual_size == reached
    finish(window)
    assert window.visual_size == GEOMETRY.compact
    assert window.isVisible()


def test_a_reversal_takes_only_the_share_of_the_duration_it_travels_back(window):
    at_rest(window, C)
    window.expand_notch()
    seek(window, DURATION // 2)
    travelled = (window.visual_size.height - 32.0) / 64.0

    window.collapse_notch()

    assert window.transition.duration() == pytest.approx(DURATION * travelled, abs=1)
    assert window.transition.duration() < DURATION


def test_a_transition_from_rest_takes_the_full_duration(window):
    at_rest(window, E)
    window.collapse_notch()

    assert window.transition.duration() == DURATION


def test_a_redirect_to_a_third_presence_scales_by_its_remaining_path(window):
    at_rest(window, C)
    window.expand_notch()
    seek(window, DURATION // 2)
    reached = window.visual_size

    window.hide_notch()

    assert window.visual_size == reached
    assert 0 < window.transition.duration() <= DURATION
    finish(window)
    assert not window.isVisible()


@pytest.mark.parametrize(
    "sequence,final",
    [
        ((NotchWindow.show_notch, NotchWindow.hide_notch, NotchWindow.show_notch), C),
        ((NotchWindow.expand_notch, NotchWindow.collapse_notch, NotchWindow.expand_notch), E),
        ((NotchWindow.hide_notch, NotchWindow.show_notch, NotchWindow.hide_notch), H),
    ],
)
def test_rapid_sequences_never_jump_and_end_on_the_last_target(window, sequence, final):
    if sequence[0] is not NotchWindow.show_notch:
        at_rest(window, C)
    for request in sequence:
        before = window.visual_size
        request(window)
        assert window.visual_size == before, "the shape jumped when retargeted"
        seek(window, min(30, window.transition.duration()))

    finish(window)

    assert window.presence is final
    assert window.visual_size == GEOMETRY.size_for(final)
    assert window.isVisible() is final.is_visible


def record_applied_sizes(window: NotchWindow) -> list:
    """Every visual size the window applies, including transient ones."""
    applied = []
    window.transition.valueChanged.connect(lambda _value: applied.append(window.visual_size))
    return applied


@pytest.mark.parametrize(
    "start,first,second",
    [(C, E, C), (C, H, C), (E, H, E), (H, C, H), (C, E, H)],
)
def test_retargeting_never_applies_a_transient_size(window, start, first, second):
    # Restarting the one animation with a new duration must not apply, even
    # for an instant, a size computed from the clock the last run left behind.
    at_rest(window, start)
    window.set_presence(first)
    seek(window, 60)
    applied = record_applied_sizes(window)

    reached = window.visual_size
    window.set_presence(second)
    assert all(size == reached for size in applied), "transient size on reversal"

    finish(window)
    applied.clear()
    settled = window.visual_size
    window.set_presence(first)
    assert all(size == settled for size in applied), "transient size after a finished run"


# --- Stale completion ----------------------------------------------------------


def test_an_interrupted_close_never_hides_the_window_later(window):
    at_rest(window, C)
    window.hide_notch()
    seek(window, 120)
    window.show_notch()

    finish(window)
    QCoreApplication.processEvents()

    assert window.isVisible()
    assert window.presence is C


def test_a_late_finished_signal_obeys_the_current_target(window):
    at_rest(window, C)
    window.hide_notch()
    seek(window, 120)
    window.expand_notch()

    # As if the interrupted close delivered its completion after all.
    window.transition.finished.emit()

    assert window.isVisible()
    assert window.presence is E


# --- Mask ----------------------------------------------------------------------


def mask_matches_shape(window: NotchWindow) -> bool:
    bounds = window.shape.bounds
    box = window.mask().boundingRect()
    reach = 2
    return (
        abs(box.x() - bounds.x) <= reach
        and abs(box.right() + 1 - bounds.right) <= reach
        and abs(box.bottom() + 1 - bounds.bottom) <= reach
    )


def test_the_mask_follows_the_shape_through_an_expansion(window):
    at_rest(window, C)
    window.expand_notch()

    for ms in (0, 30, 100, DURATION):
        seek(window, ms)
        assert mask_matches_shape(window), f"mask lags the shape at {ms} ms"


def test_opening_starts_from_a_one_pixel_mask(window):
    window.show_notch()

    assert window.mask() == QRegion(RETRACTED_MASK)


def test_a_nearly_retracted_notch_keeps_only_a_one_pixel_mask(window):
    at_rest(window, C)
    window.hide_notch()
    seek(window, 150)
    height = window.visual_size.height
    assert 0.0 < height < 1.0

    assert window.isVisible()
    assert window.mask() == QRegion(RETRACTED_MASK)
    assert not window.mask().contains(QPoint(int(window.width() / 2), 0))


def test_the_mask_is_back_on_the_shape_once_it_is_tall_enough(window):
    window.show_notch()
    seek(window, 20)

    assert window.visual_size.height >= 1.0
    assert mask_matches_shape(window)
