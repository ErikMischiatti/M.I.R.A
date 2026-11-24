"""UI-independent view of the work MIRA is carrying out right now.

A task is one execution of an action, from ``action_started`` to its
``action_completed`` or ``action_failed``, identified by the executor's
``run_id``. `RunningTasks` is the set of executions that have started and
not yet ended. It is *state* — true for as long as the work lasts — not a
result: results are notices (`mira.domain.notice`), and an action refused
before it started (invalid, blocked, awaiting confirmation) never runs.

Several executions may overlap; MIRA is working while any one remains.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunningTasks:
    run_ids: frozenset[int] = frozenset()

    @property
    def active(self) -> bool:
        return bool(self.run_ids)

    @property
    def count(self) -> int:
        return len(self.run_ids)
