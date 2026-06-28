from __future__ import annotations

import re

_NAME_PREFIX_PATTERNS = [
    re.compile(r"^mi chiamo\s+(.+)$", re.IGNORECASE),
    re.compile(r"^il mio nome [eè]\s+(.+)$", re.IGNORECASE),
    re.compile(r"^sono\s+(.+)$", re.IGNORECASE),
    re.compile(r"^chiamami\s+(.+)$", re.IGNORECASE),
    re.compile(r"^my name is\s+(.+)$", re.IGNORECASE),
    re.compile(r"^call me\s+(.+)$", re.IGNORECASE),
]

_REJECTED_SINGLE_WORDS = {
    "qui",
    "qua",
    "stanco",
    "stanca",
    "felice",
    "triste",
    "pronto",
    "pronta",
    "ok",
    "fine",
    "here",
    "tired",
    "happy",
    "ready",
}

_NAME_CHARS = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]{0,39}$")
_SENTENCE_CONTINUATION_WORDS = {"e", "and", "ma", "but", "che", "who", "which"}


def extract_user_name(text: str) -> str | None:
    """Extract a conservative first-name-like value from simple name statements."""
    normalized_text = " ".join(text.strip().split())
    if not normalized_text:
        return None

    for pattern in _NAME_PREFIX_PATTERNS:
        match = pattern.match(normalized_text)
        if not match:
            continue
        return normalize_user_name(match.group(1))

    return None


def normalize_user_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    cleaned = " ".join(value.strip().split())
    cleaned = cleaned.strip(" \t\r\n.,;:!?()[]{}\"“”‘’")
    if not cleaned:
        return None

    parts = cleaned.split()
    if len(parts) > 1 and parts[1].lower() in _SENTENCE_CONTINUATION_WORDS:
        return None

    first_token = parts[0].strip(".,;:!?()[]{}\"“”‘’")
    if not first_token or len(first_token) > 40:
        return None

    if first_token.lower() in _REJECTED_SINGLE_WORDS:
        return None

    if not _NAME_CHARS.match(first_token):
        return None

    return first_token
