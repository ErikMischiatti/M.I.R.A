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


def test_rule_engine_detects_project_path_query():
    for text in [
        "qual è la cartella del progetto?",
        "mostrami il path del progetto",
        "path del progetto",
        "dove si trova il progetto?",
        "qual è il project path?",
        "mostra la directory del progetto",
        "cartella progetto",
        "dimmi la cartella del progetto",
        "dove è salvato il progetto?",
        "dove si trova MIRA?",
        "dove si trova M.I.R.A.?",
        "mostrami la root del progetto",
        "project root",
        "project directory",
        "show project path",
        "show project directory",
        "where is the project?",
        "where is the project folder?",
        "what is the project path?",
        "what is the project directory?",
    ]:
        intent = infer(text)

        assert intent.intent == "project_path_query"
        assert intent.entities == {}


def test_rule_engine_does_not_treat_open_project_folder_as_project_path_query():
    intent = infer("apri la cartella del progetto")

    assert intent.intent != "project_path_query"


def test_rule_engine_keeps_destructive_project_phrases_unsupported():
    for text in [
        "cambia cartella del progetto",
        "cancella la cartella del progetto",
        "sposta il progetto",
        "rinomina la cartella del progetto",
        "elimina il progetto",
        "esegui ls nella cartella progetto",
        "scrivi un file nella cartella del progetto",
    ]:
        intent = infer(text)

        assert intent.intent == "unknown"
        assert intent.entities == {}


def test_rule_engine_routes_explicit_url_like_schemes_to_open_url():
    for text, expected_url in [
        ("apri file:///etc/passwd", "file:///etc/passwd"),
        ("apri javascript:alert(1)", "javascript:alert(1)"),
        ("apri ftp://example.com", "ftp://example.com"),
    ]:
        intent = infer(text)

        assert intent.intent == "open_url_request"
        assert intent.entities == {"url": expected_url}


def test_rule_engine_keeps_unknown_for_unsupported_local_command():
    intent = infer("cancella il file temporaneo")

    assert intent.intent == "unknown"
