"""TASK_RUNNING: persistent while work runs, never a timed notice.

From the executor's run ids, through `TaskActivity`, to the shared
embodiment's condition layer and the notch's content. The real executor and
bus are used wherever a task's lifecycle is asserted.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from mira.actions.action_models import ActionRequest, ActionResult
from mira.application.composition import build_application
from mira.core.task_activity import TASKS_EVENT, TaskActivity
from mira.domain.embodiment import ActivityState, EmbodimentIntent
from mira.domain.embodiment_cue import (
    BREATH_LOW,
    CONDITION_TRANSITION_S,
    CUE_TREATMENTS,
    TRANSITION_S,
    EmbodimentCondition,
    EmbodimentCue,
    EyeShape,
)
from mira.domain.embodiment_frame import EmbodimentFrame, EyeFrame
from mira.domain.micro_motion import MicroMotions
from mira.domain.notice import Notice, NoticeCategory, NoticeTone
from mira.domain.scheduler import ManualScheduler
from mira.domain.tasks import RunningTasks
from mira.messaging.events import EventBus
from mira.ui.face.eye import Eye
from mira.ui.face.eye_painter import EyePainter
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.presence import NotchPresence
from mira.ui.notch.presentation import TASK_RUNNING_CONTENT, NotchPresenter

DT = 0.03
WORKING = EmbodimentCondition.TASK_RUNNING


@pytest.fixture(scope="module", autouse=True)
def application():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def run(controller: FaceController, seconds: float) -> None:
    for _ in range(round(seconds / DT)):
        controller.update(DT)


def shape(controller: FaceController) -> EyeShape:
    return controller.get_frame().left_eye.shape


# --- Run ids from the executor -------------------------------------------------------------


def executor_and_events():
    application = build_application(scheduler=ManualScheduler())
    events = []
    for name in ("action_started", "action_completed", "action_failed", TASKS_EVENT, "notice"):
        application.event_bus.subscribe(name, lambda payload, n=name: events.append((n, payload)))
    return application, application.brain.action_executor, events


def test_every_started_execution_gets_its_own_run_id():
    _, executor, events = executor_and_events()

    first = executor.execute(ActionRequest("echo_text", {"text": "a"}))
    second = executor.execute(ActionRequest("echo_text", {"text": "b"}))

    started = [payload.run_id for name, payload in events if name == "action_started"]
    assert started == [first.run_id, second.run_id] and first.run_id < second.run_id
    # Results compare as before: the run id is bookkeeping, not content.
    assert first == ActionResult(True, "echo_text", first.message, first.data)


def test_a_request_refused_before_starting_never_runs():
    _, executor, events = executor_and_events()

    refused = executor.execute(ActionRequest("no_such_action"))

    assert refused.run_id is None
    assert [name for name, _ in events if name in ("action_started", TASKS_EVENT)] == []


# --- TaskActivity ----------------------------------------------------------------------------


def lifecycle():
    bus = EventBus()
    tasks = TaskActivity(bus)
    changes = []
    bus.subscribe(TASKS_EVENT, changes.append)
    return bus, tasks, changes


def started(run_id):
    return ActionRequest("x", run_id=run_id)


def ended(run_id, success=True):
    return ActionResult(success, "x", "", run_id=run_id)


def test_a_task_runs_from_its_start_to_its_end():
    bus, tasks, changes = lifecycle()

    bus.emit("action_started", started(1))
    assert tasks.running == RunningTasks(frozenset({1})) and tasks.running.active
    bus.emit("action_completed", ended(1))

    assert not tasks.running.active
    assert [change.count for change in changes] == [1, 0]


def test_a_failure_ends_the_run_too():
    bus, tasks, _ = lifecycle()
    bus.emit("action_started", started(4))

    bus.emit("action_failed", ended(4, success=False))

    assert not tasks.running.active


def test_overlapping_tasks_keep_mira_working_until_the_last_ends():
    bus, tasks, changes = lifecycle()

    bus.emit("action_started", started(1))
    bus.emit("action_started", started(2))
    bus.emit("action_completed", ended(1))
    assert tasks.running.active and tasks.running.run_ids == {2}
    bus.emit("action_failed", ended(2, success=False))

    assert not tasks.running.active
    assert [change.count for change in changes] == [1, 2, 1, 0]


def test_results_that_never_started_or_are_unknown_change_nothing():
    bus, tasks, changes = lifecycle()
    bus.emit("action_started", started(1))

    for payload in (ended(None, success=False), ended(99), object(), None):
        bus.emit("action_failed", payload)
    bus.emit("action_started", started(1))  # a duplicate start

    assert tasks.running.run_ids == {1}
    assert len(changes) == 1


def test_a_real_task_stops_running_before_its_result_is_announced():
    _, executor, events = executor_and_events()

    executor.execute(ActionRequest("echo_text", {"text": "ciao"}))

    # These subscribers run after the production ones, so nested emissions
    # are recorded first; what matters is running → not running → result.
    sequence = [(name, getattr(payload, "active", payload)) for name, payload in events
                if name in (TASKS_EVENT, "notice")]
    assert sequence == [
        (TASKS_EVENT, True),
        (TASKS_EVENT, False),
        ("notice", Notice(NoticeCategory.TASK, NoticeTone.SUCCESS)),
    ]


def test_an_action_awaiting_confirmation_never_runs_and_warns():
    _, executor, events = executor_and_events()

    # Gated by its contract (`mira/actions/action_contracts.py`).
    result = executor.execute(ActionRequest("clear_session_memory"))

    assert result.data.get("requires_confirmation") and result.run_id is None
    assert [name for name, _ in events if name in ("action_started", TASKS_EVENT)] == []
    notices = [payload for name, payload in events if name == "notice"]
    assert notices == [Notice(NoticeCategory.TASK, NoticeTone.WARNING, "Needs confirmation")]


# --- The condition layer ------------------------------------------------------------------------


def controller():
    return FaceController(MicroMotions(random.Random(8)))


def test_task_running_eases_in_holds_without_timeout_and_eases_out():
    face = controller()
    face.set_intent(EmbodimentIntent(ActivityState.THINKING))

    face.set_condition(WORKING, True)
    run(face, CONDITION_TRANSITION_S + DT)
    assert shape(face) is EyeShape.FOCUSED
    run(face, 30.0)  # no lifetime: still working
    assert shape(face) is EyeShape.FOCUSED and face.conditions == {WORKING}
    assert face.intent.activity is ActivityState.THINKING

    face.set_condition(WORKING, False)
    run(face, CONDITION_TRANSITION_S + DT)
    assert shape(face) is EyeShape.NATURAL and face.conditions == frozenset()


def test_the_working_eyes_breathe_within_bounds():
    face = controller()
    face.set_condition(WORKING, True)
    run(face, CONDITION_TRANSITION_S + DT)

    amounts = []
    for _ in range(120):
        face.update(DT)
        amounts.append(face.get_frame().left_eye.shape_amount)

    assert min(amounts) >= BREATH_LOW - 1e-9 and max(amounts) <= 1.0 + 1e-9
    assert max(amounts) - min(amounts) > 0.1  # it moves, slowly


def test_an_error_shows_over_task_running_and_working_returns_after_it():
    face = controller()
    face.set_condition(WORKING, True)
    run(face, CONDITION_TRANSITION_S + DT)

    face.show_cue(EmbodimentCue.ERROR)
    run(face, TRANSITION_S + DT)
    assert shape(face) is EyeShape.CROSS
    run(face, CUE_TREATMENTS[EmbodimentCue.ERROR].hold_s + 2 * TRANSITION_S)

    assert face.active_cue is None and shape(face) is EyeShape.FOCUSED


def test_a_task_ending_during_an_error_leaves_the_plain_expression_after_it():
    face = controller()
    face.set_condition(WORKING, True)
    run(face, CONDITION_TRANSITION_S + DT)
    face.show_cue(EmbodimentCue.ERROR)
    run(face, TRANSITION_S + DT)

    face.set_condition(WORKING, False)
    run(face, CUE_TREATMENTS[EmbodimentCue.ERROR].hold_s + 2 * TRANSITION_S + CONDITION_TRANSITION_S)

    assert shape(face) is EyeShape.NATURAL and face.conditions == frozenset()


def test_a_task_that_finishes_shows_success_then_plain_eyes():
    face = controller()
    face.set_condition(WORKING, True)
    run(face, CONDITION_TRANSITION_S + DT)

    face.set_condition(WORKING, False)
    face.show_cue(EmbodimentCue.SUCCESS)
    run(face, TRANSITION_S + DT)
    assert shape(face) is EyeShape.CHEVRON
    run(face, CUE_TREATMENTS[EmbodimentCue.SUCCESS].hold_s + 2 * TRANSITION_S + CONDITION_TRANSITION_S)

    assert shape(face) is EyeShape.NATURAL


# --- Drawing ------------------------------------------------------------------------------------


EYE = Eye(x_ratio=0.3, y_ratio=0.3, width_ratio=0.18, height_ratio=0.22)


def lit_rows(frame: EyeFrame) -> tuple[set[int], QRectF]:
    image = QImage(700, 450, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = EyePainter(EYE, EYE, eye_color=QColor(245, 245, 245), lid_color=QColor(0, 0, 0))
    canvas = QRectF(0, 0, 700, 450)
    qp = QPainter(image)
    painter.paint(qp, canvas, EmbodimentFrame(frame, frame))
    qp.end()
    rect = painter.eye_rect(canvas, EYE, frame)
    rows = {y for y in range(image.height()) for x in range(int(rect.x()), int(rect.right()), 4)
            if image.pixelColor(x, y).alpha() > 16}
    return rows, rect


def test_focused_eyes_are_narrower_and_stay_inside_the_eye():
    natural, rect = lit_rows(EyeFrame())
    focused, _ = lit_rows(EyeFrame(shape=EyeShape.FOCUSED, shape_amount=1.0))

    assert len(focused) < len(natural) * 0.75
    assert min(focused) >= rect.top() - 1 and max(focused) <= rect.bottom() + 1


def test_focused_eyes_still_blink():
    open_rows, _ = lit_rows(EyeFrame(shape=EyeShape.FOCUSED, shape_amount=1.0))
    blinking, _ = lit_rows(EyeFrame(shape=EyeShape.FOCUSED, shape_amount=1.0, closed=True))

    assert 0 < len(blinking) < len(open_rows) / 3


# --- The notch ---------------------------------------------------------------------------------


def test_the_open_notch_says_working_but_running_never_expands_it():
    presenter = NotchPresenter(ManualScheduler())
    presenter.show()

    presenter.set_tasks_running(True)
    assert presenter.presentation.presence is NotchPresence.COMPACT
    assert presenter.presentation.reasons == frozenset()

    presenter.set_hovered(True)
    assert presenter.presentation.content == TASK_RUNNING_CONTENT
    presenter.present(Notice(NoticeCategory.ALERT, NoticeTone.WARNING, "Low battery"))
    assert presenter.presentation.content.label == "Low battery"  # a notice wins
    presenter.dismiss_notice()
    presenter.set_tasks_running(False)
    assert presenter.presentation.content is None  # idle shows nothing


# --- The shell, on every surface ----------------------------------------------------------------


@pytest.mark.parametrize("surface", ["main window", "notch"])
def test_running_work_reaches_the_shared_eyes_whichever_surface_shows_them(surface):
    from mira.ui.desktop_shell import DesktopShell, Surface

    application = build_application(scheduler=ManualScheduler())
    shell = DesktopShell(application, initial_surface=Surface(surface))
    try:
        application.event_bus.emit(TASKS_EVENT, RunningTasks(frozenset({7})))
        assert WORKING in shell.controller.conditions

        shell.show_surface(Surface.NOTCH)  # built now, or already there
        assert shell.presenter.presentation.tasks_running
        shell.show_surface(Surface.MAIN_WINDOW)
        assert shell.window.face_widget.controller is shell.controller

        application.event_bus.emit(TASKS_EVENT, RunningTasks())
        assert WORKING not in shell.controller.conditions
        assert not shell.presenter.presentation.tasks_running
    finally:
        shell.shutdown()
        application.shutdown()
        for obj in (shell.notch, shell.window, shell.animator):
            if obj is not None:
                obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_real_task_in_the_runtime_ends_running_and_shows_success():
    from mira.ui.desktop_shell import DesktopShell, Surface

    application = build_application(scheduler=ManualScheduler())
    shell = DesktopShell(application, initial_surface=Surface.NOTCH)
    seen = []
    application.event_bus.subscribe(TASKS_EVENT, lambda running: seen.append(
        (running.active, WORKING in shell.controller.conditions)))
    try:
        application.brain.action_executor.execute(ActionRequest("echo_text", {"text": "ciao"}))

        assert seen == [(True, True), (False, False)]
        assert shell.controller.active_cue is EmbodimentCue.SUCCESS
        assert not shell.presenter.presentation.tasks_running
    finally:
        shell.shutdown()
        application.shutdown()
        for obj in (shell.notch, shell.animator):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("pinned", [False, True], ids=["compact", "expanded"])
def test_working_eyes_stay_inside_the_notch_and_off_its_content(pinned):
    from mira.ui.face.face_animator import FaceAnimator
    from mira.ui.notch.eye_layout import production_envelope
    from mira.ui.notch.notch_window import NotchWindow
    from mira.ui.notch.startup import bind_presenter, deliver_tasks

    face = controller()
    animator = FaceAnimator(face)
    notch = NotchWindow(animator=animator)
    presenter = NotchPresenter(ManualScheduler())
    bind_presenter(notch, presenter)
    try:
        presenter.show()
        presenter.set_pinned(pinned)
        notch.transition.setCurrentTime(notch.transition.duration())
        deliver_tasks(True, presenter=presenter, controller=face)
        run(face, CONDITION_TRANSITION_S + DT)
        assert shape(face) is EyeShape.FOCUSED

        image = notch.grab().toImage()
        envelope = production_envelope()
        bounds = [notch.eye_layout.motion_bounds(side, envelope) for side in ("left", "right")]
        row = notch.content_row
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() < 200 or color.lightness() < 200:
                    continue
                in_eyes = any(b.x - 1 <= x <= b.right + 1 and b.y - 1 <= y <= b.bottom + 1 for b in bounds)
                in_content = pinned and row.area.y - 1 <= y <= row.area.bottom + 1
                assert in_eyes or in_content, (x, y)
        assert notch.presentation.content == TASK_RUNNING_CONTENT  # what hover would reveal
        assert notch.presence is (NotchPresence.EXPANDED if pinned else NotchPresence.COMPACT)
    finally:
        presenter.shutdown()
        notch.hide()
        for obj in (notch, animator):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
