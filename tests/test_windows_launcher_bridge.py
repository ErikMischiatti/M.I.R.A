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


def test_warm_cache_launch_uses_argv_and_wslenv_not_shell_settings(monkeypatch, tmp_path):
    import json
    b = bridge()
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / '.complete').write_text('env')
    exe = runtime / 'python.exe'
    exe.touch();exe.chmod(0o755)
    app = tmp_path / 'app'
    (app / 'scripts').mkdir(parents=True)
    (app / '.complete').write_text('code')
    bootstrap = app / 'scripts/native_bootstrap.py'
    bootstrap.touch()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'runtime_key': 'env', 'app_key': 'code'}))
    (tmp_path / 'ready-env-code.json').write_text(json.dumps({
        'Python': 'EXE', 'Bootstrap': 'BOOT', 'Source': 'SOURCE', 'Mirror': 'MIRROR'}))
    monkeypatch.setattr(b.subprocess, 'check_output', lambda args, **kwargs: {'EXE': str(exe), 'BOOT': str(bootstrap)}[args[-1]])
    monkeypatch.setattr(b, 'windows_path', lambda value: 'WIN:' + value)
    argv, env = b.direct_launch(str(manifest), 'mira.main', ['--notch', '--config', '/a b.json'],
                               {'MIRA_VOICE': '1', 'QT_QPA_PLATFORM': 'xcb', 'UNRELATED_SECRET': 'private', 'WSLENV': 'EXISTING/p'})
    assert argv[:3] == [str(exe), '-u', 'BOOT']
    assert argv[-4:] == ['mira.main', '--notch', '--config', 'WIN:/a b.json']
    assert env['QT_QPA_PLATFORM'] == 'windows'
    assert 'MIRA_VOICE' in env['WSLENV'] and 'EXISTING/p' in env['WSLENV']
    assert 'UNRELATED_SECRET' not in env['WSLENV']
    assert all('private' not in arg for arg in argv)
    (runtime / '.complete').unlink()
    assert b.direct_launch(str(manifest), 'mira.main', [], {}) is None


def test_missing_cache_descriptor_falls_back_to_setup(tmp_path):
    b = bridge()
    assert b.direct_launch(str(tmp_path / 'missing.json'), 'mira.main', [], {}) is None
