"""Build a `NotchWindow` from notch settings: the one way every launcher does it.

`MIRA` (through `DesktopShell`), `MIRA-NOTCH` and `MIRA-TUNE` all open the
notch with the same `NotchSettings` model loaded by `config_store`, and all
recover from rejected settings the same way. Keeping that here means the
three cannot drift apart.
"""

from __future__ import annotations

import sys

from mira.domain.embodiment_cue import EmbodimentCondition, cue_for_notice
from mira.domain.notice import Notice
from mira.ui.face.face_animator import FaceAnimator
from mira.ui.face.face_controller import FaceController
from mira.ui.notch.config_store import NotchSettings
from mira.ui.notch.notch_window import NotchWindow
from mira.ui.notch.presentation import NotchPresenter
from mira.ui.notch.visual_config import InvalidVisualConfig


def build_notch(settings: NotchSettings, *, animator: FaceAnimator) -> NotchWindow:
    """A hidden notch showing ``animator``'s embodiment with ``settings``.

    Settings the shown embodiment rejects are reported and replaced by the
    production defaults; building never fails on configuration. Nothing is
    shown and the animator is neither started nor stopped.
    """

    notch = NotchWindow(animator=animator, monitor_preference=settings.monitor)
    try:
        notch.apply_visual_config(settings.visual)
    except InvalidVisualConfig as error:
        # Saved settings were checked against the packaged profiles; the
        # embodiment shown here may differ. Fall back rather than clip.
        print(f"[notch] saved visual configuration not applied: {error}", file=sys.stderr)
        notch.apply_visual_config(NotchSettings().visual)
    return notch


def bind_presenter(notch: NotchWindow, presenter: NotchPresenter) -> None:
    """Make ``presenter`` the authority over what ``notch`` presents.

    The notch's hover reports become presenter input, and every presentation
    the presenter resolves is rendered by the notch, starting with the
    current one. Afterwards nothing else should call the notch's presence
    methods; ask the presenter instead.
    """

    notch.hover_changed.connect(presenter.set_hovered)
    notch.controls_toggle_requested.connect(presenter.toggle_controls)
    presenter.subscribe(notch.present)
    notch.present(presenter.presentation)


def deliver_notice(notice: Notice, *, presenter: NotchPresenter | None, controller: FaceController | None) -> None:
    """Present ``notice`` the one way the desktop does it.

    The notch's presenter shows it (and decides whether it expands); the
    shared embodiment reacts with the notice's cue, if it has one, as an
    overlay that ends on its own. The running shell and the tuning
    previews both come through here, so previews exercise the real path.
    """

    if presenter is not None:
        presenter.present(notice)
    cue = cue_for_notice(notice)
    if controller is not None and cue is not None:
        controller.show_cue(cue)


def deliver_tasks(running: bool, *, presenter: NotchPresenter | None, controller: FaceController | None) -> None:
    """Show whether MIRA is working, the one way the desktop does it.

    The shared embodiment holds TASK_RUNNING for exactly as long as work
    runs; the notch only changes what it would show when open. Nothing
    expands. Used by the running shell and by the tuning previews.
    """

    if controller is not None:
        controller.set_condition(EmbodimentCondition.TASK_RUNNING, running)
    if presenter is not None:
        presenter.set_tasks_running(running)
