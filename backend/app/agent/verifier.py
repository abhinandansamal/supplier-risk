"""Claim verifier (Python, not a model): ids exist, numbers come from cited facts, ratings match.

Per claim:
- it cites at least one evidence id, every cited id belongs to this investigation, and a cited
  record about a supplier is about a supplier the claim names (SEC-089);
- every number in its text is backed by a fact of the evidence it cites (SEC-066). Text is
  NFKC-normalised first; non-ASCII digits, number words and malformed grouping are refused
  (SEC-091). Only real ids (S0003, PO000004, FY2025, ...), valid ISO dates/months and years
  2000-2099 are skipped. The unit next to a number decides which facts may back it (SEC-088):
  - percent ("%", "pp", "percent"): rate/share facts x 100, or *_pct / *_pp / *change* facts;
  - money (M, bn, million, billion, USD, EUR): facts about usd, eur, revenue or value;
  - window ("6 months", "3 years"): the months/years parameters and *month*/*year* facts;
  - anything else: facts only (never parameters, never a rate x 100), and a word near the
    number must match a word of the fact's key ("57 deliveries" -> deliveries_last_6m).
  Integer facts match exactly. A float fact may be rounded to the written precision, within
  5 %, and a non-zero fact never backs a written 0 (SEC-087). An explicit sign must match;
  without a sign, direction words must agree with the fact (SEC-090). Monthly facts
  (*_YYYY_MM) count only when the claim names that month;
- a rating word (green, low risk, safe, ...) must match the computed rating of the supplier it
  is about; on a claim about suppliers with different ratings it is refused (SEC-092);
- no links, schemes, bare domains or markup (T9, T10, SEC-097), and no control or invisible
  format characters (SEC-096).
The same text checks apply to proposal rationales (SEC-094). "Missing" items are built in
Python, so they are not checked here (SEC-093).
"""

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from app.agent.models import Claim, Report
from app.evidence.models import EvidenceRecord

ProblemCode = Literal[
    "no_evidence",
    "unknown_evidence",
    "wrong_supplier",
    "unsupported_number",
    "rating_mismatch",
    "link_or_markup",
    "hidden_characters",
]
Unit = Literal["percent", "money", "window", "plain"]
MAX_RELATIVE_ERROR = Decimal("0.05")

_IDS = re.compile(
    r"\bev-[A-Za-z0-9_-]+"
    r"|\b(?:S[0-9]{4}|P[0-9]{4}|PO[0-9]{6,8}|FY[0-9]{4}|Q[1-4]|H[12]|v[0-9]{1,3})\b"
    r"|\b(?:10-K|10-Q|20-F)\b"
    r"|\b20[0-9]{2}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12][0-9]|3[01]))?\b"
    r"|\b20[0-9]{2}\b"
)
_NUMBER = re.compile(r"(?<![0-9.,])([+\-]?)([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?|\.[0-9]+)")
_GROUPED = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+")
_UNIT_AFTER = re.compile(
    r"\s*(%|percentage points?\b|percent\b|pp\b|million\b|billion\b|bn\b|M\b|k\b|usd\b|eur\b|"
    r"months?\b|years?\b|weeks?\b|days?\b)",
    re.IGNORECASE,
)
_SCALES = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "k": 1e3}
_NUMBER_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|thousand|dozen)\b",
    re.IGNORECASE,
)
_MONTHLY_KEY = re.compile(r"_(20[0-9]{2})_([0-9]{2})$")
_WORD = re.compile(r"[a-z]+")
_STOPWORDS = frozenset(
    "of the in on over for and with to at by from last is was were are had has have its".split()
)
_FALL = frozenset(
    "fell fall falls fallen drop dropped drops declined decline declines decrease decreased "
    "down lower shrank shrink lost loss negative minus".split()
)
_RISE = frozenset(
    "grew grow grows growth rose rise rises increase increased up higher gain gained".split()
)
_LINK_OR_MARKUP = re.compile(
    r"https?://|www\.|\b(?:mailto|javascript|data|file|ftp):|<[a-zA-Z/!][^>]*>|\]\("
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/"
    r"|\b[a-z0-9-]+\.(?:com|net|org|io|example|ai|info|biz|xyz|ru|cn|co|app|dev|ly|me)\b",
    re.IGNORECASE,
)
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_RATING_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bgreen\b|\b(low|minimal|little|negligible|no)[\s-]risk\b|\bsafe\b|\brisk[\s-]free\b"
        ),
        "green",
    ),
    (re.compile(r"\bamber\b|\b(medium|moderate)[\s-]risk\b"), "amber"),
    (re.compile(r"\bred\b|\b(high|severe|very high)[\s-]risk\b"), "red"),
)
_SUPPLIER_ID = re.compile(r"\bS[0-9]{4}\b")


