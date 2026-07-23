"""Rukhwise requirement-drift discovery: read-only analysis, never mutates
taxonomy_v1.yaml (or anything else -- this script has no write path at all).

  python drift.py
      Full run over every posting currently in Supabase: computes the
      DEPTH metric (the headline blind-spot statistic -- see below), mines
      candidate requirement phrases the taxonomy doesn't recognize,
      classifies/segments/ranks them, writes
      output/drift_report_{date}.md (gitignored), and prints the depth
      table plus the top 15 full-corpus candidates per requirement type
      to the console.

  python drift.py --gap-focus
      Additionally restricts a SECOND candidate pass to only postings in
      the blind-spot corpus (<=1 substantive taxonomy match -- see DEPTH
      below), ranked by LIFT rather than distinct-company count, and
      prints the top 25 of those to console (top 100 go in the report).
      This is the section of the report actually worth reading first: it
      answers "what is this taxonomy missing, specifically where it's
      already known to be blind," not just "what's common everywhere."

  python drift.py --gap-focus --with-groq
      Additionally sends the gap-focus top 100 (and ONLY those -- never
      the full ~1300 candidate list) to Groq: phrase, heuristic type,
      lift, and counts only, never raw posting text or snippets. Asks for
      a proposed requirement type, a proposed category (one of the
      existing 11, or a new one if none fit), a one-line rationale, and a
      confidence 0-1, per candidate. The raw response is logged verbatim
      to output/drift_groq_{date}.json (a separate file, gitignored) and
      summarized (never dumped in full) in the report. Requires
      GROQ_API_KEY in the environment; skipped with a log line if absent,
      and skipped (with a log line) if --gap-focus wasn't also passed,
      since there's no other list this is meant to run against anymore.
      No matter what Groq returns, this script NEVER writes to
      taxonomy_v1.yaml -- there is no code path here that could.

WHY THIS EXISTS: taxonomy v1 is 96 skills, built and tuned against a
corpus that was, at the time, almost entirely technology and
business-support roles from Mustakbil/Rozee. The corpus has since grown
to include food service, retail, accounting, HR, engineering, NGO, admin
and trades postings via Indeed and LinkedIn -- categories the taxonomy was
never designed to see. This tool's entire purpose is to expose that blind
spot, so every design choice here deliberately avoids favoring
technical-looking vocabulary:
  - N-gram mining and the stopword/filler exclusion list are plain-English
    grammar/HR-boilerplate only, never anything domain-specific.
  - Domain inference (from posting titles) explicitly covers non-technical
    categories (food_service, retail, trades, ngo_development, etc.),
    not just tech.
  - Full-corpus ranking is by DISTINCT-COMPANY count, never raw mention
    frequency -- the same anti-bulk-poster principle forecast.py and
    api/main.py already apply elsewhere in this codebase, since a single
    high-volume employer's repeated phrasing would otherwise manufacture
    "demand" for a candidate no other employer actually uses.
  - Gap-focus ranking is by LIFT instead, deliberately: distinct-company
    count alone would just resurface generic business vocabulary that's
    common everywhere (including in postings the taxonomy already
    measures fine). Lift -- how much more common a phrase is in the
    blind-spot corpus than in the full corpus -- surfaces vocabulary
    doing real, disproportionate work SPECIFICALLY where the taxonomy
    cannot currently see, which is the entire point of --gap-focus.

DEPTH METRIC (the headline blind-spot statistic, replacing plain zero-match
coverage): for every posting, total distinct taxonomy matches and
SUBSTANTIVE distinct matches (excluding the 'soft' and 'office_admin'
categories, which match almost any posting regardless of actual domain --
a posting can register as "matched" while being substantively unmeasured,
which is exactly what plain zero-match coverage was blind to). A posting
with <=1 substantive match is this script's definition of the "blind-spot
corpus" that --gap-focus restricts to.

Candidate qualification bar (computed within whichever corpus is being
mined -- the full corpus normally, or the blind-spot corpus under
--gap-focus): an n-gram (1-4 words) mined from posting descriptions must
appear in >= 8 distinct postings from >= 3 distinct companies WITHIN that
corpus, and must not already be a taxonomy_v1 alias (checked by exact,
whitespace-normalized string match against every alias in
taxonomy_v1.yaml -- reusing the already-loaded taxonomy object from
extract_skills.py rather than re-parsing it, so there is exactly one
source of truth for "what the taxonomy already knows").
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

# Windows console defaults to cp1252, which crashes/mangles printing real
# scraped text (curly apostrophes, non-Latin script) -- same fix applied
# in experiment_jobspy.py for the same reason.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

import extract_skills  # noqa: E402 -- reuses the already-loaded taxonomy + matcher rather than reimplementing either

MIN_DISTINCT_POSTINGS = 8
MIN_DISTINCT_COMPANIES = 3
MAX_NGRAM = 4
CONTEXT_SNIPPETS_PER_CANDIDATE = 3
SNIPPET_WINDOW_CHARS = 40
TOP_N_CONSOLE = 15
# The "skill" fallback bucket alone can hold 1000+ qualifying candidates
# (it's literally "everything not otherwise classified") -- giving every
# one of them full detail (source split + 3 snippets) would produce an
# unreviewable multi-megabyte file. Ranking is always by distinct-company
# count, so capping here only ever drops the LEAST-evidenced tail, never
# the signal at the top.
REPORT_DETAIL_CAP = 50   # full detail (postings/companies/source-split/snippets), per requirement type
REPORT_SEGMENT_CAP = 30  # compact table rows, per source/domain segment
OUTPUT_DIR = Path("output")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

REQUIREMENT_TYPES = ("credential", "experience", "language", "attribute", "skill")


# --------------------------------------------------------------------------
# Stopwords / generic filler -- plain-English grammar and HR boilerplate
# ONLY, never anything domain-specific (technical or otherwise). An n-gram
# is dropped if its first or last token falls in this set, or if every
# token in it does. Deliberately does NOT include temporal-unit words
# (year/years/month/week/day/hour and their plurals) or attribute words
# (shift/travel/license/own/vehicle/etc.) -- those carry the exact signal
# classify_type() below needs to detect experience/attribute/credential
# candidates, so filtering them out here would silently defeat step 2.
# --------------------------------------------------------------------------

_STOPWORDS = frozenset("""
a an the and or nor but if then else when while as of to in on at by for
with without from into onto over under again further once here there all
any both each few more most other some such no not only own same so than
too very can will just should now is are was were be been being have has
had do does did having this that these those i me my we our you your he
him his she her it its they them their what which who whom per about
through
""".split())

_FILLER = frozenset("""
candidate candidates company companies team teams role roles position
positions job jobs opportunity opportunities please apply applicant
applicants send resume resumes cv cvs salary package packages benefit
benefits environment environments individual individuals person persons
someone anyone growing looking seeking join joining excellent strong good
great preferred required requirement requirements responsibility
responsibilities description descriptions duties duty successful ideal
minimum maximum plus etc detail details overview summary base ago now
click below above regarding related including include includes various
maintain maintaining ensure ensuring manage managing coordinate
coordinating prepare preparing support supporting develop developing
implement implementing perform performing conduct conducting handle
handling oversee overseeing monitor monitoring assist assisting provide
providing deliver delivering execute executing drive driving build
building create creating review reviewing process processing complete
completing achieve achieving meet meeting collaborate collaborating
communicate communicating participate participating contribute
contributing identify identifying analyze analyzing plan planning
organize organizing follow following keep keeping help helping location
key business management performance records record information activity
activities standard standards policy policies guideline guidelines task
tasks function functions operation operations level levels basis area
areas field fields sector industry organization organizations department
departments staff employee employees workplace workforce matter matters
issue issues system systems tool tools knowledge ability abilities
understanding background qualification qualifications experience work
working skill skills report reports development pakistan accurate
services data client clients lead leads across using up type lahore
karachi islamabad rawalpindi faisalabad multan peshawar quetta customer
solutions digital technical design content basic based track professional
""".split())

# NOTE: deliberately does NOT include generic domain/function words like
# "sales" or "marketing" -- those are exactly the non-technical vocabulary
# this tool exists to surface, not suppress. Filtering them out would bias
# the report right back toward technical-looking terms, the opposite of
# what it's for.

# Standalone temporal-unit words (bare "years", "months", etc. with no
# leading number) carry no requirement signal alone, but must NOT go in
# _FILLER above -- that set is checked at n-gram BOUNDARIES, and "years" is
# the boundary token of every legitimate "N years" candidate (see
# _EXPERIENCE_RE below). Filtered separately, only for size-1 grams.
_BARE_TEMPORAL_UNITS = frozenset({
    "year", "years", "yr", "yrs", "month", "months", "week", "weeks", "day", "days", "hour", "hours",
})

_EXCLUDE_TOKENS = _STOPWORDS | _FILLER

# Unicode-aware: sequences of letters (any script, so Urdu/Arabic-script
# text is tokenized too, not just ASCII), allowing an internal apostrophe
# (straight ' or curly U+2019 -- job postings mix both) or hyphen/slash
# between letter groups (e.g. "bachelor's", "co-ordination", "on-site"),
# or a digit run with an optional trailing '+' (e.g. "3", "3+", "24").
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-/][^\W\d_]+)*|\d+\+?", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _normalize_company_key(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def _build_alias_set(taxonomy: dict) -> frozenset[str]:
    aliases = set()
    for spec in taxonomy["skills"].values():
        for alias in spec.get("aliases", []):
            aliases.add(" ".join(str(alias).lower().split()))
    return frozenset(aliases)


_TAXONOMY_ALIASES = _build_alias_set(extract_skills._TAXONOMY)


def _clean_ngrams_for_posting(tokens: list[str]) -> set[str]:
    """Every qualifying 1-4 gram in `tokens`, deduped WITHIN this posting --
    a phrase repeated 5 times in one description still only counts once
    toward that posting's contribution, mirroring extract_skills.py's own
    anti-templating stance on skill mentions."""
    grams: set[str] = set()
    n = len(tokens)
    for size in range(1, MAX_NGRAM + 1):
        for i in range(n - size + 1):
            gram = tokens[i:i + size]
            if gram[0] in _EXCLUDE_TOKENS or gram[-1] in _EXCLUDE_TOKENS:
                continue
            if size == 1 and gram[0] in _BARE_TEMPORAL_UNITS:
                continue
            if all(t.isdigit() or t.rstrip("+").isdigit() for t in gram):
                continue  # a bare number/number-run carries no requirement meaning alone
            text = " ".join(gram)
            if text in _TAXONOMY_ALIASES:
                continue  # already recognized -- not a drift candidate
            grams.add(text)
    return grams


def _extract_snippet(description: str, gram: str, window: int = SNIPPET_WINDOW_CHARS) -> str | None:
    pattern = re.compile(
        r"(?<!\w)" + r"\s+".join(re.escape(t) for t in gram.split()) + r"(?!\w)", re.IGNORECASE | re.UNICODE
    )
    m = pattern.search(description)
    if not m:
        return None
    start = max(0, m.start() - window)
    end = min(len(description), m.end() + window)
    snippet = re.sub(r"\s+", " ", description[start:end].replace("\n", " ")).strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(description) else ""
    return f"{prefix}{snippet}{suffix}"


# --------------------------------------------------------------------------
# Domain inference -- crude, title-only, deliberately covers non-technical
# categories (this is the whole point: the new domains Indeed/LinkedIn
# brought in). "Approximate grouping is fine" per spec -- first matching
# bucket wins, in the order below; anything unmatched falls to
# 'other_unclassified' rather than being forced into a wrong bucket.
# --------------------------------------------------------------------------

_DOMAIN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("food_service", ("chef", "cook", "waiter", "waitress", "barista", "kitchen", "restaurant",
                       "food", "catering", "steward", "bartender", "server")),
    ("healthcare", ("nurse", "doctor", "physician", "medical officer", "pharmacist", "dentist",
                     "healthcare", "clinic", "hospital", "paramedic")),
    ("ngo_development", ("ngo", "program officer", "project officer", "field officer",
                          "monitoring and evaluation", "m&e", "development sector", "humanitarian",
                          "donor", "csr")),
    ("hr", ("human resource", "hr officer", "hr executive", "hr manager", "hr generalist",
            "recruiter", "recruitment", "talent acquisition", "hr business partner")),
    ("accounting_finance", ("accountant", "accounts", "finance officer", "finance manager",
                             "bookkeeper", "auditor", "tax consultant", "payroll")),
    ("engineering", ("engineer", "engineering", "mechanical", "electrical", "civil engineer")),
    ("trades", ("electrician", "plumber", "welder", "carpenter", "mechanic", "mason",
                 "labourer", "laborer", "machine operator", "fitter")),
    ("logistics_supply_chain", ("logistics", "supply chain", "warehouse", "procurement",
                                  "dispatch", "fleet", "courier", "delivery rider", "driver")),
    ("hospitality_travel", ("hotel", "hospitality", "travel consultant", "tourism", "housekeeping",
                              "front office", "concierge")),
    ("retail", ("cashier", "retail", "store manager", "sales associate", "showroom", "merchandiser")),
    ("education", ("teacher", "tutor", "instructor", "lecturer", "professor", "academic coordinator")),
    ("admin_clerical", ("admin", "administrative", "office assistant", "receptionist", "clerk",
                          "secretary", "front desk")),
    ("sales_marketing", ("sales executive", "sales officer", "marketing executive",
                            "business development", "sales representative")),
    ("technology_it", ("developer", "software", "programmer", "it officer", "system admin",
                          "network engineer", "data scientist", "data analyst")),
]
_DEFAULT_DOMAIN = "other_unclassified"


def infer_domain(title: str | None) -> str:
    t = (title or "").lower()
    for domain, keywords in _DOMAIN_KEYWORDS:
        if any(kw in t for kw in keywords):
            return domain
    return _DEFAULT_DOMAIN


# --------------------------------------------------------------------------
# Requirement-type classification -- pattern heuristics first, in this
# precedence order (a phrase can only technically match one branch here,
# but where overlap is possible, e.g. "certified" vs a stray digit, this
# order decides it): credential -> experience -> language -> attribute,
# everything else falls through to 'skill'.
# --------------------------------------------------------------------------

_CREDENTIAL_RE = re.compile(
    # Deliberately excludes bare "ms"/"bs"/"ba" -- in this corpus those are
    # overwhelmingly abbreviations for MS Office/MS Excel, not Master's/
    # Bachelor's degrees, the same false-positive collision taxonomy_v1.yaml
    # itself already documents for bare "r" (R&D vs the R language). "mba",
    # "bsc"/"msc" are kept since they're unambiguous in this corpus.
    r"(?<!\w)(bachelor'?s?|master'?s?|mba|bsc|msc|degree|diploma|"
    r"certificat\w*|certified|licen[sc]e[ds]?|licensed|registration|registered|"
    r"pmp|cfa|acca|cpa|cma|shrm)(?!\w)",
    re.IGNORECASE,
)
_EXPERIENCE_RE = re.compile(r"(?<!\w)\d+\+?\s*(years?|yrs?)(?!\w)", re.IGNORECASE)
_LANGUAGES = frozenset({
    "english", "urdu", "punjabi", "sindhi", "pashto", "pushto", "balochi", "saraiki",
    "arabic", "french", "german", "chinese", "mandarin", "spanish", "hindi", "farsi",
    "persian", "turkish",
})
_ATTRIBUTE_RE = re.compile(
    r"(?<!\w)(night shift|morning shift|evening shift|rotational shift|day shift|shift work|"
    r"flexible hours|willing(?:ness)? to travel|own (?:vehicle|car|conveyance|transport)|"
    r"physically fit|able to lift|standing for long|travel required|work from office|"
    r"on[- ]site|relocat\w*|full-time|part-time|permanent|contractual|temporary|internship)(?!\w)",
    re.IGNORECASE,
)


def classify_type(phrase: str) -> str:
    if _CREDENTIAL_RE.search(phrase):
        return "credential"
    if _EXPERIENCE_RE.search(phrase):
        return "experience"
    if set(phrase.split()) & _LANGUAGES:
        return "language"
    if _ATTRIBUTE_RE.search(phrase):
        return "attribute"
    return "skill"


# --------------------------------------------------------------------------
# Candidate aggregation
# --------------------------------------------------------------------------

@dataclass
class CandidateStats:
    postings: set = field(default_factory=set)
    companies: set = field(default_factory=set)
    by_source_postings: dict = field(default_factory=lambda: defaultdict(set))
    by_source_companies: dict = field(default_factory=lambda: defaultdict(set))
    by_domain_postings: dict = field(default_factory=lambda: defaultdict(set))
    by_domain_companies: dict = field(default_factory=lambda: defaultdict(set))
    snippets: list = field(default_factory=list)


def build_candidates(postings: list[dict], label: str = "corpus") -> tuple[dict[str, CandidateStats], int]:
    """Mines candidate n-grams from whichever list of postings is passed in
    -- the full corpus normally, or a blind-spot-restricted subset under
    --gap-focus. Returns (candidates, considered) where `considered` is
    the count of postings that actually had a non-empty description (the
    denominator this candidate set was mined against) -- needed by
    rank_by_lift() below to compare occurrence SHARES across two different
    corpora, not just raw counts."""
    candidates: dict[str, CandidateStats] = defaultdict(CandidateStats)
    considered = 0

    for p in postings:
        description = (p.get("description") or "").strip()
        if not description:
            continue
        considered += 1

        grams = _clean_ngrams_for_posting(_tokenize(description))
        if not grams:
            continue

        posting_id = p["id"]
        company_key = _normalize_company_key(p.get("company"))
        source = p.get("source") or "unknown"
        domain = infer_domain(p.get("title"))

        for gram in grams:
            stats = candidates[gram]
            stats.postings.add(posting_id)
            stats.by_source_postings[source].add(posting_id)
            stats.by_domain_postings[domain].add(posting_id)
            if company_key:
                stats.companies.add(company_key)
                stats.by_source_companies[source].add(company_key)
                stats.by_domain_companies[domain].add(company_key)
            if len(stats.snippets) < CONTEXT_SNIPPETS_PER_CANDIDATE:
                snippet = _extract_snippet(description, gram)
                if snippet and snippet not in stats.snippets:
                    stats.snippets.append(snippet)

    logger.info(f"[{label}] {considered} postings had a non-empty description and were mined for candidates")
    return candidates, considered


def qualify(candidates: dict[str, CandidateStats]) -> dict[str, CandidateStats]:
    return {
        gram: stats for gram, stats in candidates.items()
        if len(stats.postings) >= MIN_DISTINCT_POSTINGS and len(stats.companies) >= MIN_DISTINCT_COMPANIES
    }


# --------------------------------------------------------------------------
# Segmentation -- always ranked by distinct-company count, never raw
# posting/mention frequency.
# --------------------------------------------------------------------------

def segment_by_type(qualified: dict[str, CandidateStats], classified: dict[str, str]) -> dict[str, list[str]]:
    by_type: dict[str, list[str]] = defaultdict(list)
    for gram, t in classified.items():
        by_type[t].append(gram)
    for t in by_type:
        by_type[t].sort(key=lambda g: -len(qualified[g].companies))
    return by_type


def segment_by_source(qualified: dict[str, CandidateStats]) -> dict[str, list[str]]:
    sources = {s for stats in qualified.values() for s in stats.by_source_companies}
    out: dict[str, list[str]] = {}
    for source in sources:
        grams = [g for g, stats in qualified.items() if stats.by_source_companies.get(source)]
        grams.sort(key=lambda g: -len(qualified[g].by_source_companies[source]))
        out[source] = grams
    return out


def segment_by_domain(qualified: dict[str, CandidateStats]) -> dict[str, list[str]]:
    domains = {d for stats in qualified.values() for d in stats.by_domain_companies}
    out: dict[str, list[str]] = {}
    for domain in domains:
        grams = [g for g, stats in qualified.items() if stats.by_domain_companies.get(domain)]
        grams.sort(key=lambda g: -len(qualified[g].by_domain_companies[domain]))
        out[domain] = grams
    return out


# --------------------------------------------------------------------------
# DEPTH METRIC -- the headline blind-spot statistic. Plain zero-match
# coverage undercounts the problem: taxonomy v1's 'soft' and
# 'office_admin' categories (communication, teamwork, MS Office, data
# entry...) match almost any posting regardless of its actual domain, so a
# posting can register as "matched" while being substantively unmeasured.
# SUBSTANTIVE matches exclude those two categories -- everything else in
# this module (the blind-spot corpus for --gap-focus, etc.) is built on
# top of this.
# --------------------------------------------------------------------------

_NON_SUBSTANTIVE_CATEGORIES = frozenset({"soft", "office_admin"})
BLIND_SPOT_MAX_SUBSTANTIVE = 1  # <=1 substantive match defines the blind-spot corpus


def _posting_depth(posting: dict) -> tuple[int, int]:
    """(total distinct matches, substantive distinct matches) for one
    posting, using the exact same matcher extract.py uses in production
    (extract_skills.extract_skills), not a reimplementation."""
    skills = extract_skills.extract_skills(posting)
    total = len(skills)
    substantive = sum(
        1 for s in skills if extract_skills.skill_category(s) not in _NON_SUBSTANTIVE_CATEGORIES
    )
    return total, substantive


def compute_depth(all_postings: list[dict]) -> dict:
    by_source: dict[str, list[int]] = defaultdict(list)
    by_domain: dict[str, list[int]] = defaultdict(list)
    per_posting_substantive: dict[str, int] = {}

    for p in all_postings:
        _total, substantive = _posting_depth(p)
        source = p.get("source") or "unknown"
        domain = infer_domain(p.get("title"))
        by_source[source].append(substantive)
        by_domain[domain].append(substantive)
        per_posting_substantive[p["id"]] = substantive

    def _row(counts: list[int]) -> dict:
        n = len(counts)
        le1 = sum(1 for c in counts if c <= BLIND_SPOT_MAX_SUBSTANTIVE)
        zero = sum(1 for c in counts if c == 0)
        return {
            "n_postings": n,
            "median": statistics.median(counts) if counts else None,
            "mean": round(statistics.fmean(counts), 2) if counts else None,
            "pct_le1": round(le1 / n * 100, 2) if n else None,
            "pct_zero": round(zero / n * 100, 2) if n else None,
        }

    by_source_rows = {s: _row(counts) for s, counts in by_source.items()}
    by_domain_rows = {d: _row(counts) for d, counts in by_domain.items()}

    return {
        "by_source": dict(sorted(by_source_rows.items(), key=lambda kv: -(kv[1]["pct_le1"] or 0))),
        "by_domain": dict(sorted(by_domain_rows.items(), key=lambda kv: -(kv[1]["pct_le1"] or 0))),
        "per_posting_substantive": per_posting_substantive,
    }


# --------------------------------------------------------------------------
# Optional Groq pass -- GAP-FOCUS TOP 100 ONLY, never the full candidate
# set. Proposal only, logged verbatim to its own JSON file, never applied.
# Uses `requests` directly against Groq's OpenAI-compatible endpoint so no
# new dependency is needed. Sends counts/labels only, never raw posting
# text or snippets.
# --------------------------------------------------------------------------

def _count_new_category_proposals(raw: dict, existing_categories_lower: set[str]) -> int:
    """Parses the model's JSON array out of the chat-completion response
    and counts proposals whose category isn't one of taxonomy v1's
    existing 11 -- the direct signal of how far a v2 taxonomy would need
    to expand. Defensive: falls back to comparing proposed_category
    against the existing set if the model didn't set is_new_category
    itself; returns 0 (logged) if the response isn't parseable JSON."""
    try:
        content = raw["choices"][0]["message"]["content"]
        proposals = json.loads(content)
    except Exception as exc:
        logger.warning(f"Could not parse Groq response content as JSON ({exc}) -- new-category count unavailable")
        return 0

    count = 0
    for item in proposals:
        is_new = item.get("is_new_category")
        if is_new is None:
            is_new = str(item.get("proposed_category", "")).strip().casefold() not in existing_categories_lower
        if is_new:
            count += 1
    return count


