"""Tests for the memory and messaging layer boundaries.

These cover the boundary rather than the behaviour: that both packages are
importable with no GUI toolkit and drag in no layer above themselves, that the
layering checker accepts the intended inward dependencies and rejects the
outward ones, and that the import cycle the lazy `mira/core/__init__.py` shim
used to work around is genuinely gone.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "scripts" / "check_layering.py"

# Blocks every GUI toolkit so the import proves independence rather than relying
# on the environment. It deliberately does NOT claim to block a model backend:
# mira.cognition.llm_client uses urllib and socket from the standard library, so
# naming requests/httpx here would block nothing this repo uses. Independence
# from the layers above is asserted directly instead, via ABOVE_MEMORY below.
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

IMPORT_MEMORY = BLOCKER + """
from mira.domain.models import BrainResponse, IntentResult, UserInput
from mira.domain.state import FaceState
from mira.memory.session_memory import MemoryMessage, SessionMemory

memory = SessionMemory(max_history=2)
memory.add_user_input(UserInput(text="ciao"))
memory.add_response(BrainResponse(text="hi", face_state=FaceState.HAPPY))
memory.set_last_intent(IntentResult(intent="greeting"))
memory.set_context("name", "Erik")

assert [m.role for m in memory.history] == ["user", "assistant"]
assert memory.get_context("name") == "Erik"
assert isinstance(memory.get_recent_history()[0], MemoryMessage)

# Nothing above memory in the layering may have been dragged in.
ABOVE_MEMORY = ("cognition", "actions", "core", "adapters", "ui", "messaging")
loaded = sorted(
    m for m in sys.modules
    if m.startswith("mira.") and m.split(".")[1] in ABOVE_MEMORY
)
assert loaded == [], loaded
print("OK")
"""

IMPORT_MESSAGING = BLOCKER + """
from mira.messaging.events import EventBus

seen = []
bus = EventBus()
bus.subscribe("e", seen.append)
bus.emit("e", 1)

assert seen == [1]

# messaging sits below everything, including domain.
ABOVE_MESSAGING = ("domain", "memory", "cognition", "actions", "core", "adapters", "ui")
loaded = sorted(
    m for m in sys.modules
    if m.startswith("mira.") and m.split(".")[1] in ABOVE_MESSAGING
)
assert loaded == [], loaded
print("OK")
"""

# The cycle only bites for some import orders, so each entry point is checked in
# its own interpreter with nothing pre-imported.
CYCLE_ENTRY_POINTS = [
    "mira.core",
    "mira.core.brain",
    "mira.cognition.session_context_builder",
    "mira.cognition.llm_intent_engine",
    "mira.memory.session_memory",
    "mira.messaging.events",
    "mira.actions.builtin_actions",
    "mira.ui.main_window",
]

# (case id, module path, source, expected message fragment)
REJECTION_CASES = [
    pytest.param(
        "mira/memory/offender.py",
        "from mira.core.brain import Brain\n\nUSED = Brain\n",
        "[direction] mira.memory must not import mira.core",
        id="memory-must-not-import-orchestration",
    ),
    pytest.param(
        "mira/memory/offender.py",
        "from mira.messaging.events import EventBus\n\nUSED = EventBus\n",
        "[direction] mira.memory must not import mira.messaging",
        id="memory-must-not-import-messaging",
    ),
    pytest.param(
        "mira/messaging/offender.py",
        "from mira.domain.models import UserInput\n\nUSED = UserInput\n",
        "[direction] mira.messaging must not import mira.domain",
        id="messaging-must-not-import-domain",
    ),
    pytest.param(
        "mira/messaging/offender.py",
        "from mira.memory.session_memory import SessionMemory\n\nUSED = SessionMemory\n",
        "[direction] mira.messaging must not import mira.memory",
        id="messaging-must-not-import-memory",
    ),
    pytest.param(
        # Cognition interprets; it must not be able to emit. mira/AGENTS.md
        # forbids the worker phase emitting events, and withholding this
        # allowance is what makes that mechanical rather than advisory.
        "mira/cognition/offender.py",
        "from mira.messaging.events import EventBus\n\nUSED = EventBus\n",
        "[direction] mira.cognition must not import mira.messaging",
        id="cognition-must-not-import-messaging",
    ),
    pytest.param(
        "mira/memory/offender.py",
        "from PySide6.QtCore import QTimer\n\nUSED = QTimer\n",
        "[qt] mira.memory must not import a GUI toolkit",
        id="no-qt-in-memory",
    ),
    pytest.param(
        "mira/messaging/offender.py",
        "from PySide6.QtCore import QTimer\n\nUSED = QTimer\n",
        "[qt] mira.messaging must not import a GUI toolkit",
        id="no-qt-in-messaging",
    ),
]

# Dependencies that must be permitted, so the rules are not merely restrictive.
ACCEPTANCE_CASES = [
    pytest.param(
        "mira/memory/ok.py",
        "from mira.domain.models import UserInput\n\nUSED = UserInput\n",
        id="memory-may-import-domain",
    ),
    pytest.param(
        "mira/cognition/ok.py",
        "from mira.memory.session_memory import SessionMemory\n\nUSED = SessionMemory\n",
        id="cognition-may-import-memory",
    ),
    pytest.param(
        "mira/actions/ok.py",
        "from mira.messaging.events import EventBus\n\nUSED = EventBus\n",
        id="actions-may-import-messaging",
    ),
    pytest.param(
        "mira/core/ok.py",
        "from mira.memory.session_memory import SessionMemory\n"
        "from mira.messaging.events import EventBus\n\nUSED = (SessionMemory, EventBus)\n",
        id="core-may-import-both",
    ),
]


def run_python(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def run_checker(cwd: Path, checker: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(checker)], cwd=cwd, capture_output=True, text=True, timeout=60
    )


def isolated_tree(tmp_path: Path, module_path: str, source: str) -> Path:
    """Build a throwaway tree with the checker and one module under test."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy(CHECKER, scripts_dir / "check_layering.py")

    target = tmp_path / module_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return scripts_dir / "check_layering.py"


