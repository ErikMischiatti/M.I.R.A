"""Keyboard activation of listening: the in-window key and the global shortcut.

Both only *ask*: each press emits ``"listening_toggle_requested"`` with
`ActivationSource.KEYBOARD` on the application bus, and the listening
controller decides (`mira.core.listening`). Two keys, for two situations:

- **In a MIRA window**, the plain key (``M`` by default) toggles listening —
  but never while a text entry has focus, where it is simply typed: typing
  "MIRA" in the chat must type "MIRA". The filter is application-wide and
  looks at the widget receiving the key and at the focused widget, so it
  holds for every text field there is.
- **Anywhere on the desktop**, the global shortcut (``Ctrl+Alt+M`` by
  default) toggles listening through `GlobalShortcut`; it always carries a
  modifier, so it never takes a key from typing in another application.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QKeySequenceEdit,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from mira.ui.desktop.global_shortcut import GlobalShortcut


def is_text_entry(widget: QWidget | None) -> bool:
    """Whether ``widget`` (the focus widget) would consume a typed letter."""

    if widget is None:
        return False
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
        # A read-only view (the chat history) takes no typing.
        return not widget.isReadOnly()
    if isinstance(widget, (QAbstractSpinBox, QKeySequenceEdit)):
        return True
    if isinstance(widget, QComboBox) and widget.isEditable():
        return True
    # Anything else that takes text input: input-method enabled widgets.
    return widget.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)


class InWindowKey(QObject):
    """An application-wide filter for the plain listening key; see the module."""

    def __init__(self, key: str, on_activated: Callable[[], None]):
        super().__init__()
        sequence = QKeySequence(key)
        self.key = sequence[0].key() if not sequence.isEmpty() else Qt.Key.Key_M
        self._on_activated = on_activated

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.KeyPress or event.isAutoRepeat():
            return False
        if event.key() != self.key or event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            return False
        # The widget the key is delivered to, and the one with focus: if
        # either takes text, the key is text. Checking only the focus widget
        # is not enough — it can be None while a field still receives keys.
        receiver = watched if isinstance(watched, QWidget) else None
        if is_text_entry(receiver) or is_text_entry(QApplication.focusWidget()):
            return False  # typed as text
        self._on_activated()
        return True


class KeyboardActivation:
    """Both keys, started and stopped together."""

    def __init__(self, request: Callable[[], None], *, in_window_key: str = "M",
                 global_shortcut: str | None = "Ctrl+Alt+M", platform: str | None = None):
        self.in_window = InWindowKey(in_window_key, request)
        application = QApplication.instance()
        platform = platform if platform is not None else (application.platformName() if application else "")
        self.shortcut = GlobalShortcut(global_shortcut, request, platform=platform) if global_shortcut else None
        self._installed = False

    def start(self) -> None:
        application = QApplication.instance()
        if application is not None and not self._installed:
            application.installEventFilter(self.in_window)
            self._installed = True
        if self.shortcut is not None:
            self.shortcut.start()

    def stop(self) -> None:
        application = QApplication.instance()
        if application is not None and self._installed:
            application.removeEventFilter(self.in_window)
            self._installed = False
        if self.shortcut is not None:
            self.shortcut.stop()
