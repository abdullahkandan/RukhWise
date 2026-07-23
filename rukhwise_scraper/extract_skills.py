"""Skill extraction: matches taxonomy_v2.yaml aliases against posting text.

Each posting reports a skill at most once in the output, regardless of how
many times an alias appears in its text -- this is deliberate defense
against templated postings (the same job text, and therefore the same
skill phrase, repeated verbatim across many near-identical listings from
one employer) inflating mention counts. extract_skills only needs to
answer "does this posting mention X," not "how many times."

v2 adds requirement_type (skill | credential | experience | language |
attribute) alongside category on every entry -- see skill_requirement_type()
below and output/taxonomy_v2_spec.md. Credential and experience are NOT
taxonomy entries (they're structured postings columns, see
structured_extraction.py); only language and attribute are new here,
matched via the exact same alias mechanism as v1's 96 skills. This module
never touches taxonomy_v1.yaml, which stays in place, untouched, as the
historical record extraction_method='taxonomy_v1' rows were computed
against.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from config import setup_logging

logger = setup_logging()

TAXONOMY_PATH = Path(__file__).parent / "taxonomy_v2.yaml"


def _load_taxonomy() -> dict:
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _alias_to_regex_fragment(alias: str) -> str:
    """Escape an alias, letting internal whitespace match `\\s+` so a
    multi-word phrase still matches across a newline (e.g. text produced by
    the HTML-bullet-to-newline conversion in mustakbil.py)."""
    tokens = alias.strip().split()
    return r"\s+".join(re.escape(t) for t in tokens)


def _build_skill_patterns(taxonomy: dict) -> list[tuple[str, str, "re.Pattern"]]:
    """Returns [(skill_key, category, compiled_pattern), ...].

    Boundary is a manual `(?<!\\w)...(?!\\w)` lookaround rather than `\\b`.
    `\\b` requires a word-char/non-word-char *transition* at that exact
    position -- which silently fails to match right after a
    punctuation-ending alias like "c++" or "c#" at the end of a sentence,
    since neither the trailing "+" nor a following space/end-of-string is a
    `\\w` character, so no transition ever occurs and `\\b` never fires
    there. A lookaround only asserts "the adjacent character is not a word
    character" independently on each side, which degrades correctly to
    end-of-string and punctuation -- fixing that case while still behaving
    exactly like `\\b` for ordinary word-ending aliases.

    This is also what makes bare "r" safe: `(?<!\\w)r(?!\\w)` only matches
    an isolated "r" token (e.g. "R programming"), never the letter buried
    inside another word like "director" or "programmer", because in both of
    those the character adjacent to that "r" is itself a word character,
    which fails the lookaround.

    `re` operates in Unicode mode by default for `str` patterns in Python 3
    (no extra flag needed), so `\\w` and these lookarounds work correctly
    for the Arabic alias too.
    """
    patterns = []
    for skill_key, spec in taxonomy["skills"].items():
        category = spec["category"]
        aliases = spec.get("aliases", [])
        if not aliases:
            continue
        # Longest-first so the alternation prefers matching a longer phrase
        # over a shorter one that happens to be its prefix.
        fragments = sorted(
            (_alias_to_regex_fragment(a.lower()) for a in aliases),
            key=len,
            reverse=True,
        )
        combined = "(?:" + "|".join(fragments) + ")"
        pattern = re.compile(r"(?<!\w)" + combined + r"(?!\w)")
        patterns.append((skill_key, category, pattern))
    return patterns


_TAXONOMY = _load_taxonomy()
_SKILL_PATTERNS = _build_skill_patterns(_TAXONOMY)


def skill_category(skill_key: str) -> str | None:
    spec = _TAXONOMY["skills"].get(skill_key)
    return spec["category"] if spec else None


def skill_requirement_type(skill_key: str) -> str:
    """skill | credential | experience | language | attribute for a
    matched skill key. Defaults to 'skill' for any entry that predates
    v2's requirement_type field (should be unreachable -- taxonomy_v2.yaml
    sets it explicitly on all 114 entries -- but a matched skill absent
    from the taxonomy at lookup time must never crash extraction)."""
    spec = _TAXONOMY["skills"].get(skill_key)
    return (spec or {}).get("requirement_type", "skill")


def _skills_raw_to_text(skills_raw) -> str:
    """Flatten either Rozee's tag-list or Mustakbil's required_skills_text
    dict shape into plain text. Defensively also accepts a raw JSON string
    in case a caller hasn't already deserialized the jsonb column."""
    if not skills_raw:
        return ""
    if isinstance(skills_raw, str):
        try:
            skills_raw = json.loads(skills_raw)
        except (ValueError, TypeError):
            return skills_raw
    if isinstance(skills_raw, list):
        return " ".join(str(s) for s in skills_raw)
    if isinstance(skills_raw, dict):
        return str(skills_raw.get("required_skills_text") or "")
    return ""


def extract_skills(posting: dict) -> list[str]:
    """Returns distinct canonical skill keys mentioned in a posting's
    title + description + skills_raw (concatenated, lowercased). Order
    follows taxonomy declaration order, not detection order.
    """
    text_parts = [
        posting.get("title") or "",
        posting.get("description") or "",
        _skills_raw_to_text(posting.get("skills_raw")),
    ]
    text = " ".join(text_parts).lower()
    if not text.strip():
        return []

    found = []
    for skill_key, _category, pattern in _SKILL_PATTERNS:
        if pattern.search(text):
            found.append(skill_key)
    return found
