"""Execute a local source mirror while retaining original resource locations.

Only MIRA's Python modules are mirrored. __file__ and tracebacks retain the
source checkout path, so configuration saves and project actions keep their
existing semantics; module searches and bytecode I/O use the local mirror.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path
import runpy
import os
import sys


class MirroredLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, fullname: str, origin: str, source_root: Path, mirror_root: Path):
        super().__init__(fullname, origin)
        self.source_root = source_root
        self.mirror_root = mirror_root

    def local(self, path: str) -> str:
        return str(self.mirror_root / Path(path).relative_to(self.source_root))

    def get_data(self, path):
        return super().get_data(self.local(path))

    def path_stats(self, path):
        return super().path_stats(self.local(path))

    def _cache_bytecode(self, source_path, bytecode_path, data):
        # CPython reads source permissions before writing .pyc. Keep that stat
        # local too, otherwise first compilation would still touch UNC files.
        return super()._cache_bytecode(self.local(source_path), bytecode_path, data)

    def set_data(self, path, data, **kwargs):
        return super().set_data(self.local(path), data, **kwargs)


class SourceMirror(importlib.abc.MetaPathFinder):
    def __init__(self, source: Path, mirror: Path):
        self.source = source
        self.mirror = mirror

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'mira' and not fullname.startswith('mira.'):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, [str(self.mirror)] if path is None else path)
        if spec is None or not isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            return spec
        if not Path(spec.origin).is_relative_to(self.mirror):
            return spec  # an explicitly added extension path is not mirrored
        origin = str(self.source / Path(spec.origin).relative_to(self.mirror))
        return importlib.util.spec_from_file_location(
            fullname, origin,
            loader=MirroredLoader(fullname, origin, self.source, self.mirror),
            submodule_search_locations=spec.submodule_search_locations,
        )


def main():
    options = sys.argv[1:]
    if options[:1] == ['--cwd']:
        os.chdir(options[1])
        options = options[2:]
    source, mirror, module, *arguments = options
    sys.meta_path.insert(0, SourceMirror(Path(source), Path(mirror)))
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