def run_groq_pass(
    gap_ranked: list[tuple[str, float]],
    gap_qualified: dict[str, CandidateStats],
    existing_categories: list[str],
    report_date: str,
) -> tuple[dict | None, int]:
    """Sends the gap-focus top-100-by-lift list (and ONLY that list -- see
    module docstring) to Groq for a proposed requirement type/category/
    rationale/confidence per candidate. Returns (raw_response,
    new_category_count). Raw response is written verbatim to
    output/drift_groq_{date}.json, gitignored, never folded wholesale into
    the markdown report (only a summary count is)."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning(
            "GROQ_API_KEY not set -- skipping the optional Groq pass. "
            "(Applies to the gap-focus list only regardless; proposal-only regardless of that: "
            "taxonomy_v1.yaml is never written by this script.)"
        )
        return None, 0

    payload_candidates = [
        {
            "phrase": gram,
            "heuristic_type": classify_type(gram),
            "lift": round(lift, 3),
            "distinct_postings": len(gap_qualified[gram].postings),
            "distinct_companies": len(gap_qualified[gram].companies),
        }
        for gram, lift in gap_ranked
    ]
    prompt = (
        "You are assisting a job-market analytics project that maintains a fixed skill taxonomy "
        "(v1) with these existing categories: " + ", ".join(existing_categories) + ". Below is a "
        "list of candidate requirement phrases mined SPECIFICALLY from postings the current "
        "taxonomy substantively fails to measure (the 'blind spot' corpus) -- only each phrase, a "
        "heuristic type guess, its lift (how much more common it is in the blind-spot corpus than "
        "the full corpus), and its distinct-posting/distinct-company counts are given; no raw "
        "posting text. For each candidate, propose: proposed_requirement_type (one of skill, "
        "credential, experience, language, attribute), proposed_category (one of the existing "
        "categories above if one genuinely fits, otherwise a new category name), is_new_category "
        "(boolean, true if proposed_category is not one of the existing ones), a one-line "
        "rationale, and confidence (a number between 0 and 1). This is a proposal only -- nothing "
        "you return is applied automatically. Respond with a JSON array of objects with keys: "
        "phrase, proposed_requirement_type, proposed_category, is_new_category, rationale, "
        "confidence.\n\n" + json.dumps(payload_candidates, ensure_ascii=False)
    )

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.error(f"Groq pass failed: {exc}")
        return None, 0

    OUTPUT_DIR.mkdir(exist_ok=True)
    groq_path = OUTPUT_DIR / f"drift_groq_{report_date}.json"
    groq_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Groq raw response (proposal only, NOT applied to taxonomy_v1.yaml) written to {groq_path}")

    existing_lower = {c.casefold() for c in existing_categories}
    new_category_count = _count_new_category_proposals(raw, existing_lower)
    logger.info(
        f"Groq proposed a NEW category (not one of taxonomy v1's {len(existing_categories)}) for "
        f"{new_category_count} of {len(gap_ranked)} gap-focus candidate(s) -- this is the direct "
        f"signal of how far a taxonomy v2 would need to expand beyond v1's categories."
    )
    return raw, new_category_count


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _source_split(stats: CandidateStats) -> str:
    parts = sorted(stats.by_source_postings.items(), key=lambda kv: -len(kv[1]))
    return ", ".join(f"{source}:{len(ids)}" for source, ids in parts)


def _domain_split(stats: CandidateStats) -> str:
    parts = sorted(stats.by_domain_postings.items(), key=lambda kv: -len(kv[1]))
    return ", ".join(f"{domain}:{len(ids)}" for domain, ids in parts)


# --------------------------------------------------------------------------
# Gap-focus: lift ranking over the blind-spot corpus only (see DEPTH METRIC
# above for what defines that corpus).
# --------------------------------------------------------------------------

def rank_by_lift(
    gap_qualified: dict[str, CandidateStats],
    gap_considered: int,
    full_candidates: dict[str, CandidateStats],
    full_considered: int,
) -> list[tuple[str, float]]:
    """Candidates that qualify WITHIN the blind-spot corpus, ranked by
    LIFT = (share of occurrences within the blind-spot corpus) / (share
    within the full corpus) -- never by distinct-company count. Company-
    count ranking would just resurface generic business vocabulary common
    everywhere; lift surfaces vocabulary doing disproportionate work
    SPECIFICALLY in postings the taxonomy cannot currently see.

    `full_candidates` is the RAW (pre-qualify()) full-corpus candidate
    dict -- any gram that meets the min-evidence bar within the (smaller)
    blind-spot corpus necessarily also meets it in the (larger) full
    corpus that contains it, so it is always present there too; using the
    raw dict rather than re-deriving anything keeps this a pure lookup.
    """
    ranked: list[tuple[str, float]] = []
    if gap_considered == 0 or full_considered == 0:
        return ranked
    for gram, stats in gap_qualified.items():
        full_stats = full_candidates.get(gram)
        if full_stats is None:
            continue
        full_share = len(full_stats.postings) / full_considered
        if full_share == 0:
            continue
        gap_share = len(stats.postings) / gap_considered
        ranked.append((gram, gap_share / full_share))
    ranked.sort(key=lambda kv: -kv[1])
    return ranked


def _print_console(by_type: dict[str, list[str]], qualified: dict[str, CandidateStats]) -> None:
    print(f"\n{'=' * 70}\nTOP {TOP_N_CONSOLE} CANDIDATES PER REQUIREMENT TYPE (ranked by distinct companies)\n{'=' * 70}")
    for t in REQUIREMENT_TYPES:
        grams = by_type.get(t, [])
        print(f"\n-- {t} ({len(grams)} qualifying) --")
        if not grams:
            print("  (none)")
            continue
        for gram in grams[:TOP_N_CONSOLE]:
            stats = qualified[gram]
            print(
                f"  {gram!r:<40} postings={len(stats.postings):<4} companies={len(stats.companies):<4} "
                f"sources=[{_source_split(stats)}]"
            )


def _print_depth_table(rows: dict[str, dict]) -> None:
    header = f"{'':<24}{'n':<6}{'median':<8}{'mean':<8}{'%<=1':<8}{'%=0':<8}"
    print(header)
    print("-" * len(header))
    for key, row in rows.items():
        print(
            f"{key:<24}{row['n_postings']:<6}{row['median']:<8}{row['mean']:<8}"
            f"{row['pct_le1']:<8}{row['pct_zero']:<8}"
        )


def _print_depth(depth: dict) -> None:
    print(f"\n{'=' * 70}\nDEPTH -- SUBSTANTIVE TAXONOMY COVERAGE (headline blind-spot statistic)\n{'=' * 70}")
    print(
        "Substantive = distinct taxonomy matches EXCLUDING 'soft' and 'office_admin' categories, "
        "which match almost any posting regardless of actual domain. Sorted by %% with <=1 "
        "substantive match, descending -- that column is the actual measure of the blind spot.\n"
    )
    print("By source:")
    _print_depth_table(depth["by_source"])
    print("\nBy inferred domain:")
    _print_depth_table(depth["by_domain"])


def _print_gap_focus_console(
    ranked: list[tuple[str, float]], gap_qualified: dict[str, CandidateStats], n: int = 25
) -> None:
    print(
        f"\n{'=' * 70}\nGAP-FOCUS -- TOP {n} CANDIDATES BY LIFT "
        f"(blind-spot corpus: postings with <= {BLIND_SPOT_MAX_SUBSTANTIVE} substantive match)\n{'=' * 70}"
    )
    if not ranked:
        print("  (no candidates met the minimum evidence bar within the blind-spot corpus)")
        return
    for gram, lift in ranked[:n]:
        stats = gap_qualified[gram]
        t = classify_type(gram)
        print(
            f"  {gram!r:<38} type={t:<10} lift={lift:<6.2f} postings={len(stats.postings):<4} "
            f"companies={len(stats.companies):<4} sources=[{_source_split(stats)}]"
        )


def _write_report(
    depth: dict,
    qualified: dict[str, CandidateStats],
    classified: dict[str, str],
    by_type: dict[str, list[str]],
    by_source: dict[str, list[str]],
    by_domain: dict[str, list[str]],
    gap_data: dict | None,
    groq_raw: dict | None,
    new_category_count: int,
    report_date: str,
) -> Path:
    """Section order is deliberate (TASK 4): depth table first (headline
    stat), then the four small/complete requirement-type segments, then
    gap-focus (the section actually worth reading -- taxonomy-blind-spot-
    specific signal), then the large/noisy skill bucket, then the
    supplementary full-corpus by-source/by-domain rankings last. Reviewable
    material comes first; the raw tail comes last."""
    lines: list[str] = []
    lines.append(f"# Rukhwise Requirement Drift Report -- {report_date}\n")
    lines.append(
        "Analysis only. This report never mutates `taxonomy_v1.yaml`; it exists to show what "
        "taxonomy v1 currently cannot see, now that the corpus spans food service, retail, "
        "accounting, HR, engineering, NGO, admin and trades postings via Indeed/LinkedIn, not just "
        "the technology/business-support roles it was built against.\n"
    )

    # ---- 1. Depth table (headline) --------------------------------------
    lines.append("## Depth: substantive taxonomy coverage (headline blind-spot statistic)\n")
    lines.append(
        "Substantive = distinct taxonomy matches EXCLUDING the 'soft' and 'office_admin' "
        "categories, which match almost any posting regardless of actual domain -- a posting can "
        "register as \"matched\" while being substantively unmeasured. Sorted by % of postings "
        "with <=1 substantive match, descending; that column is the actual measure of the blind "
        "spot, replacing plain zero-match coverage.\n"
    )
    lines.append("### By source\n")
    lines.append("| source | n | median | mean | % <=1 substantive | % 0 substantive |")
    lines.append("|---|---|---|---|---|---|")
    for source, row in depth["by_source"].items():
        lines.append(
            f"| {source} | {row['n_postings']} | {row['median']} | {row['mean']} | "
            f"{row['pct_le1']} | {row['pct_zero']} |"
        )
    lines.append("\n### By inferred domain\n")
    lines.append("| domain | n | median | mean | % <=1 substantive | % 0 substantive |")
    lines.append("|---|---|---|---|---|---|")
    for domain, row in depth["by_domain"].items():
        lines.append(
            f"| {domain} | {row['n_postings']} | {row['median']} | {row['mean']} | "
            f"{row['pct_le1']} | {row['pct_zero']} |"
        )

    lines.append(
        f"\n## Candidate requirement phrases (full corpus, >= {MIN_DISTINCT_POSTINGS} distinct "
        f"postings, >= {MIN_DISTINCT_COMPANIES} distinct companies, not already a taxonomy v1 alias)\n"
    )
    lines.append(f"**{len(qualified)} candidates qualify.**\n")

    # ---- 2. Requirement-type segments, credential/experience/language/
    #         attribute IN FULL (small, complete) --------------------------
    lines.append("## By requirement type: credential, experience, language, attribute (in full)\n")
    for t in ("credential", "experience", "language", "attribute"):
        grams = by_type.get(t, [])
        lines.append(f"### {t} ({len(grams)})\n")
        if not grams:
            lines.append("(none)\n")
            continue
        for gram in grams:
            stats = qualified[gram]
            lines.append(f"**`{gram}`** -- postings={len(stats.postings)}, companies={len(stats.companies)}")
            lines.append(f"- source split: {_source_split(stats) or '(none)'}")
            for snippet in stats.snippets:
                lines.append(f"- snippet: “{snippet}”")
            lines.append("")

    # ---- 3. Gap-focus: the section worth reading first after depth ------
    lines.append("## Gap-focus: top candidates by LIFT in the blind-spot corpus\n")
    lines.append(
        "Blind-spot corpus = postings with <= "
        f"{BLIND_SPOT_MAX_SUBSTANTIVE} substantive taxonomy match (see Depth above). Ranked by "
        "LIFT (share of occurrences in the blind-spot corpus / share in the full corpus), NOT by "
        "distinct-company count -- company-count ranking would just resurface generic business "
        "vocabulary common everywhere; lift surfaces vocabulary doing disproportionate work "
        "specifically where the taxonomy cannot currently see.\n"
    )
    if gap_data is None:
        lines.append("_--gap-focus was not enabled for this run._\n")
    else:
        gap_qualified = gap_data["qualified"]
        gap_top = gap_data["ranked"][:100]
        lines.append(f"**{len(gap_data['ranked'])} candidates qualify within the blind-spot corpus; top {len(gap_top)} shown.**\n")
        for gram, lift in gap_top:
            stats = gap_qualified[gram]
            t = classify_type(gram)
            lines.append(
                f"**`{gram}`** -- type={t}, lift={lift:.2f}, postings={len(stats.postings)}, "
                f"companies={len(stats.companies)}"
            )
            lines.append(f"- source split: {_source_split(stats) or '(none)'}")
            lines.append(f"- domain split: {_domain_split(stats) or '(none)'}")
            for snippet in stats.snippets:
                lines.append(f"- snippet: “{snippet}”")
            lines.append("")

        if groq_raw is not None:
            groq_path = OUTPUT_DIR / f"drift_groq_{report_date}.json"
            lines.append(
                f"**Groq proposals (PROPOSAL ONLY, not applied to taxonomy_v1.yaml):** raw response "
                f"logged verbatim to `{groq_path}`. Proposed a NEW category (not one of taxonomy "
                f"v1's existing categories) for **{new_category_count} of {len(gap_top)}** "
                "gap-focus candidates -- the direct signal of how far a taxonomy v2 would need to "
                "expand.\n"
            )

    # ---- 4. Skill bucket, capped (large/noisy, at the end) ---------------
    lines.append("## By requirement type: skill (fallback bucket, capped)\n")
    lines.append(
        f"Full detail (source split + snippets) capped at the top {REPORT_DETAIL_CAP}, ranked by "
        "distinct companies -- the tail beyond that is the least-evidenced, not hidden signal. See "
        "By source / By domain below for the full ranked list (phrase + counts only).\n"
    )
    skill_grams = by_type.get("skill", [])
    lines.append(f"### skill ({len(skill_grams)})\n")
    shown = skill_grams[:REPORT_DETAIL_CAP]
    for gram in shown:
        stats = qualified[gram]
        lines.append(f"**`{gram}`** -- postings={len(stats.postings)}, companies={len(stats.companies)}")
        lines.append(f"- source split: {_source_split(stats) or '(none)'}")
        for snippet in stats.snippets:
            lines.append(f"- snippet: “{snippet}”")
        lines.append("")
    if len(skill_grams) > len(shown):
        lines.append(f"_...{len(skill_grams) - len(shown)} more skill candidates, lower-ranked, omitted from full detail._\n")

    # ---- 5. Full-corpus by-source / by-domain rankings, raw tail ---------
    lines.append("## By source (full corpus, ranked within source by distinct companies)\n")
    for source, grams in by_source.items():
        lines.append(f"### {source} ({len(grams)})\n")
        lines.append("| phrase | type | companies | postings |")
        lines.append("|---|---|---|---|")
        shown = grams[:REPORT_SEGMENT_CAP]
        for gram in shown:
            stats = qualified[gram]
            lines.append(
                f"| `{gram}` | {classified[gram]} | {len(stats.by_source_companies[source])} | "
                f"{len(stats.by_source_postings[source])} |"
            )
        if len(grams) > len(shown):
            lines.append(f"| _...{len(grams) - len(shown)} more, lower-ranked_ | | | |")
        lines.append("")

    lines.append("## By inferred domain (full corpus, ranked within domain by distinct companies)\n")
    for domain, grams in by_domain.items():
        lines.append(f"### {domain} ({len(grams)})\n")
        lines.append("| phrase | type | companies | postings |")
        lines.append("|---|---|---|---|")
        shown = grams[:REPORT_SEGMENT_CAP]
        for gram in shown:
            stats = qualified[gram]
            lines.append(
                f"| `{gram}` | {classified[gram]} | {len(stats.by_domain_companies[domain])} | "
                f"{len(stats.by_domain_postings[domain])} |"
            )
        if len(grams) > len(shown):
            lines.append(f"| _...{len(grams) - len(shown)} more, lower-ranked_ | | | |")
        lines.append("")

    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"drift_report_{report_date}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def run_drift(gap_focus: bool = False, with_groq: bool = False) -> None:
    from storage import get_postings_for_drift

    report_date = datetime.now(timezone.utc).date().isoformat()

    postings = get_postings_for_drift()
    logger.info(f"Loaded {len(postings)} postings for drift analysis")

    depth = compute_depth(postings)
    _print_depth(depth)

    candidates, full_considered = build_candidates(postings, label="full corpus")
    qualified = qualify(candidates)
    classified = {gram: classify_type(gram) for gram in qualified}
    logger.info(
        f"{len(candidates)} raw candidate n-grams mined (full corpus), {len(qualified)} qualify "
        f"(>= {MIN_DISTINCT_POSTINGS} postings, >= {MIN_DISTINCT_COMPANIES} companies, "
        f"not already a taxonomy v1 alias)"
    )

    by_type = segment_by_type(qualified, classified)
    by_source = segment_by_source(qualified)
    by_domain = segment_by_domain(qualified)

    _print_console(by_type, qualified)

    gap_data = None
    if gap_focus:
        blindspot_ids = {
            pid for pid, substantive in depth["per_posting_substantive"].items()
            if substantive <= BLIND_SPOT_MAX_SUBSTANTIVE
        }
        blindspot_postings = [p for p in postings if p["id"] in blindspot_ids]
        gap_candidates_raw, gap_considered = build_candidates(blindspot_postings, label="blind-spot corpus")
        gap_qualified = qualify(gap_candidates_raw)
        ranked = rank_by_lift(gap_qualified, gap_considered, candidates, full_considered)
        logger.info(
            f"gap-focus: {len(blindspot_postings)}/{len(postings)} postings are in the blind-spot "
            f"corpus, {len(gap_qualified)} candidates qualify within it, ranked by lift"
        )
        _print_gap_focus_console(ranked, gap_qualified, n=25)
        gap_data = {"ranked": ranked, "qualified": gap_qualified}
    else:
        logger.info("--gap-focus not enabled -- skipping blind-spot lift ranking")

    groq_raw = None
    new_category_count = 0
    if with_groq:
        if gap_data is None:
            logger.warning(
                "--with-groq requires --gap-focus (it applies to the gap-focus list only) -- skipping"
            )
        else:
            existing_categories = list(extract_skills._TAXONOMY["categories"].keys())
            groq_raw, new_category_count = run_groq_pass(
                gap_data["ranked"][:100], gap_data["qualified"], existing_categories, report_date
            )

    report_path = _write_report(
        depth, qualified, classified, by_type, by_source, by_domain,
        gap_data, groq_raw, new_category_count, report_date,
    )
    logger.info(f"Report written to {report_path}")
    print(f"\nFull report: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rukhwise requirement-drift discovery (read-only analysis, never mutates taxonomy_v1.yaml)"
    )
    parser.add_argument(
        "--gap-focus", action="store_true",
        help="Also rank candidates by lift within the blind-spot corpus (postings with <=1 substantive match)"
    )
    parser.add_argument(
        "--with-groq", action="store_true",
        help="Also run the optional Groq proposal pass on the gap-focus top 100 only (requires --gap-focus)"
    )
    args = parser.parse_args()
    run_drift(gap_focus=args.gap_focus, with_groq=args.with_groq)


if __name__ == "__main__":
    main()