@dataclass(frozen=True)
class NumberToken:
    """A number written in a text.

    Attributes:
        text: The digits as written (thousands separators kept, sign not included).
        sign: "+", "-" or "" (no explicit sign).
        scale: 1, 1e3, 1e6 or 1e9 from a following unit word.
        unit: percent, money, window or plain.
        context: Lowercase content words around the number.
        valid: False when the digits are malformed (e.g. "12,99").
    """

    text: str
    sign: str
    scale: float
    unit: Unit = "plain"
    context: tuple[str, ...] = ()
    valid: bool = True

    @property
    def value(self) -> Decimal:
        """Return the absolute value as written (before scaling)."""
        return Decimal(self.text.replace(",", ""))

    @property
    def decimals(self) -> int:
        """Return how many decimals were written."""
        digits = self.text.replace(",", "")
        return len(digits.split(".")[1]) if "." in digits else 0


@dataclass(frozen=True)
class Problem:
    """A verifier finding.

    Attributes:
        where: claim or rationale.
        index: Position of the claim.
        code: What is wrong.
        detail: Short detail (a number, an id or a rating word; never the whole text).
    """

    where: Literal["claim", "rationale"]
    index: int
    code: ProblemCode
    detail: str

    def describe(self) -> str:
        """Return "code: detail" for claim problems and rewrite prompts."""
        return f"{self.code}: {self.detail}"


def _normalise(text: str) -> str:
    """Return NFKC text with every dash written as "-"."""
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


def _words(text: str) -> list[str]:
    """Return lowercase content words (stopwords dropped)."""
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _unit(after: str) -> tuple[Unit, float, int]:
    """Return the unit class, scale and length of the unit word right after a number."""
    match = _UNIT_AFTER.match(after)
    if not match:
        return "plain", 1.0, 0
    word = match.group(1).lower()
    if word in ("%", "percent", "pp") or word.startswith("percentage"):
        return "percent", 1.0, match.end()
    if word in _SCALES:
        return "money", _SCALES[word], match.end()
    if word in ("usd", "eur"):
        return "money", 1.0, match.end()
    return "window", 1.0, 0


def extract_numbers(text: str) -> list[NumberToken]:
    """Return the numbers written in a text, skipping ids, valid dates and years.

    Args:
        text: Claim text.

    Returns:
        Number tokens in order, with their unit class and the words around them.
    """
    cleaned = _IDS.sub(" ", _normalise(text)).replace("..", " ")
    tokens = []
    for match in _NUMBER.finditer(cleaned):
        unit, scale, length = _unit(cleaned[match.end() :])
        before = _words(cleaned[max(0, match.start() - 40) : match.start()])[-3:]
        after = _words(cleaned[match.end() + length : match.end() + length + 40])[:4]
        if unit == "plain" and cleaned[max(0, match.start() - 1) : match.start()] in "$€":
            unit = "money"
        tokens.append(
            NumberToken(
                text=match.group(2),
                sign=match.group(1),
                scale=scale,
                unit=unit,
                context=(*before, *after),
                valid=bool(_GROUPED.fullmatch(match.group(2))),
            )
        )
    return tokens


@dataclass(frozen=True)
class _Fact:
    """A number from evidence that may back a written number."""

    key: str
    value: float
    is_int: bool
    is_param: bool


