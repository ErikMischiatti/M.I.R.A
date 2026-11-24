"""The countdown timer in the notch: a Timer control, an inline panel, a readout.

The presenter half is Qt-free and driven by a `ManualScheduler`. The desktop
half runs the real `DesktopShell` offscreen on an application graph whose
timer reads a fake monotonic clock, so a countdown is advanced explicitly
and the clicks go through the notch exactly as a pointer's would.
"""

from __future__ import annotations

import pytest

from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.domain.timer import TimerSnapshot, TimerState
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import (
    CONTROLS_LINGER_MS,
    DEFAULT_TIMER_SELECTION_S,
    MAX_TIMER_SELECTION_S,
    MIN_TIMER_SELECTION_S,
    TIMER_PRESET_MINUTES,
    ExpansionReason,
    NotchAction,
    NotchBehaviour,
    NotchPresenter,
    NotchSymbol,
    TimerButton,
    TimerCommand,
    adjusted_selection,
    timer_buttons,
)

ALL_ACTIONS = frozenset(NotchAction)
DONE_PANEL = (TimerButton(TimerCommand.ACKNOWLEDGE),)
SETUP = timer_buttons(TimerSnapshot())
RUNNING = TimerSnapshot(TimerState.RUNNING, 600, 600.0)
PAUSED = TimerSnapshot(TimerState.PAUSED, 600, 312.0)
FINISHED = TimerSnapshot(TimerState.FINISHED, 600, 0.0)


def presenter_for(actions=ALL_ACTIONS) -> tuple[NotchPresenter, ManualScheduler]:
    scheduler = ManualScheduler()
    presenter = NotchPresenter(scheduler, actions=actions)
    presenter.show()
    return presenter, scheduler


def preset(minutes: int) -> TimerButton:
    return TimerButton(TimerCommand.PRESET, minutes)


# --- Presenter: the panel ------------------------------------------------------------


def test_the_timer_is_the_first_integrated_control():
    presenter, _ = presenter_for()

    presenter.toggle_controls()

    assert presenter.presentation.controls == (NotchAction.TIMER, NotchAction.OPEN_SETTINGS, NotchAction.QUIT)


def test_opening_the_panel_replaces_the_controls_and_expands():
    presenter, _ = presenter_for()
    presenter.toggle_controls()

    presenter.open_timer_panel()

    presentation = presenter.presentation
    assert presentation.controls == ()
    assert presentation.timer_panel == SETUP
    assert presentation.presence is NotchPresence.EXPANDED
    assert presentation.reasons == {ExpansionReason.TIMER}


def test_a_notch_not_offering_the_timer_never_opens_its_panel():
    presenter, _ = presenter_for(frozenset({NotchAction.QUIT}))

    presenter.open_timer_panel()

    assert presenter.presentation.timer_panel == ()
    assert presenter.presentation.presence is NotchPresence.COMPACT


def test_a_hidden_notch_opens_no_panel():
    presenter, _ = presenter_for()
    presenter.hide()

    presenter.open_timer_panel()

    assert presenter.presentation.timer_panel == ()


def test_right_click_closes_the_open_panel():
    presenter, _ = presenter_for()
    presenter.open_timer_panel()

    presenter.toggle_controls()

    assert presenter.presentation.timer_panel == ()
    assert presenter.presentation.controls == ()
    assert presenter.presentation.presence is NotchPresence.COMPACT


def test_the_panel_lingers_after_the_pointer_leaves_and_then_closes():
    presenter, scheduler = presenter_for()
    presenter.set_hovered(True)
    presenter.open_timer_panel()
    presenter.set_hovered(False)

    scheduler.advance(CONTROLS_LINGER_MS - 1)
    assert presenter.presentation.timer_panel == SETUP

    scheduler.advance(1)
    assert presenter.presentation.timer_panel == ()
    assert presenter.presentation.presence is NotchPresence.COMPACT


