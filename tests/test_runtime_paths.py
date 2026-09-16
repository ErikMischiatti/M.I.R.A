from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import mira

from mira.actions.desktop_actions import (
    DesktopPaths,
    make_get_project_path_action,
    make_open_app_action,
    make_open_directory_action,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _desktop_paths(
    *,
    home: Path,
    invocation_directory: Path,
    project_root: Path | None,
) -> DesktopPaths:
    return DesktopPaths(
        home=home.resolve(),
        invocation_directory=invocation_directory.resolve(),
        project_root=project_root.resolve() if project_root is not None else None,
    )


def test_desktop_path_discovery_is_stable_across_the_invocation_cwd(
    monkeypatch,
    tmp_path: Path,
):
    package_root = Path(mira.__file__).resolve().parent
    expected_project_root = package_root.parent

    before_chdir = DesktopPaths.discover()
    monkeypatch.chdir(tmp_path)
    from_arbitrary_cwd = DesktopPaths.discover()

    assert before_chdir.project_root == expected_project_root
    assert from_arbitrary_cwd.project_root == expected_project_root
    assert from_arbitrary_cwd.invocation_directory == tmp_path.resolve()


def test_installed_package_without_checkout_markers_has_no_project_root(tmp_path: Path):
    package_root = tmp_path / "site-packages" / "mira"
    package_root.mkdir(parents=True)

    paths = DesktopPaths.discover(package_root=package_root)

    assert paths.project_root is None


def test_source_checkout_is_recognized_only_at_the_package_direct_parent(tmp_path: Path):
    project_root = tmp_path / "checkout"
    package_root = project_root / "mira"
    launcher = project_root / "bin" / "mira"
    package_root.mkdir(parents=True)
    launcher.parent.mkdir()
    (project_root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    paths = DesktopPaths.discover(package_root=package_root)

    assert paths.project_root == project_root.resolve()


def test_get_project_path_uses_the_explicit_development_root_not_cwd(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    project = tmp_path / "checkout"
    elsewhere = tmp_path / "elsewhere"
    for directory in (home, project, elsewhere):
        directory.mkdir()
    paths = _desktop_paths(
        home=home,
        invocation_directory=elsewhere,
        project_root=project,
    )
    handler = make_get_project_path_action(paths)

    monkeypatch.chdir(home)
    result = handler({})

    assert result.success is True
    assert result.data["path"] == str(project.resolve())


def test_get_project_path_fails_clearly_without_a_development_checkout(tmp_path: Path):
    home = tmp_path / "home"
    invocation = tmp_path / "invocation"
    home.mkdir()
    invocation.mkdir()
    paths = _desktop_paths(
        home=home,
        invocation_directory=invocation,
        project_root=None,
    )

    result = make_get_project_path_action(paths)({})

    assert result.success is False
    assert result.data["reason"] == "project_root_unavailable"


def test_project_directory_is_an_explicit_allowed_root(tmp_path: Path):
    home = tmp_path / "home"
    project = tmp_path / "checkout"
    invocation = tmp_path / "elsewhere"
    for directory in (home, project, invocation):
        directory.mkdir()
    paths = _desktop_paths(
        home=home,
        invocation_directory=invocation,
        project_root=project,
    )

    with patch(
        "mira.actions.desktop_actions.shutil.which",
        return_value="/usr/bin/xdg-open",
    ):
        with patch("mira.actions.desktop_actions.subprocess.Popen") as popen:
            result = make_open_directory_action(paths)({"directory": "project"})

    assert result.success is True
    assert result.data["path"] == str(project.resolve())
    assert popen.call_args.args[0] == ["/usr/bin/xdg-open", str(project.resolve())]


def test_arbitrary_invocation_directory_does_not_widen_the_allowed_boundary(
    tmp_path: Path,
):
    home = tmp_path / "home"
    project = tmp_path / "checkout"
    outside = tmp_path / "outside"
    for directory in (home, project, outside):
        directory.mkdir()
    paths = _desktop_paths(
        home=home,
        invocation_directory=outside,
        project_root=project,
    )

    result = make_open_directory_action(paths)({"directory": "current"})

    assert result.success is False
    assert result.data["requested_directory"] == "current"


def test_symlink_cannot_escape_an_allowed_user_directory(tmp_path: Path):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    invocation = home / "workspace"
    invocation.mkdir(parents=True)
    outside.mkdir()
    escape = home / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    paths = _desktop_paths(
        home=home,
        invocation_directory=invocation,
        project_root=None,
    )

    result = make_open_directory_action(paths)({"directory": str(escape)})

    assert result.success is False


def test_current_and_relative_paths_use_the_captured_invocation_directory(
    monkeypatch,
    tmp_path: Path,
):
    home = tmp_path / "home"
    invocation = home / "workspace"
    child = invocation / "notes"
    later_cwd = tmp_path / "later"
    child.mkdir(parents=True)
    later_cwd.mkdir()
    paths = _desktop_paths(
        home=home,
        invocation_directory=invocation,
        project_root=None,
    )
    handler = make_open_directory_action(paths)
    monkeypatch.chdir(later_cwd)

    with patch(
        "mira.actions.desktop_actions.shutil.which",
        return_value="/usr/bin/xdg-open",
    ):
        with patch("mira.actions.desktop_actions.subprocess.Popen") as popen:
            current_result = handler({"directory": "current"})
            relative_result = handler({"directory": "notes"})

    assert current_result.data["path"] == str(invocation.resolve())
    assert relative_result.data["path"] == str(child.resolve())
    assert [call.args[0] for call in popen.call_args_list] == [
        ["/usr/bin/xdg-open", str(invocation.resolve())],
        ["/usr/bin/xdg-open", str(child.resolve())],
    ]


def test_tilde_path_uses_the_explicit_user_home(tmp_path: Path):
    home = tmp_path / "selected-home"
    documents = home / "Documents"
    invocation = tmp_path / "invocation"
    documents.mkdir(parents=True)
    invocation.mkdir()
    paths = _desktop_paths(
        home=home,
        invocation_directory=invocation,
        project_root=None,
    )

    with patch(
        "mira.actions.desktop_actions.shutil.which",
        return_value="/usr/bin/xdg-open",
    ):
        with patch("mira.actions.desktop_actions.subprocess.Popen") as popen:
            result = make_open_directory_action(paths)({"directory": "~/Documents"})

    assert result.success is True
    assert popen.call_args.args[0] == ["/usr/bin/xdg-open", str(documents.resolve())]


def test_file_manager_opens_the_user_home_not_process_cwd(tmp_path: Path):
    home = tmp_path / "home"
    invocation = tmp_path / "invocation"
    home.mkdir()
    invocation.mkdir()
    paths = _desktop_paths(
        home=home,
        invocation_directory=invocation,
        project_root=None,
    )

    with patch(
        "mira.actions.desktop_actions.shutil.which",
        return_value="/usr/bin/xdg-open",
    ):
        with patch("mira.actions.desktop_actions.subprocess.Popen") as popen:
            result = make_open_app_action(paths)({"app_name": "files"})

    assert result.success is True
    assert popen.call_args.args[0] == ["xdg-open", str(home.resolve())]


def test_launcher_preserves_the_callers_working_directory(tmp_path: Path):
    fake_checkout = tmp_path / "checkout"
    launcher = fake_checkout / "bin" / "mira"
    python_bin = fake_checkout / "venv" / "bin" / "python"
    caller = tmp_path / "caller"
    launcher.parent.mkdir(parents=True)
    python_bin.parent.mkdir(parents=True)
    caller.mkdir()
    shutil.copy2(REPO_ROOT / "bin" / "mira", launcher)
    python_bin.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$PWD\"\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    python_bin.chmod(0o755)

    result = subprocess.run(
        [str(launcher), "--probe"],
        cwd=caller,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        str(caller),
        "-m",
        "mira.main",
        "--probe",
    ]
