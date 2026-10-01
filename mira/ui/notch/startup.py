"""Build a `NotchWindow` from notch settings: the one way every launcher does it.

`MIRA` (through `DesktopShell`), `MIRA-NOTCH` and `MIRA-TUNE` all open the
notch with the same `NotchSettings` model loaded by `config_store`, and all
recover from rejected settings the same way. Keeping that here means the
three cannot drift apart.
"""

from __future__ import annotations

import sys

from mira.ui.face.face_animator import FaceAnimator
from mira.ui.notch.config_store import NotchSettings
from mira.ui.notch.notch_window import NotchWindow
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
