"""Choose a positionable Qt backend for the WSLg desktop bridge."""
from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path


def configure_desktop_platform(
    *, environment: MutableMapping[str, str] | None = None,
    wslg: bool | None = None,
) -> bool:
    """Prefer X11 in WSLg before QApplication selects a platform.

    The notch requires absolute screen placement. WSLg's Wayland compositor
    decides that placement itself. Explicit user choices, including headless
    test platforms, always win. This does not implement Windows desktop pinning.
    """
    env = os.environ if environment is None else environment
    if wslg is None:
        wslg = sys.platform == "linux" and Path("/mnt/wslg").is_dir()
    if not wslg or not env.get("DISPLAY") or "QT_QPA_PLATFORM" in env:
        return False
    env["QT_QPA_PLATFORM"] = "xcb"
    print("[display] WSLg: using X11 for notch screen positioning")
    return True