def test_coming_back_in_time_keeps_the_panel_open():
    presenter, scheduler = presenter_for()
    presenter.set_hovered(True)
    presenter.open_timer_panel()
    presenter.set_hovered(False)
    scheduler.advance(CONTROLS_LINGER_MS // 2)

    presenter.set_hovered(True)
    scheduler.advance(CONTROLS_LINGER_MS * 2)

    assert presenter.presentation.timer_panel == SETUP


def test_hiding_closes_the_panel():
    presenter, _ = presenter_for()
    presenter.open_timer_panel()

    presenter.hide()
    presenter.show()

    assert presenter.presentation.timer_panel == ()


def test_the_panel_offers_what_the_timers_state_allows():
    assert SETUP == (
        TimerButton(TimerCommand.SHORTER),
        TimerButton(TimerCommand.LONGER),
        *(preset(minutes) for minutes in (5, 10, 15, 25)),
        TimerButton(TimerCommand.START),
    )
    assert timer_buttons(FINISHED) == DONE_PANEL
    assert timer_buttons(RUNNING) == (TimerButton(TimerCommand.PAUSE), TimerButton(TimerCommand.RESET))
    assert timer_buttons(PAUSED) == (TimerButton(TimerCommand.RESUME), TimerButton(TimerCommand.RESET))


def test_the_panel_follows_the_timer():
    presenter, _ = presenter_for()
    presenter.open_timer_panel()

    presenter.set_timer(RUNNING)
    assert presenter.presentation.timer_panel == timer_buttons(RUNNING)

    presenter.set_timer(PAUSED)
    assert presenter.presentation.timer_panel == timer_buttons(PAUSED)


# --- Presenter: a finished timer awaiting acknowledgement ---------------------------------------


def test_a_finished_timer_expands_with_its_done_panel():
    presenter, _ = presenter_for()
    presenter.set_timer(RUNNING)

    presenter.set_timer(FINISHED)

    presentation = presenter.presentation
    assert presentation.timer_panel == DONE_PANEL
    assert presentation.presence is NotchPresence.EXPANDED
    assert presentation.reasons == {ExpansionReason.TIMER_DONE}


def test_the_done_panel_outlasts_any_notice_timeout_and_the_pointer_leaving():
    presenter, scheduler = presenter_for()
    presenter.set_hovered(True)
    presenter.open_timer_panel()
    presenter.set_timer(FINISHED)
    presenter.present(Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Keyboard"))

    presenter.set_hovered(False)
    scheduler.advance(NotchBehaviour().notice_duration_ms + CONTROLS_LINGER_MS + 3_600_000)

    assert presenter.presentation.notice is None
    assert presenter.presentation.timer_panel == DONE_PANEL
    assert presenter.presentation.presence is NotchPresence.EXPANDED


def test_right_click_shows_the_controls_over_the_done_panel_and_back():
    presenter, scheduler = presenter_for()
    presenter.set_timer(FINISHED)

    presenter.toggle_controls()
    assert presenter.presentation.controls == (NotchAction.TIMER, NotchAction.OPEN_SETTINGS, NotchAction.QUIT)

    presenter.toggle_controls()
    assert presenter.presentation.controls == ()
    assert presenter.presentation.timer_panel == DONE_PANEL

    presenter.toggle_controls()
    presenter.set_hovered(False)
    scheduler.advance(CONTROLS_LINGER_MS)
    assert presenter.presentation.controls == ()
    assert presenter.presentation.timer_panel == DONE_PANEL


def test_only_the_timer_leaving_finished_releases_the_notch():
    presenter, _ = presenter_for()
    presenter.set_timer(RUNNING)
    presenter.open_timer_panel()
    presenter.set_timer(FINISHED)

    presenter.set_timer(TimerSnapshot(TimerState.IDLE, 600, 600.0))

    # The panel the user had open closed with the finish; nothing holds the notch.
    assert presenter.presentation.timer_panel == ()
    assert presenter.presentation.reasons == frozenset()
    assert presenter.presentation.presence is NotchPresence.COMPACT


def test_a_hidden_notch_stays_hidden_when_the_timer_finishes():
    presenter, _ = presenter_for()
    presenter.hide()

    presenter.set_timer(FINISHED)

    assert presenter.presentation.presence is NotchPresence.HIDDEN


# --- Presenter: duration selection ------------------------------------------------------------


def test_the_presets_are_five_ten_fifteen_and_twenty_five_minutes():
    assert TIMER_PRESET_MINUTES == (5, 10, 15, 25)
    assert DEFAULT_TIMER_SELECTION_S == 5 * 60


@pytest.mark.parametrize("minutes", TIMER_PRESET_MINUTES)
def test_a_preset_selects_its_duration(minutes):
    assert adjusted_selection(DEFAULT_TIMER_SELECTION_S, preset(minutes)) == minutes * 60


@pytest.mark.parametrize(
    ("selected_min", "command", "expected_min"),
    [
        (5, TimerCommand.LONGER, 6),
        (5, TimerCommand.SHORTER, 4),
        (9, TimerCommand.LONGER, 10),
        (10, TimerCommand.LONGER, 15),
        (15, TimerCommand.SHORTER, 10),
        (10, TimerCommand.SHORTER, 9),
        (12, TimerCommand.LONGER, 15),
        (12, TimerCommand.SHORTER, 10),
        (1, TimerCommand.SHORTER, 1),
        (MAX_TIMER_SELECTION_S // 60, TimerCommand.LONGER, MAX_TIMER_SELECTION_S // 60),
    ],
)
def test_shorter_and_longer_step_by_one_minute_then_by_five(selected_min, command, expected_min):
    assert adjusted_selection(selected_min * 60, TimerButton(command)) == expected_min * 60


def test_the_selection_stays_within_bounds():
    assert MIN_TIMER_SELECTION_S == 60
    assert adjusted_selection(MIN_TIMER_SELECTION_S, TimerButton(TimerCommand.SHORTER)) == MIN_TIMER_SELECTION_S
    assert adjusted_selection(MAX_TIMER_SELECTION_S, TimerButton(TimerCommand.LONGER)) == MAX_TIMER_SELECTION_S


def test_choosing_in_the_panel_changes_the_selection():
    presenter, _ = presenter_for()
    presenter.open_timer_panel()

    presenter.choose_timer(preset(15))
    presenter.choose_timer(TimerButton(TimerCommand.LONGER))

    assert presenter.timer_selection_s == 20 * 60
    assert presenter.presentation.timer_selection_s == 20 * 60


def test_commands_are_not_selections():
    presenter, _ = presenter_for()

    presenter.choose_timer(TimerButton(TimerCommand.START))

    assert presenter.timer_selection_s == DEFAULT_TIMER_SELECTION_S


# --- Presenter: a running timer ---------------------------------------------------------------


def test_a_running_timer_never_expands_the_notch():
    presenter, _ = presenter_for()

    presenter.set_timer(RUNNING)

    assert presenter.presentation.presence is NotchPresence.COMPACT
    assert presenter.presentation.reasons == frozenset()


def test_an_open_notch_shows_the_running_countdown():
    presenter, _ = presenter_for()
    presenter.set_hovered(True)

    presenter.set_timer(RUNNING)
    assert presenter.presentation.content.symbol is NotchSymbol.TIMER
    assert presenter.presentation.content.label == "10:00"

    presenter.set_timer(PAUSED)
    assert presenter.presentation.content.label == "05:12 paused"


def test_a_notice_and_listening_come_before_the_countdown_and_it_before_tasks():
    presenter, _ = presenter_for()
    presenter.set_hovered(True)
    presenter.set_timer(RUNNING)
    presenter.set_tasks_running(True)
    assert presenter.presentation.content.symbol is NotchSymbol.TIMER

    presenter.set_listening(True)
    assert presenter.presentation.content.symbol is NotchSymbol.LISTENING
    presenter.set_listening(False)

    presenter.present(Notice(NoticeCategory.DEVICE, NoticeTone.INFO, "Keyboard"))
    assert presenter.presentation.content.label == "Keyboard"


def test_a_finished_or_idle_timer_shows_nothing():
    presenter, _ = presenter_for()
    presenter.set_hovered(True)

    presenter.set_timer(FINISHED)

    assert presenter.presentation.content is None


# --- Embodiment: the eyes hold the completion -------------------------------------------------


DT = 0.03


def eyes(controller):
    frame = controller.get_frame()
    return frame.left_eye.shape, frame.left_eye.shape_amount


def run(controller, seconds: float) -> None:
    for _ in range(round(seconds / DT)):
        controller.update(DT)


@pytest.fixture
def face():
    pytest.importorskip("PySide6")
    from mira.ui.face.face_controller import FaceController

    return FaceController()


def test_the_finished_timer_holds_smiling_eyes_until_released(face):
    from mira.domain.embodiment_cue import (
        CONDITION_BREATH_LOW,
        CONDITION_TRANSITION_S,
        EmbodimentCondition,
        EyeShape,
    )

    face.set_condition(EmbodimentCondition.TIMER_FINISHED, True)
    run(face, CONDITION_TRANSITION_S + DT)
    assert eyes(face)[0] is EyeShape.CHEVRON

    # Far beyond any cue's lifetime: still held, breathing only a little.
    amounts = []
    for _ in range(round(60 / DT)):
        face.update(DT)
        shape, amount = eyes(face)
        assert shape is EyeShape.CHEVRON
        amounts.append(amount)
    low = CONDITION_BREATH_LOW[EmbodimentCondition.TIMER_FINISHED]
    assert low >= 0.9
    assert min(amounts) >= low - 1e-9 and max(amounts) <= 1.0 + 1e-9

    face.set_condition(EmbodimentCondition.TIMER_FINISHED, False)
    run(face, CONDITION_TRANSITION_S + DT)
    assert eyes(face) == (EyeShape.NATURAL, 0.0)


def test_the_finished_timer_wins_over_running_work_and_yields_to_a_cue(face):
    from mira.domain.embodiment_cue import (
        CONDITION_TRANSITION_S,
        CUE_TREATMENTS,
        TRANSITION_S,
        EmbodimentCondition,
        EmbodimentCue,
        EyeShape,
    )

    face.set_condition(EmbodimentCondition.TASK_RUNNING, True)
    face.set_condition(EmbodimentCondition.TIMER_FINISHED, True)
    run(face, 2 * CONDITION_TRANSITION_S + DT)
    assert eyes(face)[0] is EyeShape.CHEVRON

    face.show_cue(EmbodimentCue.ERROR)
    run(face, TRANSITION_S + DT)
    assert eyes(face)[0] is EyeShape.CROSS
    run(face, CUE_TREATMENTS[EmbodimentCue.ERROR].hold_s + TRANSITION_S + DT)
    assert eyes(face)[0] is EyeShape.CHEVRON


# --- Desktop: the notch, the shell and the real timer -----------------------------------------

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mira.application.composition import build_application  # noqa: E402
from mira.ui.desktop_shell import NOTCH_ACTIONS, DesktopShell, Surface  # noqa: E402
from mira.ui.notch.config_store import ConfigSession  # noqa: E402
from mira.ui.notch.notch_window import ACTION_LABELS, NotchWindow  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def qt_application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def desktop(qt_application, tmp_path):
    scheduler = ManualScheduler()
    clock = FakeClock()
    application = build_application(scheduler=scheduler, clock=clock)
    shell = DesktopShell(
        application, initial_surface=Surface.NOTCH, notch_session=ConfigSession(tmp_path / "notch.json"),
        quit=lambda: None,
    )
    shell.start()
    settle(shell.notch)
    shell.application, shell.scheduler, shell.clock = application, scheduler, clock  # for the tests only
    yield shell
    shell.shutdown()
    application.shutdown()
    for obj in (shell.notch, shell.window, shell.animator, shell.settings_window):
        if obj is not None:
            obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def settle(notch: NotchWindow) -> None:
    transition = notch.transition
    if transition.state() is not transition.State.Stopped:
        transition.setCurrentTime(transition.duration())


def elapse(desktop, seconds: float) -> None:
    desktop.clock.now += seconds
    desktop.scheduler.advance(round(seconds * 1000))
    settle(desktop.notch)


def click_at(notch: NotchWindow, rect: QRectF) -> None:
    centre = rect.center()
    QTest.mouseClick(notch, Qt.LeftButton, Qt.NoModifier, QPoint(round(centre.x()), round(centre.y())))
    settle(notch)


def open_panel(notch: NotchWindow) -> None:
    point = QPoint(notch.width() // 2, 6)
    QCoreApplication.sendEvent(notch, QContextMenuEvent(QContextMenuEvent.Mouse, point, notch.mapToGlobal(point)))
    settle(notch)
    click_at(notch, notch.control_rects[NotchAction.TIMER])


def press(notch: NotchWindow, button: TimerButton) -> None:
    click_at(notch, notch.timer_button_rects[button])


def leave(desktop) -> None:
    desktop.presenter.set_hovered(False)
    desktop.scheduler.advance(CONTROLS_LINGER_MS)
    settle(desktop.notch)


def render(notch: NotchWindow) -> QImage:
    image = QImage(notch.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    notch.render(image)
    return image


def lit_in_row(image: QImage, notch: NotchWindow) -> int:
    area = notch.content_row.area
    return sum(
        1
        for y in range(int(area.y), int(area.y + area.height - 2))
        for x in range(int(area.x), int(area.x + area.width))
        if image.pixelColor(x, y).lightness() > 60
    )


def test_the_shell_offers_the_timer_and_labels_it():
    assert NotchAction.TIMER in NOTCH_ACTIONS
    assert ACTION_LABELS[NotchAction.TIMER] == "Timer"


def test_the_timer_control_opens_the_inline_panel(desktop):
    open_panel(desktop.notch)

    notch = desktop.notch
    assert desktop.presenter.presentation.timer_panel == SETUP
    assert notch.presence is NotchPresence.EXPANDED
    assert list(notch.timer_button_rects) == list(SETUP)
    # Inside the content band, readout first, nothing overlapping.
    readout = notch.timer_readout_rect
    rects = [readout, *notch.timer_button_rects.values()]
    area = notch.content_row.area
    band = QRectF(area.x, area.y, area.width, area.height)
    for rect in rects:
        assert band.contains(rect)
    for left, right in zip(rects, rects[1:]):
        assert left.right() <= right.left()
    assert notch.timer_readout == "05:00"


def test_choosing_a_duration_in_the_panel(desktop):
    open_panel(desktop.notch)
    notch = desktop.notch

    press(notch, preset(25))
    assert notch.timer_readout == "25:00"
    press(notch, TimerButton(TimerCommand.SHORTER))
    assert notch.timer_readout == "20:00"
    press(notch, preset(5))
    press(notch, TimerButton(TimerCommand.LONGER))
    assert notch.timer_readout == "06:00"


def test_start_pause_resume_and_reset_from_the_panel(desktop):
    open_panel(desktop.notch)
    notch, timer = desktop.notch, desktop.application.timer

    press(notch, preset(10))
    press(notch, TimerButton(TimerCommand.START))
    assert timer.snapshot == TimerSnapshot(TimerState.RUNNING, 600, 600.0)
    assert list(notch.timer_button_rects) == [TimerButton(TimerCommand.PAUSE), TimerButton(TimerCommand.RESET)]

    elapse(desktop, 61)
    assert notch.timer_readout == "08:59"

    press(notch, TimerButton(TimerCommand.PAUSE))
    elapse(desktop, 100)
    assert timer.snapshot.state is TimerState.PAUSED
    assert notch.timer_readout == "08:59"

    press(notch, TimerButton(TimerCommand.RESUME))
    elapse(desktop, 9)
    assert timer.snapshot.state is TimerState.RUNNING
    assert notch.timer_readout == "08:50"

    press(notch, TimerButton(TimerCommand.RESET))
    assert timer.snapshot.state is TimerState.IDLE
    assert list(notch.timer_button_rects) == list(SETUP)
    assert notch.timer_readout == "10:00"


def test_the_countdown_continues_while_the_notch_is_collapsed(desktop):
    open_panel(desktop.notch)
    press(desktop.notch, TimerButton(TimerCommand.START))

    leave(desktop)
    assert desktop.notch.presence is NotchPresence.COMPACT
    assert desktop.notch.content_opacity == 0.0

    elapse(desktop, 120)
    assert desktop.application.timer.snapshot.remaining_s == pytest.approx(180.0)

    desktop.presenter.set_hovered(True)
    settle(desktop.notch)
    assert desktop.presenter.presentation.content.symbol is NotchSymbol.TIMER
    assert desktop.notch.current_content.label == "03:00"


def finish_a_countdown(desktop) -> list:
    """Start five minutes from the panel, leave, and let it run out; returns the finishes."""
    from mira.core.countdown_timer import TIMER_FINISHED_EVENT

    finishes = []
    desktop.application.event_bus.subscribe(TIMER_FINISHED_EVENT, finishes.append)
    open_panel(desktop.notch)
    press(desktop.notch, preset(5))
    press(desktop.notch, TimerButton(TimerCommand.START))
    leave(desktop)
    elapse(desktop, 300)
    return finishes


def test_a_finished_countdown_waits_expanded_for_ok(desktop, monkeypatch):
    from mira.domain.embodiment_cue import EmbodimentCondition

    cues = []
    monkeypatch.setattr(desktop.controller, "show_cue", cues.append)

    finishes = finish_a_countdown(desktop)

    notch = desktop.notch
    assert desktop.application.timer.snapshot.state is TimerState.FINISHED
    assert notch.presence is NotchPresence.EXPANDED
    assert notch.timer_readout == "Timer done!"
    assert list(notch.timer_button_rects) == list(DONE_PANEL)
    assert EmbodimentCondition.TIMER_FINISHED in desktop.controller.conditions
    # Held by the condition, not replayed as a cue.
    assert cues == []

    # No timeout and no pointer movement ends it.
    desktop.presenter.set_hovered(True)
    leave(desktop)
    elapse(desktop, 3600)
    assert notch.presence is NotchPresence.EXPANDED
    assert notch.timer_readout == "Timer done!"
    assert EmbodimentCondition.TIMER_FINISHED in desktop.controller.conditions
    assert len(finishes) == 1


def test_ok_acknowledges_releases_the_eyes_and_restores_the_notch(desktop):
    from mira.domain.embodiment_cue import EmbodimentCondition

    finishes = finish_a_countdown(desktop)
    changes = []
    desktop.application.event_bus.subscribe("timer_changed", changes.append)

    press(desktop.notch, TimerButton(TimerCommand.ACKNOWLEDGE))

    assert desktop.application.timer.snapshot == TimerSnapshot(TimerState.IDLE, 300, 300.0)
    assert [change.state for change in changes] == [TimerState.IDLE]
    assert len(finishes) == 1
    assert EmbodimentCondition.TIMER_FINISHED not in desktop.controller.conditions
    assert desktop.presenter.presentation.timer_panel == ()
    assert desktop.notch.presence is NotchPresence.COMPACT

    # Asking again changes nothing.
    assert desktop.application.timer.acknowledge() is False
    desktop.on_timer_requested(TimerButton(TimerCommand.ACKNOWLEDGE))
    assert len(changes) == 1


def test_a_new_countdown_after_ok_finishes_once_more(desktop):
    finishes = finish_a_countdown(desktop)
    press(desktop.notch, TimerButton(TimerCommand.ACKNOWLEDGE))

    open_panel(desktop.notch)
    press(desktop.notch, preset(5))
    press(desktop.notch, TimerButton(TimerCommand.START))
    leave(desktop)
    elapse(desktop, 300)

    assert len(finishes) == 2
    assert desktop.notch.timer_readout == "Timer done!"


def test_settings_stay_reachable_while_the_timer_waits_for_ok(desktop):
    finish_a_countdown(desktop)
    point = QPoint(desktop.notch.width() // 2, 6)
    QCoreApplication.sendEvent(
        desktop.notch, QContextMenuEvent(QContextMenuEvent.Mouse, point, desktop.notch.mapToGlobal(point))
    )
    settle(desktop.notch)

    click_at(desktop.notch, desktop.notch.control_rects[NotchAction.OPEN_SETTINGS])

    assert desktop.settings_window is not None
    assert desktop.application.timer.snapshot.state is TimerState.FINISHED
    assert desktop.presenter.presentation.timer_panel == DONE_PANEL
    assert desktop.notch.presence is NotchPresence.EXPANDED


def test_the_done_panel_is_drawn(desktop):
    finish_a_countdown(desktop)

    assert lit_in_row(render(desktop.notch), desktop.notch) > 0
    readout = desktop.notch.timer_readout_rect
    ok = desktop.notch.timer_button_rects[TimerButton(TimerCommand.ACKNOWLEDGE)]
    assert readout.right() <= ok.left()


def test_the_panel_and_the_readout_are_drawn(desktop):
    open_panel(desktop.notch)
    assert lit_in_row(render(desktop.notch), desktop.notch) > 0

    press(desktop.notch, TimerButton(TimerCommand.START))
    leave(desktop)
    desktop.presenter.set_hovered(True)
    settle(desktop.notch)
    assert lit_in_row(render(desktop.notch), desktop.notch) > 0


def test_a_compact_notch_draws_the_same_with_or_without_a_running_timer(desktop):
    before = render(desktop.notch)

    desktop.application.timer.start(600)
    settle(desktop.notch)

    assert desktop.notch.presence is NotchPresence.COMPACT
    assert render(desktop.notch) == before


def test_settings_and_quit_still_work_after_the_timer_control(desktop):
    point = QPoint(desktop.notch.width() // 2, 6)
    QCoreApplication.sendEvent(
        desktop.notch, QContextMenuEvent(QContextMenuEvent.Mouse, point, desktop.notch.mapToGlobal(point))
    )
    settle(desktop.notch)

    click_at(desktop.notch, desktop.notch.control_rects[NotchAction.OPEN_SETTINGS])

    assert desktop.settings_window is not None
    assert desktop.presenter.presentation.controls == ()
    assert desktop.presenter.presentation.timer_panel == ()


# --- Layout ------------------------------------------------------------------------------------


def test_panel_cells_are_centred_and_shrink_in_proportion_when_they_do_not_fit():
    from mira.ui.notch.expanded_layout import PANEL_GAP_PX, ContentRow, panel_cells
    from mira.ui.notch.presence import Rect

    row = ContentRow(area=Rect(10.0, 50.0, 200.0, 28.0), symbol_px=28.0, label_px=20.0, gap_px=12.0)

    fitting = panel_cells(row, [40.0, 60.0])
    assert [cell.width for cell in fitting] == [40.0, 60.0]
    assert fitting[0].x - row.area.x == pytest.approx(row.area.right - fitting[-1].right)
    assert fitting[1].x - fitting[0].right == pytest.approx(PANEL_GAP_PX)

    crowded = panel_cells(row, [100.0, 100.0, 100.0])
    assert crowded[0].x == pytest.approx(row.area.x)
    assert crowded[-1].right == pytest.approx(row.area.right)
    assert crowded[0].width == pytest.approx(crowded[1].width)
    assert all(cell.height == row.area.height for cell in crowded)

    assert panel_cells(row, []) == []
