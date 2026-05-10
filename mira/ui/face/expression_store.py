import json
from pathlib import Path

from nero.ui.face.expression_library import DEFAULT_EXPRESSION_LIBRARY, make_expression_library
from nero.ui.face.expression_profile import ExpressionProfile
from nero.ui.face.face_state import FaceState


EXPRESSIONS_FILE = Path("nero/config/expression_profiles.json")


def save_expression_library(library: dict[FaceState, ExpressionProfile], filepath: Path = EXPRESSIONS_FILE):
    filepath.parent.mkdir(parents=True, exist_ok=True)

    serialized = {
        state.name: profile.to_dict()
        for state, profile in library.items()
    }

    with filepath.open("w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2)


def load_expression_library(filepath: Path = EXPRESSIONS_FILE):
    library = make_expression_library()

    if not filepath.exists():
        return library

    with filepath.open("r", encoding="utf-8") as f:
        raw_data = json.load(f)

    for state_name, profile_data in raw_data.items():
        try:
            state = FaceState[state_name]
            library[state] = ExpressionProfile.from_dict(profile_data)
        except KeyError:
            continue

    return library


def reset_expression(state: FaceState):
    return ExpressionProfile.from_dict(DEFAULT_EXPRESSION_LIBRARY[state].to_dict())