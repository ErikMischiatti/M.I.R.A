"""The production choice of `Scheduler`, without the rest of the graph.

`build_application` takes its default scheduler from here. Standalone tools
that need timing but must not build the application graph — the notch tools
time transient notices — take it from here too, so choosing the adapter stays
in the composition layer and the tools never load `composition`.
"""

from __future__ import annotations

from mira.adapters.qt_scheduler import QtScheduler
from mira.domain.scheduler import Scheduler


def build_scheduler() -> Scheduler:
    """The production `Scheduler`, bound to the calling (Qt main) thread."""
    return QtScheduler()
