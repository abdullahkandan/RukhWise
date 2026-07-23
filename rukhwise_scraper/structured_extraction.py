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
# needed (unlike the short/ambiguous forms below).
_LEVEL_ALIASES_SAFE: dict[str, tuple[str, ...]] = {
    "matriculation": ("matriculation",),
    "diploma": ("diploma", "dae"),
    "bachelors": ("bachelor's degree", "bachelor's", "bachelor", "bsc", "bba"),
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

# "graduate" -- guarded separately from bs/ms/ba/be/ma, with its own poison
# vocabulary: live-observed after the first extract.py --all run, 27 of
# 128 bachelors labels rested SOLELY on this bare alias (no stronger
# degree phrase alongside it), and one sampled case was an institution
# describing its own "undergraduate and graduate programs" -- not a job
# requirement at all. "Fresh graduate"/"recent graduate" is an
# EXPERIENCE-level phrase (extract_experience_level already handles it
# via _FRESH_GRADUATE_RE) and must never also set degree_level.
_LEVEL_ALIASES_GRADUATE: dict[str, tuple[str, ...]] = {"bachelors": ("graduate",)}
_GRADUATE_EMPLOYMENT_LEVEL_BEFORE = frozenset({"fresh", "recent", "new"})
_GRADUATE_CONTEXT_WORDS = frozenset({"degree", "qualification", "education"})
_GRADUATE_POISON_WORDS = frozenset({
    "program", "programs", "programme", "programmes", "institution", "institute",
    "university", "school", "alumni", "alumnus", "college", "campus", "curriculum",
    "offers", "offering",
})

# "intermediate" -- guarded separately, same reasoning: live-observed 3 of
# 6 labels were skill-proficiency language ("intermediate proficiency in
# Microsoft Excel", "basic to intermediate SQL", "basic and intermediate
# math concepts"), not the Pakistani Intermediate (11th/12th grade,
# FA/FSc-equivalent) qualification.
_LEVEL_ALIASES_INTERMEDIATE: dict[str, tuple[str, ...]] = {"intermediate": ("intermediate",)}
_INTERMEDIATE_CONTEXT_WORDS = frozenset({
    "qualification", "education", "matric", "matriculation", "fsc", "fa", "bachelor's", "bachelors",
})
_INTERMEDIATE_POISON_AFTER = frozenset({
    "proficiency", "level", "in", "excel", "sql", "math", "mathematics", "computer",
    "english", "communication", "skills", "knowledge",
})

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
_GRADUATE_PATTERNS = {
    level: [_phrase_pattern(alias) for alias in aliases]
    for level, aliases in _LEVEL_ALIASES_GRADUATE.items()
}
_INTERMEDIATE_PATTERNS = {
    level: [_phrase_pattern(alias) for alias in aliases]
    for level, aliases in _LEVEL_ALIASES_INTERMEDIATE.items()
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


def _graduate_guard_passes(text: str, match: "re.Match[str]") -> bool:
    """'graduate' implies bachelors ONLY when adjacent to degree language
    within the same clause. Rejects "fresh/recent/new graduate"
    (employment-level phrasing, an experience signal handled entirely
    elsewhere) outright, and rejects when the surrounding clause is an
    institution describing its own programs/alumni rather than a job
    requirement."""
    before_word = _tokenize_words(text[max(0, match.start() - 15):match.start()])
    if before_word and before_word[-1] in _GRADUATE_EMPLOYMENT_LEVEL_BEFORE:
        return False

    before_region = text[max(0, match.start() - 80):match.start()]
    before_clause = _CLAUSE_BOUNDARY_RE.split(before_region)[-1]
    after_region = text[match.end():match.end() + 80]
    after_clause = _CLAUSE_BOUNDARY_RE.split(after_region)[0]
    local_words = set(_tokenize_words(before_clause)) | set(_tokenize_words(after_clause))

    if local_words & _GRADUATE_POISON_WORDS:
        return False
    if local_words & _GRADUATE_CONTEXT_WORDS:
        return True

    trailing = _tokenize_words(after_clause)[:3]
    trailing_no_in = trailing[1:] if trailing and trailing[0] == "in" else trailing
    return bool(trailing_no_in and trailing_no_in[0] in _DEGREE_FIELD_WORDS)


def _intermediate_guard_passes(text: str, match: "re.Match[str]") -> bool:
    """'intermediate' implies the qualification ONLY when adjacent to
    qualification language, or standing alone as a stated requirement
    (approximated as: inside a requirements/qualifications-labeled
    section, reusing the same heading-detection the numeric-years guard
    uses). Rejects outright when immediately followed by a proficiency-
    level or skill-name word -- "intermediate proficiency in Excel",
    "intermediate SQL" are skill claims, not a credential."""
    before_region = text[max(0, match.start() - 80):match.start()]
    before_clause = _CLAUSE_BOUNDARY_RE.split(before_region)[-1]
    after_region = text[match.end():match.end() + 80]
    after_clause = _CLAUSE_BOUNDARY_RE.split(after_region)[0]

    after_words = _tokenize_words(after_clause)[:2]
    if after_words and after_words[0] in _INTERMEDIATE_POISON_AFTER:
        return False

    local_words = set(_tokenize_words(before_clause)) | set(_tokenize_words(after_clause))
    if local_words & _INTERMEDIATE_CONTEXT_WORDS:
        return True

    return _inside_requirements_section(text, match.start())


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

    for level, patterns in _GRADUATE_PATTERNS.items():
        for pattern in patterns:
            if any(_graduate_guard_passes(text, m) for m in pattern.finditer(text)):
                found_levels.add(level)
                break

    for level, patterns in _INTERMEDIATE_PATTERNS.items():
        for pattern in patterns:
            if any(_intermediate_guard_passes(text, m) for m in pattern.finditer(text)):
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
# "2-3 years", so the more specific patterns are tried first.
_EXPERIENCE_RANGE_RE = re.compile(r"(?<!\w)(\d+)\s*(?:-|to)\s*(\d+)\+?\s*years?(?!\w)", re.IGNORECASE)
_EXPERIENCE_MIN_PHRASE_RE = re.compile(
    r"(?<!\w)(?:minimum|min\.?|at least)\s*(?:of\s*)?(\d+)\+?\s*years?(?!\w)", re.IGNORECASE
)
_EXPERIENCE_PLUS_RE = re.compile(r"(?<!\w)(\d+)\+\s*years?(?!\w)", re.IGNORECASE)
_EXPERIENCE_EXACT_RE = re.compile(r"(?<!\w)(\d+)\s*years?(?!\w)", re.IGNORECASE)


def _parse_range_match(m: "re.Match[str]") -> tuple[int, int]:
    lo, hi = int(m.group(1)), int(m.group(2))
    return (lo, hi) if lo <= hi else (hi, lo)


def _parse_open_ended_match(m: "re.Match[str]") -> tuple[int, None]:
    return int(m.group(1)), None


def _parse_single_match(m: "re.Match[str]") -> tuple[int, int]:
    n = int(m.group(1))
    return n, n


# Priority order: range > explicit-minimum phrase > "N+" > bare "N years".
_EXPERIENCE_PATTERN_PARSERS = (
    (_EXPERIENCE_RANGE_RE, _parse_range_match),
    (_EXPERIENCE_MIN_PHRASE_RE, _parse_open_ended_match),
    (_EXPERIENCE_PLUS_RE, _parse_open_ended_match),
    (_EXPERIENCE_EXACT_RE, _parse_single_match),
)

# Same failure class as the bare-"senior" guard: a bare "N years" (or even
# "N-N years", "minimum N years") mention in free text can describe the
# COMPANY's years in business ("in business for over 35 years"), a
# candidate AGE threshold ("18 years or older", "Age: 26-50 years"), or
# years of EDUCATION (the Pakistani HEC convention "18 Years of Education"
# for a PhD) -- none of which are the candidate's required work
# experience. Live-observed after the first extract.py --all run: 8 of 10
# postings with experience_min_years > 15 were exactly this (2 company-age,
# 4 candidate-age, 1 education-duration Pakistani-Urdu one already double-
# counted, 1 clean-but-unusual outlier not in this class).
_REQUIREMENTS_CONTEXT_RE = re.compile(
    r"\b(experience|exp|required|require|requires|requirement|requirements|minimum|min|least)\b",
    re.IGNORECASE,
)
# Local-word matching is regex-based, not a tokenize-and-set-intersect --
# a word-boundary regex correctly ignores trailing punctuation
# ("experience." still matches \bexperience\b); an earlier version built
# on _tokenize_words swallowed the trailing period into the token itself
# ("experience." != "experience"), silently rejecting nearly every
# genuine "N years of experience." match ending a sentence.

# A number can sit near "required"/"experience" and STILL not be about
# work experience -- "18 years or older (... you will be required to
# show your CNIC)" has "required" nearby, but it modifies "CNIC", not the
# candidate's years. education/older/age are hard rejects regardless of
# an otherwise-passing local or section-heading check, since a number
# adjacent to one of these is describing something else by definition.
_EXPERIENCE_POISON_RE = re.compile(r"\b(education|educational|older|age)\b", re.IGNORECASE)

_REQ_SECTION_HEADING_RE = re.compile(
    r"(requirements?|qualifications?|required\s+skills?|what\s+you.?ll\s+need|who\s+you\s+are)\s*[:\-]",
    re.IGNORECASE,
)
_OTHER_SECTION_HEADING_RE = re.compile(
    r"(responsibilities|about\s+(us|the\s+role|the\s+company)|benefits|perks|overview|"
    r"job\s+description|role\s+overview|company\s+description|what\s+we\s+offer|duties)\s*[:\-]",
    re.IGNORECASE,
)


def _inside_requirements_section(text: str, pos: int, lookback: int = 400) -> bool:
    """True if the nearest preceding section heading (within `lookback`
    chars) is a requirements/qualifications-style heading, not some other
    section (responsibilities, about us, benefits...) that would mean
    we've moved past the requirements block."""
    window = text[max(0, pos - lookback):pos]
    req_matches = list(_REQ_SECTION_HEADING_RE.finditer(window))
    if not req_matches:
        return False
    other_matches = list(_OTHER_SECTION_HEADING_RE.finditer(window))
    last_req_pos = req_matches[-1].start()
    last_other_pos = other_matches[-1].start() if other_matches else -1
    return last_req_pos > last_other_pos


_CLAUSE_BOUNDARY_RE = re.compile(r"[.;()]")


def _requirements_context_guard(text: str, match: "re.Match[str]") -> bool:
    """Local context is CLAUSE-bounded, not just a fixed character count --
    stopping at the nearest sentence/clause boundary (. ; ( )) on each
    side. A fixed-width window is not enough: "18 years or older (you
    will be required to show your CNIC)" has "required" only ~30 chars
    after "years", well inside any window wide enough to also catch "3 to
    5 years of hands-on software development experience" (~45 chars).
    Clause-bounding correctly excludes the first ("required" is inside a
    separate parenthetical) while keeping the second (nothing breaks the
    "years ... experience" phrase)."""
    before_region = text[max(0, match.start() - 80):match.start()]
    before_clause = _CLAUSE_BOUNDARY_RE.split(before_region)[-1]

    after_region = text[match.end():match.end() + 80]
    after_clause = _CLAUSE_BOUNDARY_RE.split(after_region)[0]

    local = before_clause + " " + after_clause
    if _EXPERIENCE_POISON_RE.search(local):
        return False
    if _REQUIREMENTS_CONTEXT_RE.search(local):
        return True
    return _inside_requirements_section(text, match.start())

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


def _find_years_pattern(text: str) -> tuple[int | None, int | None]:
    """First matching pattern, priority order, no guard -- for text that's
    already trusted (experience_raw, a structured field the source site
    itself scopes to "years required," not free text that could be about
    anything)."""
    for pattern, parser in _EXPERIENCE_PATTERN_PARSERS:
        m = pattern.search(text)
        if m:
            return parser(m)
    return None, None


def _find_years_pattern_guarded(text: str) -> tuple[int | None, int | None]:
    """Same priority order, but for UNTRUSTED free text (description): each
    candidate match must pass the requirements-context guard. A match that
    fails is skipped in favor of a later match of the SAME pattern type,
    before falling through to the next priority tier -- so a posting with
    both "in business for over 35 years" and, later, "3-5 years of
    experience" correctly finds the second, not the first."""
    for pattern, parser in _EXPERIENCE_PATTERN_PARSERS:
        for m in pattern.finditer(text):
            if _requirements_context_guard(text, m):
                return parser(m)
    return None, None


def extract_experience_years(description: str | None, experience_raw: str | None) -> tuple[int | None, int | None]:
    """(min_years, max_years). experience_raw is checked FIRST and trusted
    unconditionally -- it's a structured, site-provided "years required"
    field (see mustakbil.py/rozee_parser.py), not free text, so no guard
    is needed or applied. Only falls back to scanning description when
    experience_raw yields nothing, and that scan requires each candidate
    match to pass _requirements_context_guard -- description prose can
    just as easily be stating the COMPANY's years in business, a
    candidate AGE threshold, or years of EDUCATION, none of which are the
    candidate's required work experience (see the guard's own docstring
    for live-observed examples of each)."""
    if experience_raw:
        result = _find_years_pattern(experience_raw)
        if result != (None, None):
            return result

    if not description:
        return None, None
    return _find_years_pattern_guarded(description)


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
    prior run is correct, not a bug.

    Numeric years extraction is the one exception to "concatenate and
    search the combined text": experience_raw and description are passed
    to extract_experience_years() SEPARATELY, since experience_raw is a
    trusted structured field but description is free text needing the
    requirements-context guard (see that function's docstring). Every
    other extractor here still operates on the combined text -- degree
    level/field, certification, and the senior/fresh phrase checks inside
    extract_experience_level() aren't the failure class this guard
    addresses."""
    description = posting.get("description")
    experience_raw = posting.get("experience_raw")
    text = " ".join(filter(None, [description, experience_raw]))

    if not text.strip():
        return {
            "degree_level": None,
            "degree_field": None,
            "has_certification": False,
            "experience_min_years": None,
            "experience_max_years": None,
            "experience_level": None,
        }

    min_years, max_years = extract_experience_years(description, experience_raw)
    return {
        "degree_level": extract_degree_level(text),
        "degree_field": extract_degree_field(text),
        "has_certification": extract_has_certification(text),
        "experience_min_years": min_years,
        "experience_max_years": max_years,
        "experience_level": extract_experience_level(text, min_years, max_years),
    }
