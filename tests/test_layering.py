"""Tests for the architectural boundary introduced with mira/domain.

These cover the boundary itself rather than any product behaviour: that the
shared vocabulary is importable without a GUI toolkit, and that the layering
checker passes on the current tree while rejecting each class of forbidden
import.
"""

from __future__ import annotations

import importlib.util
import re

import pytest

from layering_harness import BLOCKER, CHECKER, REPO_ROOT, isolated_tree, run_checker, run_python

IMPORT_DOMAIN = BLOCKER + """
import mira.domain.models
import mira.domain.state

# The vocabulary must be usable, not merely importable.
state = mira.domain.state.FaceState.SPEAKING
response = mira.domain.models.BrainResponse(text="ok", face_state=state)
assert response.face_state is mira.domain.state.FaceState.SPEAKING
assert "PySide6" not in sys.modules
print("OK")
"""

IMPORT_PYSIDE6 = BLOCKER + """
import PySide6  # must raise
print("BLOCKER FAILED: PySide6 imported anyway")
"""

# (module path, source, expected message fragment, expected reported location)
REJECTION_CASES = [
    pytest.param(
        "mira/cognition/offender.py",
        "from mira.core.brain import Brain\n\nUSED = Brain\n",
        "[direction] mira.cognition must not import mira.core",
        "mira/cognition/offender.py:1",
        id="cognition-imports-orchestration",
    ),
    pytest.param(
        "mira/cognition/offender.py",
        "from mira.ui.debug_panel import DebugPanel\n\nUSED = DebugPanel\n",
        "[direction] mira.cognition must not import mira.ui",
        "mira/cognition/offender.py:1",
        id="cognition-imports-ui",
    ),
    pytest.param(
        "mira/domain/offender.py",
        "from PySide6.QtCore import QTimer\n\nUSED = QTimer\n",
        "[qt] mira.domain must not import a GUI toolkit",
        "mira/domain/offender.py:1",
        id="qt-in-domain",
    ),
    pytest.param(
        # Since the scheduler port there is no Qt exception for mira.core.
        "mira/core/offender.py",
        "from PySide6.QtCore import QTimer\n\nUSED = QTimer\n",
        "[qt] mira.core must not import a GUI toolkit",
        "mira/core/offender.py:1",
        id="qt-in-core-has-no-exception",
    ),
    pytest.param(
        # Adapters are the declared home for technology bindings.
        "mira/adapters/offender.py",
        "from mira.core.brain import Brain\n\nUSED = Brain\n",
        "[direction] mira.adapters must not import mira.core",
        "mira/adapters/offender.py:1",
        id="adapters-must-not-import-orchestration",
    ),
    pytest.param(
        "mira/perception/camera.py",
        "from mira.ui.debug_panel import DebugPanel\n\nUSED = DebugPanel\n",
        "[unclassified] mira.perception.camera belongs to no declared layer",
        "mira/perception/camera.py",
        id="unclassified-package",
    ),
    pytest.param(
        "mira/core/offender.py",
        "from ..ui.debug_panel import DebugPanel\n\nUSED = DebugPanel\n",
        "[relative] relative imports are not allowed in mira",
        "mira/core/offender.py:1",
        id="relative-import",
    ),
    pytest.param(
        "mira/cognition/offender.py",
        "from mira import ui\n\nUSED = ui\n",
        "[direction] mira.cognition must not import mira.ui",
        "mira/cognition/offender.py:1",
        id="from-mira-import-submodule",
    ),
    pytest.param(
        # A whole mira/main/ package must not inherit main.py's exemption.
        "mira/main/hidden.py",
        "from mira.ui.debug_panel import DebugPanel\n\nUSED = DebugPanel\n",
        "[unclassified] mira.main.hidden belongs to no declared layer",
        "mira/main/hidden.py",
        id="main-package-is-not-exempt",
    ),
    pytest.param(
        # Multi-alias import: one statement, one finding, but two targets.
        "mira/cognition/offender.py",
        "import mira.ui.debug_panel, mira.ui.chat_panel\n\nUSED = 1\n",
        "[direction] mira.cognition must not import mira.ui",
        "mira/cognition/offender.py:1",
        id="multi-alias-import",
    ),
]


