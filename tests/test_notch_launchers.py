"""Repository launchers: MIRA and MIRA-NOTCH run the application, MIRA-TUNE the tuner.

Each launcher is run for real, through a symlink as on PATH, inside a copy of
the repository layout whose virtual-environment interpreter is a stub that
reports how it was invoked. That checks symlink resolution, the module and
the arguments without starting Qt.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from mira.ui.notch.__main__ import parse_arguments


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin"

LAUNCHERS = {
    "MIRA": ("mira", ["-m", "mira.main"]),
    "MIRA-NOTCH": ("mira-notch", ["-m", "mira.main", "--notch"]),
    "MIRA-TUNE": ("mira-tune", ["-m", "mira.ui.notch", "--tune"]),
}

STUB_PYTHON = """#!/usr/bin/env bash
printf '%s\\n' "$@"
"""


@pytest.fixture
def fake_repo(tmp_path) -> pathlib.Path:
    repo = tmp_path / "checkout"
    (repo / "bin").mkdir(parents=True)
    for script, _ in LAUNCHERS.values():
        shutil.copy2(BIN / script, repo / "bin" / script)
    python = repo / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(STUB_PYTHON)
    python.chmod(0o755)
    return repo


def run_through_symlink(target: pathlib.Path, name: str, tmp_path: pathlib.Path, *args: str):
    path_dir = tmp_path / "local-bin"
    path_dir.mkdir(exist_ok=True)
    link = path_dir / name
    link.symlink_to(target)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir(exist_ok=True)
    return subprocess.run(
        [str(link), *args], cwd=elsewhere, capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )


@pytest.mark.parametrize("command", list(LAUNCHERS))
def test_each_launcher_is_an_executable_bash_script(command):
    script = BIN / LAUNCHERS[command][0]

    assert script.is_file() and os.access(script, os.X_OK)
    assert script.read_text().startswith("#!/usr/bin/env bash")
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


@pytest.mark.parametrize("command", list(LAUNCHERS))
def test_each_launcher_runs_its_module_through_a_symlink_from_anywhere(command, fake_repo, tmp_path):
    script, expected = LAUNCHERS[command]

    result = run_through_symlink(fake_repo / "bin" / script, command, tmp_path, "--config", "/x/notch.json")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [*expected, "--config", "/x/notch.json"]


@pytest.mark.parametrize("command", list(LAUNCHERS))
def test_each_launcher_explains_a_missing_virtual_environment(command, fake_repo, tmp_path):
    shutil.rmtree(fake_repo / "venv")

    result = run_through_symlink(fake_repo / "bin" / LAUNCHERS[command][0], command, tmp_path)

    assert result.returncode == 1
    assert f"{command}: no virtual environment" in result.stderr


def test_mira_runs_the_application_entry_point_without_a_notch():
    # `mira.main` without --notch opens the main window only (`DesktopShell`
    # in MAIN_WINDOW mode); the launcher starts nothing else.
    text = (BIN / "mira").read_text()

    assert 'exec "$python_bin" -m mira.main "$@"' in text
    commands = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any("--notch" in line or "mira.ui.notch" in line for line in commands)


def test_mira_notch_runs_the_same_application_entry_point_in_notch_mode():
    # One runtime with the notch as its first surface, not the standalone
    # tool: double-clicking it opens this process's main window.
    text = (BIN / "mira-notch").read_text()

    assert 'exec "$python_bin" -m mira.main --notch "$@"' in text
    assert "mira.ui.notch" not in text


def test_the_runtime_mode_flags():
    from mira.main import parse_arguments as runtime_arguments

    assert runtime_arguments([])[0].notch is False
    assert runtime_arguments(["--notch"])[0].notch is True
    arguments, rest = runtime_arguments(["--notch", "--config", "/x/n.json", "-platform", "offscreen"])
    assert arguments.config == pathlib.Path("/x/n.json")
    # Everything else is Qt's.
    assert rest == ["-platform", "offscreen"]


def test_the_standalone_notch_tools_never_load_the_application_window():
    # MIRA-TUNE (and the tool's --notch-only mode) build their own embodiment
    # and notch; neither may depend on MainWindow or the application's shell.
    source = """
import sys
from PySide6.QtWidgets import QApplication
app = QApplication([])
from mira.ui.notch.__main__ import build_notch_environment
for tuning in (False, True):
    environment = build_notch_environment(tuning=tuning)
    environment.notch.close()
    if environment.debug is not None:
        environment.debug.close()
loaded = sorted(name for name in sys.modules
                if name in {"mira.ui.main_window", "mira.ui.desktop_shell", "mira.application.composition"})
print("LOADED", loaded)
"""
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )

    assert result.returncode == 0, result.stderr
    assert "LOADED []" in result.stdout


def test_the_mode_flags():
    assert parse_arguments([]).tuning is True
    assert parse_arguments(["--tune"]).tuning is True
    assert parse_arguments(["--notch-only"]).tuning is False
    assert parse_arguments(["--config", "/tmp/n.json"]).config == pathlib.Path("/tmp/n.json")
    with pytest.raises(SystemExit):
        parse_arguments(["--tune", "--notch-only"])


@pytest.mark.parametrize(("script", "options"), [
    ("mira-notch", ("--notch", "--config")),
    ("mira-tune", ("--notch-only", "--tune")),
])
def test_the_real_launchers_reach_their_entry_points(script, options):
    venv_python = REPO_ROOT / "venv" / "bin" / "python"
    if not os.access(venv_python, os.X_OK):
        pytest.skip("no repository virtual environment")

    result = subprocess.run(
        [str(BIN / script), "--help"], capture_output=True, text=True, timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )

    assert result.returncode == 0, result.stderr
    assert all(option in result.stdout for option in options)
