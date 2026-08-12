"""Repository-wide test isolation.

Only global isolation lives here. Shared test doubles are in `tests/doubles.py`
and the layering-boundary harness in `tests/layering_harness.py`, both imported
explicitly so a reader can see at the call site what a test depends on.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# `doubles`, `layering_harness` and this module are imported by test modules as
# top-level names. Under pytest's default `prepend` import mode this directory is
# already on sys.path; under `--import-mode=importlib` it is not, and collection
# fails. Inserting it here keeps both modes working, which the baseline supported
# and `test_the_suite_collects_under_the_importlib_import_mode` now pins.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Every MIRA_* variable the production code reads, at its os.getenv site:
#   MIRA_INTENT_ENGINE              mira/core/brain.py:88
#   MIRA_OLLAMA_MODEL               mira/cognition/llm_client.py:34
#   MIRA_OLLAMA_BASE_URL            mira/cognition/llm_client.py:39
#   MIRA_OLLAMA_TIMEOUT_S           mira/cognition/llm_client.py:104
#   MIRA_LLM_ACTION_MIN_CONFIDENCE  mira/cognition/llm_intent_engine.py:258
# `test_each_variable_is_read_at_the_line_it_is_attributed_to` checks all five.
MIRA_ENV_VARS = (
    "MIRA_INTENT_ENGINE",
    "MIRA_OLLAMA_MODEL",
    "MIRA_OLLAMA_BASE_URL",
    "MIRA_OLLAMA_TIMEOUT_S",
    "MIRA_LLM_ACTION_MIN_CONFIDENCE",
)


@pytest.fixture(autouse=True)
def isolate_mira_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every MIRA_* variable so no test inherits the developer's shell.

    Narrow by construction: it deletes exactly the five names in `MIRA_ENV_VARS`
    and sets nothing.

    It cannot invent behaviour. Deleting a variable selects the branch the code
    already takes when it is absent, and `test_absent_variables_select_the
    _production_defaults` asserts that for all five. It also cannot override a
    deliberate setting: `monkeypatch` in a test runs after this fixture, which
    `test_a_test_can_still_choose_its_own_value` asserts.

    What it does narrow is coverage of the parsing branches for a *set* value.
    Those are reached only by tests that opt in with `setenv` — five of them, in
    `test_llm_intent_engine.py` — rather than incidentally by an exported value.

    This is an invariant rather than a convenience. Before it existed, exporting
    `MIRA_LLM_ACTION_MIN_CONFIDENCE=0.99` broke two tests that read the threshold
    indirectly through the engine — `test_valid_llm_json_is_converted_to_intent
    _result` and `test_llm_action_validation_uses_supplied_action_metadata` —
    neither of which mentions the variable.
    """
    for name in MIRA_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