def test_memory_works_without_a_gui_toolkit_and_pulls_in_no_layer_above_it():
    result = run_python(IMPORT_MEMORY)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip().endswith("OK")


def test_messaging_works_without_a_gui_toolkit_and_pulls_in_no_layer_above_it():
    result = run_python(IMPORT_MESSAGING)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.strip().endswith("OK")


def test_the_blocker_actually_blocks():
    """Guard the guard, so the two tests above cannot pass vacuously."""
    result = run_python(BLOCKER + "import PySide6\n")
    assert result.returncode != 0
    assert "PySide6 is blocked for this test" in result.stderr


@pytest.mark.parametrize("entry_point", CYCLE_ENTRY_POINTS)
def test_no_import_cycle_from_any_entry_point(entry_point):
    """Each in a fresh interpreter: a cycle only bites for some import orders."""
    result = run_python(f"import {entry_point}\nprint('OK')\n")
    assert result.returncode == 0, f"{entry_point}: {result.stderr}"
    assert result.stdout.strip().endswith("OK")



@pytest.mark.parametrize("module_path,source,expected", REJECTION_CASES)
def test_checker_rejects_outward_dependencies(tmp_path, module_path, source, expected):
    checker = isolated_tree(tmp_path, module_path, source)
    result = run_checker(tmp_path, checker)
    assert result.returncode == 1, f"expected a violation.\nstdout: {result.stdout}"
    assert expected in result.stderr


@pytest.mark.parametrize("module_path,source", ACCEPTANCE_CASES)
def test_checker_accepts_intended_inward_dependencies(tmp_path, module_path, source):
    checker = isolated_tree(tmp_path, module_path, source)
    result = run_checker(tmp_path, checker)
    assert result.returncode == 0, f"should be allowed.\nstderr: {result.stderr}"


def test_no_module_references_the_old_locations():
    """The relocation must leave no stale import path behind, anywhere."""
    offenders = []
    searched = [REPO_ROOT / "mira", REPO_ROOT / "tests", REPO_ROOT / "scripts"]
    for path in sorted(p for root in searched for p in root.rglob("*.py")):
        if "__pycache__" in path.parts or path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8")
        for stale in ("mira.core.session_memory", "mira.core.events"):
            if stale in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {stale}")

    assert offenders == [], "stale module paths remain:\n" + "\n".join(offenders)
