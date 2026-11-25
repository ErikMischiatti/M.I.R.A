"""Startup contracts: lightweight entry and a resource-safe local code mirror."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_importing_entrypoint_does_not_import_gui_graph_or_devices():
    result = subprocess.run([sys.executable, '-c', '''
import sys
import mira.main
assert 'PySide6.QtWidgets' not in sys.modules
assert 'mira.core.brain' not in sys.modules
assert 'mira.ui.desktop_shell' not in sys.modules
assert 'mira.adapters.qt_audio' not in sys.modules
'''], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_mirror_reads_local_code_but_keeps_source_resource_paths(tmp_path):
    source, mirror = tmp_path / 'source', tmp_path / 'mirror'
    for root in (source, mirror):
        (root / 'mira').mkdir(parents=True)
        (root / 'mira' / '__init__.py').write_text('')
    (source / 'mira' / 'resource.json').write_text('{"saved": true}')
    (source / 'mira' / 'probe.py').write_text('raise RuntimeError("source code must not be loaded")')
    (mirror / 'mira' / 'probe.py').write_text('''
import json
from pathlib import Path
p=Path(__file__)
print(json.dumps({'file':str(p),'cwd':str(Path.cwd()),'resource':json.loads(p.with_name('resource.json').read_text())}))
''')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/native_bootstrap.py'), '--cwd', str(source),
                             str(source), str(mirror), 'mira.probe'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert Path(data['file']) == source / 'mira' / 'probe.py'
    assert data['resource'] == {'saved': True}
    assert Path(data['cwd']) == source
    assert not (source / 'mira' / '__pycache__').exists()
    assert (mirror / 'mira' / '__pycache__').exists()


def test_bundle_is_reused_and_invalidates_on_program_changes(tmp_path):
    cache = load_script('startup_cache')
    (tmp_path / 'mira').mkdir()
    module = tmp_path / 'mira' / '__init__.py'
    module.write_text('VERSION=1')
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/native_bootstrap.py').write_text('# bootstrap')
    env = tmp_path / 'env'
    env.mkdir()
    python = env / 'python.exe'
    python.touch()
    convert = lambda value: str(value)
    first = cache.prepare_manifest(tmp_path, python, convert)
    timestamp = first.stat().st_mtime_ns
    assert cache.prepare_manifest(tmp_path, python, convert) == first
    assert first.stat().st_mtime_ns == timestamp
    module.write_text('VERSION=2')
    second = cache.prepare_manifest(tmp_path, python, convert)
    assert second != first
    assert json.loads(second.read_text())['runtime_key'] == json.loads(first.read_text())['runtime_key']
    python.write_bytes(b'updated interpreter')
    third = cache.prepare_manifest(tmp_path, python, convert)
    assert json.loads(third.read_text())['runtime_key'] != json.loads(second.read_text())['runtime_key']


def test_bundle_does_not_include_local_secrets_or_bytecode(tmp_path):
    cache = load_script('startup_cache')
    (tmp_path / 'mira').mkdir()
    (tmp_path / 'mira/__init__.py').write_text('')
    (tmp_path / 'mira/.env').write_text('SECRET=not a program asset')
    (tmp_path / 'mira/preferences.json').write_text('{"token": "not copied"}')
    (tmp_path / 'mira/__pycache__').mkdir()
    (tmp_path / 'mira/__pycache__/a.pyc').touch()
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/native_bootstrap.py').touch()
    assert [p.relative_to(tmp_path).as_posix() for p in cache.source_files(tmp_path)] == [
        'mira/__init__.py', 'scripts/native_bootstrap.py']
