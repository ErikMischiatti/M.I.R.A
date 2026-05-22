from __future__ import annotations

from mira.cognition.rule_intent_engine import RuleIntentEngine
from mira.core.models import UserInput


def infer(text: str):
    return RuleIntentEngine().infer(UserInput(text=text))


def test_rule_engine_detects_open_directory_prefix():
    intent = infer("apri cartella download")

    assert intent.intent == "open_directory_request"
    assert intent.entities == {"directory": "download"}


def test_rule_engine_detects_common_directory_shortcut():
    intent = infer("apri documenti")

    assert intent.intent == "open_directory_request"
    assert intent.entities == {"directory": "documenti"}
