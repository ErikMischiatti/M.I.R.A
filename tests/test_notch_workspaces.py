"""The notch asks to be on every virtual workspace, behind a platform adapter.

The X11 request is checked against a recording fake libX11, so no test needs
an X server; the real behaviour on GNOME/X11 is a manual smoke test.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

pytest.importorskip("PySide6")

from mira.ui.desktop.workspaces import (
    ALL_DESKTOPS,
    NoWorkspacePresence,
    X11AllWorkspaces,
    workspace_presence_for,
)


class FakeXlib:
    def __init__(self, *, display=7, fail=None):
        self.calls = []
        self.display = display
        self.fail = fail

    def open_display(self):
        self.calls.append("open")
        return self.display

    def intern_atom(self, display, name):
        self.calls.append(("atom", display, name))
        return 99

    def set_cardinal(self, display, window, atom, value):
        if self.fail:
            raise self.fail
        self.calls.append(("set", display, window, atom, value))

    def sync_and_close(self, display):
        self.calls.append(("close", display))


def test_x11_sets_net_wm_desktop_to_all_desktops_and_closes_its_connection():
    xlib = FakeXlib()

    assert X11AllWorkspaces(xlib).prepare_to_show(0x4200007) is True
    assert xlib.calls == [
        "open",
        ("atom", 7, b"_NET_WM_DESKTOP"),
        ("set", 7, 0x4200007, 99, ALL_DESKTOPS),
        ("close", 7),
    ]
    assert ALL_DESKTOPS == 0xFFFFFFFF


def test_an_unreachable_display_fails_gracefully(capsys):
    presence = X11AllWorkspaces(FakeXlib(display=None))

    assert presence.prepare_to_show(1) is False
    assert presence.prepare_to_show(1) is False
    # Reported once, not on every show.
    assert capsys.readouterr().err.count("cannot request all workspaces") == 1


def test_an_xlib_error_never_escapes_and_still_closes_the_connection():
    xlib = FakeXlib(fail=OSError("BadWindow"))

    assert X11AllWorkspaces(xlib).prepare_to_show(1) is False
    assert xlib.calls[-1] == ("close", 7)


@pytest.mark.parametrize("platform", ["wayland", "offscreen", "minimal", ""])
def test_other_platforms_get_the_no_op_fallback(platform):
    presence = workspace_presence_for(platform, xlib_loader=lambda: pytest.fail("libX11 loaded"))

    assert isinstance(presence, NoWorkspacePresence)
    assert presence.prepare_to_show(1) is False


def test_xcb_without_libx11_falls_back(capsys):
    assert isinstance(workspace_presence_for("xcb", xlib_loader=lambda: None), NoWorkspacePresence)
    assert "libX11 not found" in capsys.readouterr().err


def test_xcb_uses_the_x11_adapter():
    assert isinstance(workspace_presence_for("xcb", xlib_loader=FakeXlib), X11AllWorkspaces)


# --- The notch window ------------------------------------------------------------


@pytest.fixture
def qt_application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class RecordingPresence:
    supported = True

    def __init__(self):
        self.windows = []

    def prepare_to_show(self, window_id):
        self.windows.append(window_id)
        return True


def test_the_notch_requests_every_workspace_before_each_show(qt_application):
    from mira.ui.notch.notch_window import NotchWindow
    from mira.ui.notch.presence import NotchPresence

    presence = RecordingPresence()
    notch = NotchWindow(workspace_presence=presence)
    try:
        assert presence.windows == []
        notch.set_presence(NotchPresence.COMPACT, animated=False)
        notch.set_presence(NotchPresence.HIDDEN, animated=False)
        notch.set_presence(NotchPresence.COMPACT, animated=False)

        # Once per map: a withdrawn window loses the request.
        assert presence.windows == [int(notch.winId())] * 2
        assert notch.all_workspaces_requested
    finally:
        notch.hide()
        notch.deleteLater()


def test_offscreen_notches_use_the_fallback(qt_application):
    from mira.ui.notch.notch_window import NotchWindow

    notch = NotchWindow()
    try:
        notch.show()
        assert isinstance(notch.workspace_presence, NoWorkspacePresence)
        assert notch.all_workspaces_requested is False
    finally:
        notch.hide()
        notch.deleteLater()


def test_presentation_and_domain_code_never_import_window_system_modules():
    root = pathlib.Path(__file__).resolve().parents[1] / "mira"
    platform_modules = {"ctypes", "mira.ui.desktop", "mira.ui.desktop.workspaces"}
    pure = [*sorted((root / "domain").glob("*.py")), root / "ui/notch/presentation.py",
            root / "ui/notch/presence.py", root / "ui/notch/expanded_layout.py",
            root / "ui/notch/visual_config.py", root / "ui/notch/config_store.py"]
    for path in pure:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert not any(name == m or name.startswith(m + ".") for m in platform_modules), (path, name)
