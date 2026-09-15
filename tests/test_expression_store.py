from __future__ import annotations

from pathlib import Path

import mira

from mira.domain.state import FaceState
from mira.ui.face.expression_store import (
    EXPRESSIONS_FILE,
    load_expression_library,
    save_expression_library,
)


def test_default_profiles_are_loaded_from_the_installed_package_from_any_cwd(
    monkeypatch,
    tmp_path: Path,
):
    package_root = Path(mira.__file__).resolve().parent
    expected_resource = package_root / "config" / "expression_profiles.json"

    monkeypatch.chdir(package_root.parent)
    repository_cwd_library = load_expression_library()
    monkeypatch.chdir(tmp_path)
    arbitrary_cwd_library = load_expression_library()

    assert EXPRESSIONS_FILE == expected_resource
    assert EXPRESSIONS_FILE.is_absolute()
    assert EXPRESSIONS_FILE.is_file()
    assert {
        state: profile.to_dict()
        for state, profile in arbitrary_cwd_library.items()
    } == {
        state: profile.to_dict()
        for state, profile in repository_cwd_library.items()
    }
    assert arbitrary_cwd_library[FaceState.IDLE].width_scale == 0.85


def test_expression_profiles_save_and_reload_through_an_explicit_writable_path(
    tmp_path: Path,
):
    active_profiles = tmp_path / "config" / "expression_profiles.json"
    library = load_expression_library()
    library[FaceState.HAPPY].offset_y = 37.0

    save_expression_library(library, active_profiles)
    reloaded = load_expression_library(active_profiles)

    assert active_profiles.is_file()
    assert reloaded[FaceState.HAPPY].offset_y == 37.0
    assert reloaded[FaceState.IDLE].to_dict() == library[FaceState.IDLE].to_dict()
