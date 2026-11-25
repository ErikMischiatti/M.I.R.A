"""Desktop policy contracts across platforms, using safe recording adapters."""
from types import SimpleNamespace
from unittest.mock import Mock

from mira.ui.desktop.workspaces import WindowsAllWorkspaces, workspace_presence_for


def test_windows_pins_only_requested_hwnd_and_checks_the_result():
    state = {'pinned': False}
    view = SimpleNamespace(is_pinned=lambda: state['pinned'],
                           pin=lambda: state.update(pinned=True))
    factory = Mock(return_value=view)
    adapter = WindowsAllWorkspaces(factory)
    assert adapter.prepare_to_show(123) is False
    assert adapter.after_show(123)
    factory.assert_called_once_with(hwnd=123)


def test_windows_pinning_failure_is_reported_and_never_escapes(capsys):
    factory = Mock(side_effect=RuntimeError('unsupported Windows build'))
    adapter = WindowsAllWorkspaces(factory)
    assert not adapter.after_show(1)
    assert not adapter.after_show(1)
    assert capsys.readouterr().err.count('Windows desktop pinning unavailable') == 1


def test_native_windows_never_loads_xlib():
    assert isinstance(workspace_presence_for('windows', xlib_loader=Mock(side_effect=AssertionError)),
                      WindowsAllWorkspaces)


def test_windows_application_commands_are_allowlisted(monkeypatch, tmp_path):
    from mira.actions import desktop_actions as actions
    monkeypatch.setattr(actions.sys, 'platform', 'win32')
    monkeypatch.setattr(actions.shutil, 'which', lambda name: name)
    paths = actions.DesktopPaths(tmp_path, tmp_path, None)
    assert actions._resolve_app_command('calcolatrice', paths) == ('calculator', ['calc.exe'])
    assert actions._resolve_app_command('files', paths) == ('files', ['explorer.exe', str(tmp_path)])
    assert actions._resolve_app_command('cmd.exe & arbitrary-command', paths)[1] is None


def test_windows_directory_opens_only_after_existing_path_policy(monkeypatch, tmp_path):
    from mira.actions import desktop_actions as actions
    monkeypatch.setattr(actions.sys, 'platform', 'win32')
    opened = Mock()
    monkeypatch.setattr(actions.os, 'startfile', opened, raising=False)
    paths = actions.DesktopPaths(tmp_path, tmp_path, None)
    handler = actions.make_open_directory_action(paths)
    assert handler({'directory': 'home'}).success
    opened.assert_called_once_with(str(tmp_path))
    opened.reset_mock()
    assert not handler({'directory': str(tmp_path.parent)}).success
    opened.assert_not_called()


def test_native_notch_has_a_shell_view_without_changing_linux_tool_policy(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import Qt
    from mira.ui.notch.notch_window import NotchWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QGuiApplication, 'platformName', staticmethod(lambda: 'windows'))
    window = NotchWindow()
    try:
        assert window.windowType() == Qt.Window
        assert not window.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert window.testAttribute(Qt.WA_ShowWithoutActivating)
        assert window.focusPolicy() == Qt.NoFocus
    finally:
        from PySide6.QtCore import QCoreApplication, QEvent
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
