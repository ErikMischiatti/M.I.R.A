"""Track which actions are running and announce it as ``"tasks_changed"``.

Fed only by the executor's own lifecycle events — ``action_started`` opens a
run, ``action_completed``/``action_failed`` with the same ``run_id`` closes
it — so there is no second task lifecycle. Results that never started
(``run_id`` None) and unknown ids change nothing, so the set can never leak a
run or go below empty. Every change of the set is emitted as a
`RunningTasks` value; embodiments turn "any task running" into their
persistent working indication (`EmbodimentCondition.TASK_RUNNING`).

Today execution is synchronous on the UI thread, so a run opens and closes
within one call and no frame shows it; the state is correct regardless, and
becomes visible as soon as any action runs for longer.
"""

from __future__ import annotations

from mira.domain.tasks import RunningTasks
from mira.messaging.events import EventBus

TASKS_EVENT = "tasks_changed"


class TaskActivity:
    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        self._running: dict[int, str] = {}
        event_bus.subscribe("action_started", self.on_action_started)
        event_bus.subscribe("action_completed", self.on_action_ended)
        event_bus.subscribe("action_failed", self.on_action_ended)

    @property
    def running(self) -> RunningTasks:
        return RunningTasks(frozenset(self._running))

    def on_action_started(self, request) -> None:
        run_id = getattr(request, "run_id", None)
        if run_id is None or run_id in self._running:
            return
        self._running[run_id] = getattr(request, "action_name", "?")
        print(f"[tasks] running: {self._running[run_id]} (run {run_id}; {len(self._running)} in progress)")
        self._bus.emit(TASKS_EVENT, self.running)

    def on_action_ended(self, result) -> None:
        run_id = getattr(result, "run_id", None)
        if run_id is None or run_id not in self._running:
            return
        name = self._running.pop(run_id)
        print(f"[tasks] ended: {name} (run {run_id}; {len(self._running)} in progress)")
        self._bus.emit(TASKS_EVENT, self.running)