def _facts(records: Iterable[EvidenceRecord], text: str) -> list[_Fact]:
    """Return the numeric facts and window parameters of the cited records."""
    found: list[_Fact] = []
    for record in records:
        pairs = [(k, v, False) for k, v in record.facts.items()]
        pairs += [(k, v, True) for k, v in record.params.items() if k.endswith(("months", "years"))]
        for key, value, is_param in pairs:
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            if not math.isfinite(value):
                continue
            monthly = _MONTHLY_KEY.search(key)
            if monthly and f"{monthly.group(1)}-{monthly.group(2)}" not in text:
                continue
            found.append(_Fact(key, float(value), isinstance(value, int), is_param))
    return found


def _stem(word: str) -> str:
    return word[:5]


def _tied(token: NumberToken, key: str) -> bool:
    """Return True if a word near the number matches a word of the fact key."""
    key_words = {_stem(w) for w in key.split("_") if w.isalpha()}
    return any(_stem(w) in key_words for w in token.context)


def _forms(token: NumberToken, fact: _Fact) -> list[float]:
    """Return the forms of a fact that may back the token, by unit class (SEC-088)."""
    key = fact.key
    is_rate = "rate" in key or "share" in key
    if token.unit == "percent":
        if is_rate:
            return [fact.value * 100]
        return [fact.value] if any(s in key for s in ("pct", "_pp", "change")) else []
    if token.unit == "money":
        money = any(s in key for s in ("usd", "eur", "revenue", "value"))
        return [fact.value] if money else []
    if token.unit == "window":
        window = fact.is_param or "month" in key or "year" in key
        return [fact.value] if window and fact.is_int else []
    if fact.is_param or not _tied(token, key):
        return []
    return [fact.value]


def _direction_ok(token: NumberToken, value: float, key: str) -> bool:
    """Return True if the sign or the direction words agree with the fact (SEC-090).

    Direction words matter only for changes; "dropped to 32 %" states a level.
    """
    if token.sign == "-":
        return value < 0
    if token.sign == "+":
        return value >= 0
    if "change" not in key and "yoy" not in key:
        return True
    words = set(token.context)
    falls, rises = bool(words & _FALL), bool(words & _RISE)
    if value < 0:
        return falls and not rises
    return not falls or value == 0


def _equal(token: NumberToken, value: float, is_int: bool) -> bool:
    """Return True if the written number states the value (SEC-087)."""
    try:
        written = token.value * Decimal(str(token.scale))
        actual = Decimal(str(abs(value)))
    except InvalidOperation:
        return False
    if is_int and token.scale == 1 and token.unit != "percent":
        return written == actual
    if actual != 0 and token.value == 0:
        return False
    quantum = Decimal(1).scaleb(-token.decimals)
    rounded = (actual / Decimal(str(token.scale))).quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded != token.value:
        return False
    return actual == 0 or abs(written - actual) / actual <= MAX_RELATIVE_ERROR


def _backed(token: NumberToken, facts: Sequence[_Fact]) -> bool:
    """Return True if some cited fact backs the written number."""
    if not token.valid:
        return False
    for fact in facts:
        for form in _forms(token, fact):
            if _direction_ok(token, form, fact.key) and _equal(token, form, fact.is_int):
                return True
    return False


def _rating_problems(text: str, ratings: Mapping[str, str]) -> list[str]:
    """Return rating words that differ from the computed rating of the supplier named."""
    lowered = _normalise(text).lower()
    used = [m.group(0) for p, _ in _RATING_WORDS if (m := p.search(lowered))]
    if not used:
        return []
    named = [s for s in _SUPPLIER_ID.findall(text) if s in ratings] or list(ratings)
    expected = {ratings[s] for s in named}
    if len(expected) != 1:
        return ["ambiguous"]
    return [m.group(0) for p, r in _RATING_WORDS if (m := p.search(lowered)) and r not in expected]


