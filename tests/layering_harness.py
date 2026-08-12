"""Shared harness for the layering-boundary tests.

A plain module rather than a `conftest.py` fixture, because these are needed at
import time — `BLOCKER` is concatenated into test source strings while the module
is being collected, which a fixture cannot supply.

Used by `tests/test_layering.py` and `tests/test_memory_messaging_boundaries.py`.
Each name here was declared in both of them at commit 476293a: `run_checker`
byte-identical, `isolated_tree` differing only in its docstring, `run_python`
differing only in whether `cwd` was a parameter or the hardcoded `REPO_ROOT` it
is now, and `BLOCKER` blocking one Qt binding in the first and four in the second.

`load_checker` is deliberately not here: it has one consumer, so it stayed local
in `tests/test_layering.py`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "scripts" / "check_layering.py"

# Source for a `sys.meta_path` hook refusing every Qt binding — the four named in
# BLOCKED below, not every GUI toolkit in existence — prepended to subprocess test
# sources. Blocking at import time proves independence rather than relying on the
# package being absent. Callers assert on the raised message, e.g.
# "PySide6 is blocked for this test". Qt is the whole set that matters here: it is
# what `scripts/check_layering.py` contains, and PySide6 is the only GUI toolkit
# in requirements.txt.
#
# It deliberately does NOT claim to block a model backend:
# `mira/cognition/llm_client.py` reaches Ollama through `urllib.request` and
# `socket` from the standard library (llm_client.py:6-8,86), so naming requests
# or httpx here would block nothing this repository uses. Independence from the
# layers above is asserted directly instead, by the ABOVE_MEMORY and
# ABOVE_MESSAGING checks in `test_memory_messaging_boundaries.py`.
BLOCKER = """
import sys

BLOCKED = ("PySide6", "PyQt5", "PyQt6", "shiboken6")


class _Block:
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in BLOCKED:
            raise ImportError(f"{root} is blocked for this test")
        return None


sys.meta_path.insert(0, _Block())
"""


def run_python(source: str) -> subprocess.CompletedProcess[str]:
    """Run `source` in a fresh interpreter at the repository root.

    Fresh so nothing is pre-imported, which is what lets each subprocess be its
    own entry point. No `cwd` parameter: every call site wanted `REPO_ROOT`, so
    one of the two variants took it as an argument and always passed that.
    """
    return subprocess.run(
        [sys.executable, "-c", source], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )


def run_checker(cwd: Path, checker: Path) -> subprocess.CompletedProcess[str]:
    """Run the layering checker with `cwd` as its working directory.

    `checker` is required rather than defaulted to `CHECKER`. The checker derives
    its own root from its file location (`scripts/check_layering.py:46`), so the
    pair must move together: `run_checker(tmp_path)` with the repository copy
    would silently scan the real tree and ignore the module written into
    `tmp_path`. Making it explicit removes that shape entirely.
    """
    return subprocess.run(
        [sys.executable, str(checker)], cwd=cwd, capture_output=True, text=True, timeout=60
    )


def isolated_tree(tmp_path: Path, module_path: str, source: str) -> Path:
    """Build a throwaway tree holding the checker and one module under test.

    The checker derives its repository root from its own location, so copying it
    into `tmp_path` makes it scan only what is written here and leaves the real
    repository untouched. Returns the path to the copied checker.

    One module per tree: every call site passes a fresh pytest `tmp_path` and
    calls this once, and `mkdir()` without `exist_ok` keeps that explicit, as
    both of the variants this replaced did.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy(CHECKER, scripts_dir / "check_layering.py")

    target = tmp_path / module_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return scripts_dir / "check_layering.py"
