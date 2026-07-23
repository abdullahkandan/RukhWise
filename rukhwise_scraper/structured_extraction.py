"""Structured, non-taxonomy extraction for postings: degree_level,
degree_field, has_certification, experience_min_years,
experience_max_years, experience_level.

Deliberately NOT part of the skill taxonomy -- see output/taxonomy_v2_spec.md
sections 2-3. "Bachelor's" / "bachelor's degree" / "bachelor's degree in
computer" are one fact about a posting stated four ways; "3 years" /
"2-3 years" / "3-5 years" are the same field at different values, and an
n-gram-shaped taxonomy entry can't carry range semantics. This module
extracts each as ONE structured fact per posting instead of many
overlapping, redundant taxonomy hits.

Every function here takes plain text (or a min/max pair) and returns a
plain value -- no Supabase access, no side effects. extract.py calls
extract_structured_fields() per posting and storage.update_postings_structured()
writes the batch.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Degree level + field
# --------------------------------------------------------------------------

DEGREE_LEVELS = ("matriculation", "intermediate", "diploma", "bachelors", "masters", "phd")
_LEVEL_ORDER = {level: i for i, level in enumerate(DEGREE_LEVELS)}

# Long/unambiguous aliases -- matched unconditionally, no adjacency guard
# needed (unlike the short forms below).
_LEVEL_ALIASES_SAFE: dict[str, tuple[str, ...]] = {
    "matriculation": ("matriculation",),
    "intermediate": ("intermediate",),
    "diploma": ("diploma", "dae"),
    "bachelors": ("bachelor's degree", "bachelor's", "bachelor", "bsc", "bba", "graduate"),
    "masters": ("master's", "msc", "mba", "m.ed"),
    "phd": ("phd", "doctorate"),
}

# Short 2-letter forms -- the spec's guard sentence names bs/ms/ba
# explicitly (the bare-"ms"-vs-"MS Office" collision is the exact failure
# class taxonomy_v1.yaml already documents for bare "r"); "be" and "ma"
# are the same failure class by construction (both are common English
# words/fragments on their own -- "be" especially, the verb, would
# otherwise false-positive on nearly every posting) so they get the same
# guard even though the one-sentence spec description didn't re-list them
# individually. Only matched when adjacent to a degree-context word
# (degree, qualification) or a known field-name word (with or without an
# intervening "in": "MS in Computer Science", "BS Computer Science").
_LEVEL_ALIASES_GUARDED: dict[str, tuple[str, ...]] = {
    "bachelors": ("bs", "ba", "be"),
    "masters": ("ms", "ma"),
}

_GUARD_CONTEXT_WORDS = frozenset({"degree", "qualification"})
_DEGREE_FIELD_WORDS = frozenset({
    "computer", "science", "marketing", "business", "administration", "education",
    "chemistry", "engineering", "accounting", "finance", "arts", "commerce",
    "technology", "management", "statistics", "electrical", "mechanical", "civil",
})

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z.'-]*")


def _tokenize_words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD_RE.findall(text)]


def _phrase_pattern(alias: str) -> "re.Pattern[str]":
    tokens = alias.strip().split()
    fragment = r"\s+".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<!\w)" + fragment + r"(?!\w)", re.IGNORECASE)


_SAFE_PATTERNS = {
    level: [_phrase_pattern(alias) for alias in aliases]
    for level, aliases in _LEVEL_ALIASES_SAFE.items()
}
_GUARDED_PATTERNS = {
    level: [_phrase_pattern(alias) for alias in aliases]
    for level, aliases in _LEVEL_ALIASES_GUARDED.items()
}


def _guard_passes(text: str, match: "re.Match[str]") -> bool:
    """True if the short/ambiguous alias match is adjacent to a degree-
    context word or a known field-name word -- directly before it, or
    directly after it (with an optional intervening "in")."""
    window = 24  # chars either side is comfortably enough for a 2-3 word window
    before_words = _tokenize_words(text[max(0, match.start() - window):match.start()])[-2:]
    after_words = _tokenize_words(text[match.end():match.end() + window])[:3]

    if before_words and before_words[-1] in _GUARD_CONTEXT_WORDS:
        return True
    if after_words and after_words[0] in _GUARD_CONTEXT_WORDS:
        return True
    trailing = after_words[1:] if after_words and after_words[0] == "in" else after_words
    return bool(trailing and trailing[0] in _DEGREE_FIELD_WORDS)


def extract_degree_level(text: str) -> str | None:
    """Lowest stated degree_level found in `text` -- "Bachelor's or
    Master's" means bachelors is the bar, so the LOWEST level in
    DEGREE_LEVELS order wins when multiple appear. None if nothing
    matched."""
    found_levels: set[str] = set()

    for level, patterns in _SAFE_PATTERNS.items():
        if any(p.search(text) for p in patterns):
            found_levels.add(level)

    for level, patterns in _GUARDED_PATTERNS.items():
        for pattern in patterns:
            if any(_guard_passes(text, m) for m in pattern.finditer(text)):
                found_levels.add(level)
                break

    if not found_levels:
        return None
    return min(found_levels, key=lambda level: _LEVEL_ORDER[level])


# Field phrase following a degree mention -- normalized on whitespace and
# case only, no further canonicalization yet ("normalized later" per spec
# section 2; this is intentionally free text, not matched against a
# closed vocabulary).
_FIELD_TRIGGER_RE = re.compile(
    r"(?:degree|bachelor'?s?|master'?s?|bsc|msc|mba|bba|phd|diploma|dae|bs|ms|ba|be)\s*"
    r"(?:'s)?\s*(?:degree)?\s*in\s+"
    r"([A-Za-z][A-Za-z/&,\- ]{2,60}?)"
    r"(?=[.;:\n*]| or | and | with | preferred| required| from | at |,|$)",
    re.IGNORECASE,
)


def extract_degree_field(text: str) -> str | None:
    match = _FIELD_TRIGGER_RE.search(text)
    if not match:
        return None
    field = " ".join(match.group(1).split()).strip(" ,").lower()
    return field or None


_CERTIFICATION_RE = re.compile(r"(?<!\w)certifications?(?!\w)", re.IGNORECASE)


def extract_has_certification(text: str) -> bool:
    return bool(_CERTIFICATION_RE.search(text))


# --------------------------------------------------------------------------
# Experience
# --------------------------------------------------------------------------

# Checked in this priority order -- a bare "N years" pattern would
# otherwise also produce a (wrong) partial hit inside "minimum 3 years" or
# "2-3 years", so the more specific patterns are tried first and the loop
# stops at the first one that matches anywhere in the text.
_EXPERIENCE_RANGE_RE = re.compile(r"(?<!\w)(\d+)\s*(?:-|to)\s*(\d+)\+?\s*years?(?!\w)", re.IGNORECASE)
_EXPERIENCE_MIN_PHRASE_RE = re.compile(
    r"(?<!\w)(?:minimum|min\.?|at least)\s*(?:of\s*)?(\d+)\+?\s*years?(?!\w)", re.IGNORECASE
)
_EXPERIENCE_PLUS_RE = re.compile(r"(?<!\w)(\d+)\+\s*years?(?!\w)", re.IGNORECASE)
_EXPERIENCE_EXACT_RE = re.compile(r"(?<!\w)(\d+)\s*years?(?!\w)", re.IGNORECASE)

_FRESH_GRADUATE_RE = re.compile(r"(?<!\w)fresh\s+graduate(?!\w)|(?<!\w)entry\s+level(?!\w)", re.IGNORECASE)
_SENIOR_WORD_RE = re.compile(r"(?<!\w)senior(?!\w)", re.IGNORECASE)

# "senior" frequently describes a COLLEAGUE or SUPERVISOR the candidate
# works with/reports to, not the candidate's own required level --
# "support senior accountants", "communicating with senior stakeholders",
# "reporting directly to a Senior Investment Analyst" all matched the bare
# word before this guard existed, live-observed after the first
# extract.py --all run (18/1318 postings, ~21% of all senior labels).
# Excludes only the clearest cases; genuinely ambiguous ones ("a senior
# leadership role") are left matching.
_SENIOR_EXCLUDE_BEFORE = frozenset({
    "support", "supporting", "reporting", "report", "reports", "communicating", "communicate",
})
_SENIOR_EXCLUDE_AFTER = frozenset({
    "management", "stakeholders", "director", "directors", "analyst",
    "accountants", "accountant", "manager", "managers",
})


def _senior_signal_present(text: str) -> bool:
    for match in _SENIOR_WORD_RE.finditer(text):
        before = _tokenize_words(text[max(0, match.start() - 40):match.start()])[-4:]
        after = _tokenize_words(text[match.end():match.end() + 30])[:2]
        if set(before) & _SENIOR_EXCLUDE_BEFORE:
            continue
        if set(after) & _SENIOR_EXCLUDE_AFTER:
            continue
        return True
    return False


def extract_experience_years(text: str) -> tuple[int | None, int | None]:
    """(min_years, max_years) from the first experience pattern found."""
    m = _EXPERIENCE_RANGE_RE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)

    m = _EXPERIENCE_MIN_PHRASE_RE.search(text)
    if m:
        return int(m.group(1)), None

    m = _EXPERIENCE_PLUS_RE.search(text)
    if m:
        return int(m.group(1)), None

    m = _EXPERIENCE_EXACT_RE.search(text)
    if m:
        n = int(m.group(1))
        return n, n

    return None, None


def extract_experience_level(text: str, min_years: int | None, max_years: int | None) -> str | None:
    """fresh | junior | mid | senior, derived from the numbers plus
    explicit phrases. "senior" the word, in a requirements-context
    description, wins outright (spec: "6+ or the word senior") --
    checked first, overriding whatever the numbers alone would say."""
    if _senior_signal_present(text):
        return "senior"
    if _FRESH_GRADUATE_RE.search(text) or (min_years == 0 and (max_years or 0) == 0):
        return "fresh"

    # The stated MINIMUM is what defines the bar an employer is actually
    # asking for ("3-5 years" reads as seeking someone at 3+, not capping
    # at 5), so min_years is preferred as the bucketing basis; max_years
    # only stands in when no minimum was captured.
    basis = min_years if min_years is not None else max_years
    if basis is None:
        return None
    if basis <= 0:
        return "fresh"
    if basis <= 2:
        return "junior"
    if basis <= 5:
        return "mid"
    return "senior"


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def extract_structured_fields(posting: dict) -> dict:
    """Runs every structured extractor above over one posting's
    description + experience_raw (spec: "parse each posting's description
    plus experience_raw"). Always returns all six keys, None/False for
    anything not found -- a batch UPDATE that clears a stale value from a
    prior run is correct, not a bug."""
    text = " ".join(filter(None, [posting.get("description"), posting.get("experience_raw")]))

    if not text.strip():
        return {
            "degree_level": None,
            "degree_field": None,
            "has_certification": False,
            "experience_min_years": None,
            "experience_max_years": None,
            "experience_level": None,
        }

    min_years, max_years = extract_experience_years(text)
    return {
        "degree_level": extract_degree_level(text),
        "degree_field": extract_degree_field(text),
        "has_certification": extract_has_certification(text),
        "experience_min_years": min_years,
        "experience_max_years": max_years,
        "experience_level": extract_experience_level(text, min_years, max_years),
    }
