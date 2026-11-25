"""Safe transfer of arguments and explicit configuration across WSL/Windows."""
import importlib.util
from pathlib import Path


def bridge():
    spec = importlib.util.spec_from_file_location('windows_launcher', Path(__file__).resolve().parents[1] / 'scripts/launch_windows.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_powershell_literals_do_not_expand_shell_text():
    b = bridge()
    assert b.literal("a'$(Start-Process evil); b") == "'a''$(Start-Process evil); b'"


def test_native_bridge_converts_paths_and_preserves_explicit_mira_settings(monkeypatch):
    b = bridge()
    monkeypatch.setattr(b, 'windows_path', lambda path: 'WIN:' + path)
    script = b.script_for('/python.exe', 'mira.main', ['--notch', '--config', '/a b/n.json', 'text;whoami'],
        {'MIRA_VOICE': '1', 'MIRA_NOTCH_CONFIG': '/n.json', 'QT_QPA_PLATFORM': 'xcb', 'UNRELATED_SECRET': 'hidden'})
    assert "'--config' 'WIN:/a b/n.json' 'text;whoami'" in script
    assert "$env:MIRA_VOICE='1'" in script
    assert "$env:MIRA_NOTCH_CONFIG='WIN:/n.json'" in script
    assert "$env:QT_QPA_PLATFORM='windows'" in script
    assert 'UNRELATED_SECRET' not in script and 'hidden' not in script


def test_windows_drive_path_is_not_reinterpreted_as_a_linux_relative_path():
    b = bridge()
    assert b.windows_path(r'C:\Users\Some Name\notch.json') == r'C:\Users\Some Name\notch.json'