def load_checker():
    """Import the checker as a module, to read its declared layer tables.

    Local: this module is its only consumer, so it stays out of the shared
    harness. `_check_layering` is a throwaway name — the checker is never on
    sys.path as an importable module.
    """
    spec = importlib.util.spec_from_file_location("_check_layering", CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_blocker_covers_exactly_the_toolkits_the_checker_rejects():
    """`BLOCKER` and the checker must agree on what counts as a GUI toolkit.

    If they drifted, a test could import a toolkit the checker forbids and still
    pass, or the blocker could reject something the rule permits.
    """
    blocked = re.search(r"BLOCKED = \((.*?)\)", BLOCKER, re.S).group(1)
    names = tuple(re.findall(r'"([^"]+)"', blocked))

    assert names == load_checker().QT_ROOTS


def test_domain_vocabulary_imports_without_pyside6():
    result = run_python(IMPORT_DOMAIN)
    assert result.returncode == 0, (
        f"domain vocabulary requires PySide6.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip().endswith("OK")


def test_pyside6_block_actually_blocks():
    """Guard the guard: the blocker must really prevent the import."""
    if importlib.util.find_spec("PySide6") is None:
        pytest.skip("PySide6 not installed; blocker cannot be exercised")

    blocked = run_python(IMPORT_PYSIDE6)
    assert blocked.returncode != 0, "the blocker did not prevent importing PySide6"
    assert "PySide6 is blocked for this test" in blocked.stderr
    assert "BLOCKER FAILED" not in blocked.stdout


def test_layering_checker_passes_on_current_tree():
    result = run_checker(REPO_ROOT, CHECKER)
    assert result.returncode == 0, (
        f"layering checker failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Layering OK" in result.stdout


def test_current_tree_declares_no_exceptions():
    """The debt is zero; pin it so a regression cannot reintroduce any.

    With no exceptions the checker prints no exception block at all, so the
    count is asserted through the success line instead.
    """
    result = run_checker(REPO_ROOT, CHECKER)
    assert result.returncode == 0
    assert "(0 declared exceptions)" in result.stdout, (
        "layering debt reappeared; every exception needs a justification.\n"
        f"{result.stdout}"
    )
    assert "Declared exceptions" not in result.stdout

    # The reported count only covers edges that are already disallowed, so a
    # non-applicable debt entry would go unreported. Assert the dicts directly.
    checker = load_checker()
    assert checker.DIRECTION_DEBT == {}
    assert checker.QT_DEBT == {}


@pytest.mark.parametrize("module_path,source,expected,location", REJECTION_CASES)
def test_layering_checker_rejects(tmp_path, module_path, source, expected, location):
    checker = isolated_tree(tmp_path, module_path, source)
    result = run_checker(tmp_path, checker)
    assert result.returncode == 1, f"expected a violation.\nstdout: {result.stdout}"
    assert expected in result.stderr
    # Diagnostics must name the file (and line, where a statement is at fault).
    assert location in result.stderr


def test_checker_reports_one_finding_per_import_statement(tmp_path):
    """A multi-alias statement must not multiply into several findings.

    `import a, b` yields one entry per alias, so this is the input
    that actually exercises deduplication. A `from x import a, b` statement
    would pass even without it.
    """
    checker = isolated_tree(
        tmp_path,
        "mira/cognition/offender.py",
        "import mira.ui.debug_panel, mira.ui.chat_panel\n\nUSED = 1\n",
    )
    result = run_checker(tmp_path, checker)
    assert result.returncode == 1
    assert result.stderr.count("[direction] mira.cognition must not import mira.ui") == 1
    assert "Layering violations (1)" in result.stderr


def test_checker_rejects_symlinked_package(tmp_path):
    """A symlinked directory stays importable but is invisible to rglob."""
    checker = isolated_tree(
        tmp_path, "mira/cognition/ok.py", "from mira.domain.models import UserInput\n"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.py").write_text(
        "from mira.ui.debug_panel import DebugPanel\n", encoding="utf-8"
    )
    (tmp_path / "mira" / "cognition" / "plugins").symlink_to(outside, target_is_directory=True)

    result = run_checker(tmp_path, checker)
    assert result.returncode == 1
    assert "[symlink] directory symlinks under mira/ are not allowed" in result.stderr
    assert "mira/cognition/plugins" in result.stderr


def test_no_domain_package_imports_the_ui_layer():
    """Direct assertion of the outcome, independent of the checker."""
    offenders = []
    # Derived from the checker so a new layer cannot be silently omitted.
    packages = sorted(
        layer.split(".", 1)[1]
        for layer in load_checker().LAYER_IMPORTS
        if layer != "mira" and layer != "mira.ui"
    )
    for package in packages:
        if not (REPO_ROOT / "mira" / package).is_dir():
            continue
        for path in sorted((REPO_ROOT / "mira" / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.lstrip()
                static = "mira.ui" in line and stripped.startswith(("import ", "from "))
                dynamic = "mira.ui" in line and (
                    "import_module(" in line or "__import__(" in line
                )
                if static or dynamic:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert offenders == [], "domain packages must not import mira.ui:\n" + "\n".join(offenders)
