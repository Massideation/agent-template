"""Pure publish gate for agent-001 public output.

Detects em dash characters and forbidden AI-tell phrases listed in PRD
section 11.8. Also exposes a helper that flags dollar figures appearing in a
candidate text but absent from the confirmed revenue ledger.

No I/O outside the explicit ledger reader. stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EM_DASH = "—"
# En dash and horizontal bar are common em-dash substitutes a model reaches
# for when told "no em dashes". When used as a spaced sentence break (" - ")
# they read exactly like an em dash and are the same AI tell, so we block
# them too. A bare en dash inside a numeric range (1-2) is left alone.
_SPACED_DASH_RE: re.Pattern[str] = re.compile(r"\s[–—―]\s")

# Phrases from PRD section 11.8. Lowercased; matching is case-insensitive.
# Verb-only entries are enforced with word-boundary regex below so plural
# nouns like "leverages" and "ensures" still trip, while bare "navigation"
# does not. The "as verb" callouts in the PRD are preserved here as comments
# next to the patterns that implement them.
FORBIDDEN_PHRASES: list[str] = [
    "delve",
    "navigate",   # as verb
    "leverage",   # as verb
    "robust",
    "ensure",
    "in this article we will explore",
    "it's important to note",
    "in conclusion",
    "furthermore",
    "moreover",
]

# Word-boundary regexes for the single-word entries. Verb-flagged words match
# common verb inflections so "leveraging", "ensures", "navigates" all trip.
_VERB_INFLECTIONS = r"(?:e[sd]?|ing)?"

_SINGLE_WORD_PATTERNS: dict[str, re.Pattern[str]] = {
    "delve": re.compile(r"\bdelv" + _VERB_INFLECTIONS + r"\b", re.IGNORECASE),
    "navigate": re.compile(r"\bnavigat" + _VERB_INFLECTIONS + r"\b", re.IGNORECASE),
    "leverage": re.compile(r"\bleverag" + _VERB_INFLECTIONS + r"\b", re.IGNORECASE),
    "robust": re.compile(r"\brobust\b", re.IGNORECASE),
    "ensure": re.compile(r"\bensur" + _VERB_INFLECTIONS + r"\b", re.IGNORECASE),
    "furthermore": re.compile(r"\bfurthermore\b", re.IGNORECASE),
    "moreover": re.compile(r"\bmoreover\b", re.IGNORECASE),
}

# Multi-word phrases use a simpler case-insensitive substring search.
_MULTI_WORD_PHRASES: list[str] = [
    "in this article we will explore",
    "it's important to note",
    "in conclusion",
]

# Currency token regex. Matches things like $99, $1,000, $1234.56, $0.99.
_CURRENCY_RE: re.Pattern[str] = re.compile(
    r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?|\$\s?\d+(?:\.\d+)?"
)


def check(text: str) -> list[str]:
    """Return a list of human-readable style violations.

    An empty list means the text is publishable. Detects em dash characters
    and forbidden phrases from PRD section 11.8.
    """
    violations: list[str] = []

    if EM_DASH in text:
        count = text.count(EM_DASH)
        violations.append(
            f"em dash character (U+2014) present {count} time(s); use hyphen or rephrase"
        )

    spaced_dashes = _SPACED_DASH_RE.findall(text)
    if spaced_dashes:
        violations.append(
            f"spaced en/em dash used as a sentence break {len(spaced_dashes)} "
            "time(s); use a comma, period, or rephrase"
        )

    for word, pattern in _SINGLE_WORD_PATTERNS.items():
        if pattern.search(text):
            violations.append(f"forbidden phrase: {word!r}")

    lowered = text.lower()
    for phrase in _MULTI_WORD_PHRASES:
        if phrase in lowered:
            violations.append(f"forbidden phrase: {phrase!r}")

    return violations


def _normalize_amount(token: str) -> str:
    """Strip the leading dollar sign and whitespace, keep digits and decimals."""
    return token.replace("$", "").replace(",", "").replace(" ", "").strip()


def _ledger_amounts(ledger_path: Path) -> set[str]:
    """Read amount_usd values from a JSONL ledger file as normalized strings."""
    amounts: set[str] = set()
    if not ledger_path.exists():
        return amounts
    with ledger_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            amount = record.get("amount_usd")
            if amount is None:
                continue
            try:
                numeric = float(amount)
            except (TypeError, ValueError):
                continue
            # Store both the integer-style and float-style representations so
            # "$99" and "$99.00" both match a ledger entry of 99.
            if numeric.is_integer():
                amounts.add(str(int(numeric)))
                amounts.add(f"{numeric:.2f}")
            else:
                amounts.add(f"{numeric:g}")
                amounts.add(f"{numeric:.2f}")
    return amounts


def unverified_revenue_figures(text: str, ledger_path: str | Path) -> list[str]:
    """Return dollar figures in text that are not present in the ledger.

    Reads the ledger file as JSONL, pulling each line's `amount_usd` field.
    Compares numerically (so $99 matches a ledger amount of 99 or 99.00).
    Returns the original tokens, in order of appearance, preserving duplicates
    only on first occurrence.
    """
    ledger = _ledger_amounts(Path(ledger_path))
    seen: set[str] = set()
    unverified: list[str] = []

    for match in _CURRENCY_RE.finditer(text):
        token = match.group(0)
        normalized = _normalize_amount(token)
        try:
            value = float(normalized)
        except ValueError:
            continue
        if value.is_integer():
            candidates = {str(int(value)), f"{value:.2f}"}
        else:
            candidates = {f"{value:g}", f"{value:.2f}"}
        if candidates.isdisjoint(ledger):
            if token not in seen:
                seen.add(token)
                unverified.append(token)
    return unverified
