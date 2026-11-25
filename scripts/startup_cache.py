"""Prepare immutable source bundles for the WSL->Windows startup cache.

Only Python program files are bundled. Launcher settings, preference files
and runtime session data are not bundled. Resources stay at their original
paths. Rebuilding a program version never overwrites the source checkout.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import tempfile
import zipfile


def source_files(root: Path) -> list[Path]:
    files = [p for p in (root / 'mira').rglob('*')
             if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts
             and p.suffix == '.py' and not p.name.startswith('.')]
    for name in ('native_bootstrap.py', 'startup_cache.py', 'windows_startup_cache.ps1'):
        path = root / 'scripts' / name
        if path.is_file():
            files.append(path)
    return sorted(files)


def prepare_manifest(root: Path, interpreter: Path, convert) -> Path:
    root = root.absolute()
    runtime = interpreter.parent.parent if interpreter.parent.name.lower() == 'scripts' else interpreter.parent
    fingerprint = hashlib.sha256()
    fingerprint.update(convert(str(runtime)).casefold().encode())
    records = [interpreter, *runtime.glob('*.dll'), *runtime.glob('python*.zip'),
               *runtime.glob('*._pth'), *runtime.glob('pyvenv.cfg'),
               *runtime.glob('Lib/site-packages/*.dist-info/METADATA'),
               *runtime.glob('Lib/site-packages/*.dist-info/entry_points.txt'),
               *runtime.glob('Lib/site-packages/*.pth')]
    for path in sorted(set(records)):
        stat = path.stat()
        fingerprint.update(f'{path.relative_to(runtime)}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    runtime_key = fingerprint.hexdigest()[:20]
    files = [(p.relative_to(root).as_posix(), p.read_bytes()) for p in source_files(root)]
    app_hash = hashlib.sha256()
    for relative, data in files:
        app_hash.update(relative.encode() + b'\0' + data)
    app_key = app_hash.hexdigest()[:20]
    cache = root / '.mira-launch-cache'
    cache.mkdir(mode=0o700, exist_ok=True)
    bundle = cache / f'app-{app_key}.zip'
    if not bundle.exists():
        fd, temp = tempfile.mkstemp(dir=cache, suffix='.zip')
        os.close(fd)
        try:
            with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
                for relative, data in files:
                    archive.writestr(relative, data)
            os.replace(temp, bundle)
        finally:
            Path(temp).unlink(missing_ok=True)
    source = convert(str(root))
    payload = dict(project_key=hashlib.sha256(source.casefold().encode()).hexdigest()[:16],
                   runtime_key=runtime_key, app_key=app_key, source=source,
                   runtime_source=convert(str(runtime)),
                   python_relative=interpreter.relative_to(runtime).as_posix(),
                   bundle=convert(str(bundle)),
                   descriptor=convert(str(cache / f'ready-{runtime_key}-{app_key}.json')))
    # Content-address the manifest too: concurrent launches never read a partly
    # replaced or another version's manifest while updating source code.
    manifest = cache / f'{runtime_key}-{app_key}.json'
    if not manifest.exists():
        fd, temp = tempfile.mkstemp(dir=cache, suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream)
            os.replace(temp, manifest)
        finally:
            Path(temp).unlink(missing_ok=True)
    return manifest