def _hidden(text: str) -> bool:
    """Return True for control or invisible format characters (bidi, zero-width)."""
    return any(unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\n\t" for ch in text)


def check_text(
    text: str,
    records: Sequence[EvidenceRecord],
    ratings: Mapping[str, str],
    *,
    where: Literal["claim", "rationale"] = "claim",
    index: int = 0,
) -> list[Problem]:
    """Return the number, rating, link and character problems of one text.

    Args:
        text: Claim text or proposal rationale (model output).
        records: The evidence the text cites.
        ratings: Computed rating per supplier id.
        where: claim or rationale.
        index: Claim position.

    Returns:
        Problems found.
    """
    if _hidden(text):
        return [Problem(where, index, "hidden_characters", "control or format character")]
    if _LINK_OR_MARKUP.search(_normalise(text)):
        # Markup is refused whole; its attribute values are not numbers worth checking.
        return [Problem(where, index, "link_or_markup", "link or markup")]
    normalised = _normalise(text)
    problems = [
        Problem(where, index, "unsupported_number", ch)
        for ch in sorted({ch for ch in normalised if ch.isdigit() and not ch.isascii()})
    ]
    problems += [
        Problem(where, index, "unsupported_number", word.lower())
        for word in _NUMBER_WORDS.findall(normalised)
    ]
    facts = _facts(records, normalised)
    problems += [
        Problem(where, index, "unsupported_number", f"{token.sign}{token.text}")
        for token in extract_numbers(text)
        if not _backed(token, facts)
    ]
    problems += [
        Problem(where, index, "rating_mismatch", word) for word in _rating_problems(text, ratings)
    ]
    return problems


def supplier_problems(
    named: Iterable[str], records: Sequence[EvidenceRecord], *, where: Literal["claim", "rationale"]
) -> list[Problem]:
    """Return a problem per cited record about a supplier that was not named (SEC-089)."""
    allowed = set(named)
    if not allowed:
        return []
    return [
        Problem(where, 0, "wrong_supplier", r.evidence_id)
        for r in records
        if r.params.get("supplier_id") not in (None, *allowed)
    ]


def _check_claim(
    index: int,
    claim: Claim,
    evidence: Mapping[str, EvidenceRecord],
    ratings: Mapping[str, str],
) -> list[Problem]:
    """Return the problems of one claim."""
    if not claim.evidence_ids:
        return [Problem("claim", index, "no_evidence", "no evidence ids")]
    unknown = [e for e in claim.evidence_ids if e not in evidence]
    if unknown:
        return [Problem("claim", index, "unknown_evidence", unknown[0][:64])]
    cited = [evidence[e] for e in claim.evidence_ids]
    wrong = supplier_problems(_SUPPLIER_ID.findall(claim.text), cited, where="claim")
    problems = [Problem("claim", index, p.code, p.detail) for p in wrong]
    return problems + check_text(claim.text, cited, ratings, index=index)


def verify_report(
    report: Report, evidence: Mapping[str, EvidenceRecord], ratings: Mapping[str, str]
) -> list[Problem]:
    """Return every problem of a report's claims (empty when it verifies).

    Args:
        report: The report to check.
        evidence: This investigation's evidence records by id.
        ratings: Computed rating per supplier id.

    Returns:
        Problems in claim order.
    """
    problems: list[Problem] = []
    for index, claim in enumerate(report.claims):
        problems += _check_claim(index, claim, evidence, ratings)
    return problems


def mark_unverified(report: Report, problems: Sequence[Problem]) -> Report:
    """Return the report with failing claims marked unverified.

    Args:
        report: The report.
        problems: Its problems.

    Returns:
        A copy with verified=False and the problems on every failing claim.
    """
    by_claim: dict[int, list[str]] = {}
    for problem in problems:
        by_claim.setdefault(problem.index, []).append(problem.describe())
    claims = [
        claim.model_copy(update={"verified": False, "problems": by_claim[i]})
        if i in by_claim
        else claim
        for i, claim in enumerate(report.claims)
    ]
    return report.model_copy(update={"claims": claims})
