"""Ask the window manager to show a window on every virtual workspace.

The notch is ambient presence: it should follow the user from workspace to
workspace instead of staying where it was first mapped. Qt has no window
flag for that, so the request is made in window-manager terms.

Mechanism (X11, EWMH). Before a window is mapped, its `_NET_WM_DESKTOP`
property is set to 0xFFFFFFFF, which the EWMH specification defines as "on
all desktops". The window manager reads the property when it starts managing
the window; mutter (GNOME on X11) honours it, and so do other EWMH window
managers. Qt's xcb plugin never writes this property, so nothing overwrites
it. A window manager removes the property when a window is withdrawn, so the
request is made again before every map (`NotchWindow.showEvent`), not once.
No workspace is polled and nothing is moved or recreated on workspace
changes.

The request goes through its own short-lived Xlib connection, opened with
`ctypes` from the system's libX11 (present wherever an X server is used, so
no Python dependency is added), and is synchronised before returning, so the
server has applied it before Qt's map request arrives on Qt's connection.

Fallback. Any other platform — Wayland, offscreen, a missing libX11 or an
unreachable display — gets `NoWorkspacePresence`: the request is a no-op and
the window behaves as before (on the workspace it was mapped on). Wayland has
no client-side equivalent; the compositor alone decides.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import Protocol

# EWMH: a _NET_WM_DESKTOP of 0xFFFFFFFF means "on all desktops".
ALL_DESKTOPS = 0xFFFFFFFF

# Xlib constants (X.h / Xatom.h).
_XA_CARDINAL = 6
_PROP_MODE_REPLACE = 0


class WorkspacePresence(Protocol):
    """How a window asks to be present on every workspace."""

    @property
    def supported(self) -> bool: ...

    def prepare_to_show(self, window_id: int) -> bool:
        """Request presence on all workspaces; call before the window maps.

        Returns whether the request was made. Never raises.
        """
        ...


class NoWorkspacePresence:
    """The fallback: the window manager keeps its default placement."""

    supported = False

    def prepare_to_show(self, window_id: int) -> bool:
        return False


class Xlib(Protocol):
    """The four libX11 calls the request needs; a seam for tests."""

    def open_display(self) -> int | None: ...

    def intern_atom(self, display: int, name: bytes) -> int: ...

    def set_cardinal(self, display: int, window: int, atom: int, value: int) -> None: ...

    def sync_and_close(self, display: int) -> None: ...


class _CtypesXlib:
    """libX11 through `ctypes`, loaded on construction."""

    def __init__(self, library: ctypes.CDLL):
        library.XOpenDisplay.restype = ctypes.c_void_p
        library.XOpenDisplay.argtypes = [ctypes.c_char_p]
        library.XInternAtom.restype = ctypes.c_ulong
        library.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        library.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
        ]
        library.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self._x = library

    def open_display(self) -> int | None:
        # None: the DISPLAY Qt itself connected to.
        return self._x.XOpenDisplay(None) or None

    def intern_atom(self, display: int, name: bytes) -> int:
        return self._x.XInternAtom(display, name, 0)

    def set_cardinal(self, display: int, window: int, atom: int, value: int) -> None:
        # Format-32 data is passed as C longs.
        data = ctypes.c_ulong(value)
        self._x.XChangeProperty(display, window, atom, _XA_CARDINAL, 32, _PROP_MODE_REPLACE, ctypes.byref(data), 1)

    def sync_and_close(self, display: int) -> None:
        self._x.XSync(display, 0)
        self._x.XCloseDisplay(display)


class X11AllWorkspaces:
    """Sets `_NET_WM_DESKTOP` to all desktops before each map (see module)."""

    supported = True

    def __init__(self, xlib: Xlib):
        self._xlib = xlib
        self._warned = False

    def prepare_to_show(self, window_id: int) -> bool:
        try:
            display = self._xlib.open_display()
            if display is None:
                self._warn("cannot open the X display")
                return False
            try:
                atom = self._xlib.intern_atom(display, b"_NET_WM_DESKTOP")
                self._xlib.set_cardinal(display, window_id, atom, ALL_DESKTOPS)
            finally:
                self._xlib.sync_and_close(display)
        except Exception as error:  # a platform quirk must never break showing
            self._warn(str(error))
            return False
        return True

    def _warn(self, reason: str) -> None:
        if not self._warned:
            self._warned = True
            print(f"[notch] cannot request all workspaces: {reason}", file=sys.stderr)


def load_xlib() -> Xlib | None:
    """The system libX11, or None when it is not installed."""

    name = ctypes.util.find_library("X11")
    if name is None:
        return None
    try:
        return _CtypesXlib(ctypes.CDLL(name))
    except (OSError, AttributeError):
        return None


def workspace_presence_for(platform_name: str, *, xlib_loader=load_xlib) -> WorkspacePresence:
    """The adapter for Qt's platform plugin name (`QGuiApplication.platformName`)."""

    if platform_name != "xcb":
        return NoWorkspacePresence()
    xlib = xlib_loader()
    if xlib is None:
        print("[notch] libX11 not found; the notch stays on one workspace", file=sys.stderr)
        return NoWorkspacePresence()
    return X11AllWorkspaces(xlib)
