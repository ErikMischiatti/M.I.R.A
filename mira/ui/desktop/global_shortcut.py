"""One global keyboard shortcut on X11, and nothing else from the keyboard.

The notch never takes keyboard focus, so an application shortcut can never
reach MIRA while the user works elsewhere. A *global* shortcut is the
window system's own mechanism for that: `XGrabKey` on the root window for
exactly one key combination (with and without NumLock/CapsLock). The X
server then delivers that combination to MIRA and every other key to
whoever has focus — MIRA sees no other keystroke, needs no privilege and
installs no input hook.

A combination must include Ctrl, Alt or Super, so it can never take a key
away from typing. If another X client already grabbed it, the grab fails
with BadAccess; that is reported once and the shortcut stays off rather than
fighting over it. GNOME's own shortcuts are not X grabs (mutter handles
them itself), so a clash with one of those (Super+M opens the message
tray) is *not* detected: the default, Ctrl+Alt+M, is unbound in GNOME's
keybinding schemas.

Events are read from a separate, short Xlib connection under a
`QSocketNotifier`: no polling. Holding the keys down toggles once:
autorepeat is made detectable, and a press counts only after a release.
On other platforms (Wayland, offscreen) `GlobalShortcut.start` returns
False and nothing happens; the in-window key still works.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from collections.abc import Callable

from PySide6.QtCore import QSocketNotifier, Qt
from PySide6.QtGui import QKeySequence

# X.h
_KEY_PRESS, _KEY_RELEASE = 2, 3
_SHIFT, _LOCK, _CONTROL, _MOD1, _MOD2, _MOD4 = 1, 2, 4, 8, 16, 64
_GRAB_ASYNC = 1
_BAD_ACCESS = 10

_MODIFIERS = (
    (Qt.KeyboardModifier.ControlModifier, _CONTROL),
    (Qt.KeyboardModifier.AltModifier, _MOD1),
    (Qt.KeyboardModifier.MetaModifier, _MOD4),
    (Qt.KeyboardModifier.ShiftModifier, _SHIFT),
)
_REQUIRED = _CONTROL | _MOD1 | _MOD4


class ShortcutError(ValueError):
    pass


def parse_shortcut(text: str) -> tuple[str, int]:
    """``"Ctrl+Alt+M"`` → (X keysym name, X modifier mask); ShortcutError if unusable.

    One key plus modifiers, at least one of Ctrl, Alt or Super; letters,
    digits and F1–F12 only.
    """

    # Qt calls the Super (Windows) key "Meta".
    sequence = QKeySequence((text or "").replace("Super", "Meta"))
    if sequence.isEmpty() or sequence.count() != 1:
        raise ShortcutError(f"not a single key combination: {text!r}")
    combination = sequence[0]
    key, modifiers = combination.key(), combination.keyboardModifiers()
    mask = 0
    for qt_modifier, x_modifier in _MODIFIERS:
        if modifiers & qt_modifier:
            mask |= x_modifier
    if not mask & _REQUIRED:
        raise ShortcutError(f"{text!r} needs Ctrl, Alt or Super, so it never takes a key from typing")
    value = key.value if hasattr(key, "value") else int(key)
    if Qt.Key.Key_A.value <= value <= Qt.Key.Key_Z.value:
        name = chr(value).lower()
    elif Qt.Key.Key_0.value <= value <= Qt.Key.Key_9.value:
        name = chr(value)
    elif Qt.Key.Key_F1.value <= value <= Qt.Key.Key_F12.value:
        name = f"F{value - Qt.Key.Key_F1.value + 1}"
    else:
        raise ShortcutError(f"use a letter, a digit or F1–F12 in {text!r}")
    return name, mask


class _XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong), ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("x_root", ctypes.c_int), ("y_root", ctypes.c_int), ("state", ctypes.c_uint), ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xkey", _XKeyEvent), ("pad", ctypes.c_long * 24)]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("display", ctypes.c_void_p), ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong), ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))


class GlobalShortcut:
    """Calls ``on_activated`` each time the shortcut is pressed; see the module."""

    def __init__(self, shortcut: str, on_activated: Callable[[], None], *, platform: str = "xcb"):
        self.shortcut = shortcut
        self._on_activated = on_activated
        self._platform = platform
        self._x = None
        self._display = None
        self._root = 0
        self._keycode = 0
        self._masks: list[int] = []
        self._notifier: QSocketNotifier | None = None
        self._pressed = False
        self.error: str | None = None

    @property
    def active(self) -> bool:
        return self._display is not None

    def start(self) -> bool:
        if self.active:
            return True
        if self._platform != "xcb":
            self.error = f"global shortcuts are not available on {self._platform or 'this platform'}"
            return False
        try:
            name, mask = parse_shortcut(self.shortcut)
        except ShortcutError as error:
            self.error = str(error)
            print(f"[listening] global shortcut off: {error}", file=sys.stderr)
            return False
        library = ctypes.util.find_library("X11")
        if library is None:
            self.error = "libX11 not found"
            return False
        x = ctypes.CDLL(library)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int]
        x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x.XSetErrorHandler.restype = ctypes.c_void_p
        x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        x.XkbSetDetectableAutoRepeat.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

        display = x.XOpenDisplay(None)
        if not display:
            self.error = "cannot open the X display"
            return False
        root = x.XDefaultRootWindow(display)
        keycode = x.XKeysymToKeycode(display, x.XStringToKeysym(name.encode()))
        if not keycode:
            x.XCloseDisplay(display)
            self.error = f"no key for {name!r} on this keyboard"
            return False

        failed = []
        handler = _ERROR_HANDLER(lambda _d, event: failed.append(event.contents.error_code) or 0)
        previous = x.XSetErrorHandler(ctypes.cast(handler, ctypes.c_void_p))
        masks = [mask | extra for extra in (0, _MOD2, _LOCK, _MOD2 | _LOCK)]
        for variant in masks:
            x.XGrabKey(display, keycode, variant, root, 0, _GRAB_ASYNC, _GRAB_ASYNC)
        x.XSync(display, 0)
        x.XSetErrorHandler(previous)
        if failed:
            for variant in masks:
                x.XUngrabKey(display, keycode, variant, root)
            x.XSync(display, 0)
            x.XCloseDisplay(display)
            taken = _BAD_ACCESS in failed
            self.error = (f"{self.shortcut} is already used by another application" if taken
                          else f"{self.shortcut} could not be registered (X error {failed[0]})")
            print(f"[listening] global shortcut off: {self.error}", file=sys.stderr)
            return False

        x.XkbSetDetectableAutoRepeat(display, 1, None)
        self._x, self._display, self._root, self._keycode, self._masks = x, display, root, keycode, masks
        self._notifier = QSocketNotifier(x.XConnectionNumber(display), QSocketNotifier.Type.Read)
        self._notifier.activated.connect(self._drain)
        self.error = None
        print(f"[listening] global shortcut {self.shortcut} toggles listening")
        return True

    def stop(self) -> None:
        if not self.active:
            return
        self._notifier.setEnabled(False)
        self._notifier.activated.disconnect(self._drain)
        self._notifier.deleteLater()
        self._notifier = None
        for variant in self._masks:
            self._x.XUngrabKey(self._display, self._keycode, variant, self._root)
        self._x.XSync(self._display, 0)
        self._x.XCloseDisplay(self._display)
        self._display = None

    def _drain(self, *_) -> None:
        if not self.active:
            return
        event = _XEvent()
        while self.active and self._x.XPending(self._display):
            self._x.XNextEvent(self._display, ctypes.byref(event))
            self.handle(event.type, event.xkey.keycode)

    def handle(self, event_type: int, keycode: int) -> None:
        """One key event of the grabbed key; a press toggles once until released."""
        if keycode != self._keycode:
            return
        if event_type == _KEY_PRESS and not self._pressed:
            self._pressed = True
            self._on_activated()
        elif event_type == _KEY_RELEASE:
            self._pressed = False
