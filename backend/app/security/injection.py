"""Detect instruction-like text in questions and in untrusted tool data (T1, T2).

One detector serves the input guard (the user's question) and the scan of ERP free text that
tools return as {"untrusted_text": ...}. It is a pattern check, not a classifier: it names the
rule that matched and never returns or logs the text itself. Text is NFKC-normalised first, so
full-width letters match, and invisible format characters count as a hit on their own.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

# Security: bounds the work on untrusted text; longer text is flagged without scanning.
MAX_SCAN_CHARS = 4000
MAX_SCAN_DEPTH = 8
MAX_HITS = 50

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
            r"\b(previous|prior|above|earlier|all|your|the)\b[^.\n]{0,20}"
            r"\b(instructions?|rules?|prompts?|guidelines?)\b"
        ),
    ),
    ("persona", re.compile(r"\byou are now\b|\bact as (an?|the)\b|\bpretend to be\b")),
    ("prompt_leak", re.compile(r"\b(system|developer) (prompt|message|instructions?)\b")),
    ("role_tag", re.compile(r"<\|?\s*(system|assistant|developer)\s*\|?>|#{2,}\s*system\b")),
    (
        "role_claim",
        re.compile(
            r"\b(i am|i'm|as) (the |an? )?(director|admin|administrator|auditor|risk manager)\b"
        ),
    ),
    ("hide", re.compile(r"\b(do not|don't|never) (mention|reveal|tell|report)\b")),
    (
        "steer_rating",
        re.compile(
            r"\b(mark|rate|label|classify|score)\b[^.\n]{0,30}"
            r"\b(as )?(low[ -]risk|green|safe|no risk)\b"
        ),
    ),
    ("encoded", re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")),
)


@dataclass(frozen=True)
class InjectionHit:
    """Instruction-like text found in untrusted data (no text, only where and which rule).

    Attributes:
        field: Name of the field that held the text (e.g. buyer_notes).
        rule: Name of the rule that matched.
    """

    field: str
    rule: str


def _has_hidden_characters(text: str) -> bool:
    """Return True for invisible format characters (zero-width, bidi, tag characters)."""
    return any(unicodedata.category(ch) == "Cf" for ch in text)


def find_instruction_like(text: str) -> str | None:
    """Return the name of the first rule that matches, or None for ordinary text.

    Args:
        text: Untrusted text (a question or a field from tool data).

    Returns:
        A rule name such as "override", "hidden_characters" or "too_long", or None.
    """
    if len(text) > MAX_SCAN_CHARS:
        return "too_long"
    if _has_hidden_characters(text):
        return "hidden_characters"
    normalised = unicodedata.normalize("NFKC", text).lower()
    for name, pattern in _RULES:
        if pattern.search(normalised):
            return name
    return None


def scan_untrusted(data: object) -> list[InjectionHit]:
    """Return a hit for every untrusted_text field in tool data that looks like instructions.

    Tools wrap ERP free text as {"untrusted_text": ...}; the field name is the key that holds
    that wrapper. Nesting deeper than MAX_SCAN_DEPTH is itself reported (rule too_deep).

    Args:
        data: A tool result's data (dicts and lists of JSON values).

    Returns:
        Hits in document order, at most MAX_HITS.
    """
    hits: list[InjectionHit] = []
    _scan(data, "data", 0, hits)
    return hits[:MAX_HITS]


def _scan(value: object, field: str, depth: int, hits: list[InjectionHit]) -> None:
    """Walk one value and append hits (bounded by depth and MAX_HITS)."""
    if len(hits) >= MAX_HITS:
        return
    if depth > MAX_SCAN_DEPTH:
        hits.append(InjectionHit(field=field, rule="too_deep"))
        return
    if isinstance(value, Mapping):
        text = value.get("untrusted_text")
        if isinstance(text, str):
            rule = find_instruction_like(text)
            if rule:
                hits.append(InjectionHit(field=field, rule=rule))
            return
        for key, item in value.items():
            _scan(item, str(key), depth + 1, hits)
    elif isinstance(value, list):
        for item in value:
            _scan(item, field, depth + 1, hits)
