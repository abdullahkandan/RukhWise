"""Rukhwise taxonomy v3 skill-gap discovery: full-text LLM extraction of
concrete skills/tools/equipment for domains the taxonomy substantively
fails to measure, followed by cross-company aggregation and a human
approval sheet. PROPOSAL ONLY -- this script has no write path to any
taxonomy file; nothing here is ever applied automatically.

  python skill_gap_discovery.py
      TASK 1: selects postings with skill_substantive<=1 (the same
      "blind-spot" definition compare_taxonomy_depth.py/drift.py already
      use), grouped by the CORRECTED domain (postings.domain, written by
      domain_classifier.py's three-stage classification -- not drift.py's
      older title-only infer_domain()). Prints per-domain target counts
      before any LLM call is made.

      TASK 2: for every domain with >=15 target postings (excluding
      'other', which isn't a coherent domain to build a taxonomy category
      around -- see EXCLUDED_DOMAINS below), batches FULL description
      text to Groq, 5 postings per call, asking for concrete skills/
      tools/equipment/techniques/competencies as short noun phrases.
      This is deliberately NOT the n-gram-mining approach drift.py uses
      for its candidate lists -- n-gram mining on trades/healthcare/
      construction text produced unusable bare nouns (see the prior
      drift round). Raw responses are logged verbatim, per domain, to
      output/skill_extraction_{domain}_{date}.json (gitignored).

      TASK 3: aggregates proposed phrases per domain -- normalizes case/
      whitespace only (no fuzzy/synonym merging, to avoid false merges).
      A (phrase, posting) pair only counts at all if the phrase is found
      as an exact case-insensitive substring in THAT posting's own
      description text -- a phrase the model proposed but that never
      appears verbatim in the source is dropped outright, never shown
      with a fabricated snippet (this caught a real contamination bug in
      the first run: the model echoing the extraction prompt's own
      illustrative example regardless of actual posting content -- see
      output/taxonomy_v3_skills_2026-07-24_run1_backup.md). A surviving
      candidate additionally needs >=2 distinct verbatim-confirmed
      postings, and a distinct-company count scaled by domain size
      (DOMAIN_SIZE_THRESHOLD/LARGE_DOMAIN_MIN_COMPANIES/
      SMALL_DOMAIN_MIN_COMPANIES below) -- a flat >=3-company bar was
      found to suppress exactly the small domains this tool exists to
      serve. Ranks by distinct companies then distinct postings, and
      attaches up to 2 real verbatim snippets.

      TASK 4: runs a second, small Groq pass over the surviving
      candidates (phrase + counts only, no raw text) asking for a
      proposed category -- one of taxonomy_v2.yaml's existing categories
      if one genuinely fits, otherwise a new one -- mirroring the exact
      question drift.py's own Groq pass answers for taxonomy v2. Writes
      output/taxonomy_v3_skills_{date}.md, grouped by domain, capped at
      the top 40 candidates per domain, and prints a per-domain console
      summary (targets, survivors, proposed new categories, and how many
      distinct proposed phrases were dropped outright for having zero
      verbatim match anywhere -- the size of that number is a direct
      measure of how much the model was inventing rather than reading).

  python skill_gap_discovery.py --domains trades_technical,healthcare
      Restricts TASK 2-4 to the named domains (still gated by the
      >=15-target qualification bar) -- used to re-run a subset without
      re-spending Groq quota on domains whose results are being kept
      as-is from a prior run.

Requires GROQ_API_KEY. A batch that fails (network error, rate limit
exhausted after retries, unparseable response) is logged and simply
contributes no phrases/no category proposal for its postings/candidates
-- never silently retried forever or allowed to abort the whole run.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

import extract_skills  # noqa: E402 -- reuse the already-loaded taxonomy_v2 as the one source of truth for existing categories

OUTPUT_DIR = Path("output")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_MAX_RETRIES = 5
GROQ_BATCH_DELAY_SECONDS = 2.5

EXTRACTION_BATCH_SIZE = 5    # full descriptions per call -- kept small to stay within context limits
CATEGORY_BATCH_SIZE = 40     # candidates per call for the (much lighter) categorization pass
DESCRIPTION_MAX_CHARS = 4000  # defensive cap against a pathologically long outlier; typical postings are far shorter

MIN_TARGET_POSTINGS_PER_DOMAIN = 15
MIN_VERBATIM_POSTINGS = 2   # a candidate needs >=2 distinct postings where the phrase is verbatim-confirmed
DOMAIN_SIZE_THRESHOLD = 60
LARGE_DOMAIN_MIN_COMPANIES = 3  # domains with >=DOMAIN_SIZE_THRESHOLD target postings
SMALL_DOMAIN_MIN_COMPANIES = 2  # domains below that -- a flat >=3 bar suppressed exactly these
APPROVAL_SHEET_CAP = 40
SNIPPET_WINDOW_CHARS = 60
SNIPPETS_PER_CANDIDATE = 2


def _min_companies_for(target_count: int) -> int:
    return LARGE_DOMAIN_MIN_COMPANIES if target_count >= DOMAIN_SIZE_THRESHOLD else SMALL_DOMAIN_MIN_COMPANIES

# 'other' is the domain classifier's residual bucket (unclassified/
# ambiguous/malformed postings), not a coherent domain -- extracting
# "skills" for it wouldn't build a usable domain-specific taxonomy
# category, it would just produce a grab-bag. Still reported in TASK 1's
# target-count table for transparency, just excluded from extraction
# onward.
EXCLUDED_DOMAINS = frozenset({"other"})

_NON_SUBSTANTIVE_CATEGORIES = frozenset({"soft", "office_admin"})
_CURRENT_EXTRACTION_METHOD = "taxonomy_v3"


def _report_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# TASK 1: target selection
# --------------------------------------------------------------------------

def _skill_substantive_counts(mentions: list[dict]) -> dict[str, int]:
    """posting_id -> distinct requirement_type='skill' matches, excluding
    the soft/office_admin categories, from the CURRENT (taxonomy_v2)
    extraction pass only. Identical definition to compare_taxonomy_depth's
    skill_substantive."""
    by_posting: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        if m.get("extraction_method") != _CURRENT_EXTRACTION_METHOD:
            continue
        if m.get("requirement_type") != "skill":
            continue
        if m.get("category") in _NON_SUBSTANTIVE_CATEGORIES:
            continue
        by_posting[m["posting_id"]].add(m["skill"])
    return {pid: len(skills) for pid, skills in by_posting.items()}


def select_targets(postings: list[dict], mentions: list[dict]) -> dict[str, list[dict]]:
    """Returns domain -> list of target postings (skill_substantive<=1),
    for EVERY domain present in the data (including 'other', for
    transparency -- callers decide whether to act on it)."""
    substantive_counts = _skill_substantive_counts(mentions)
    targets_by_domain: dict[str, list[dict]] = defaultdict(list)
    for p in postings:
        domain = p.get("domain") or "other"
        count = substantive_counts.get(p["id"], 0)
        if count <= 1:
            targets_by_domain[domain].append(p)
    return targets_by_domain


def print_target_report(postings: list[dict], targets_by_domain: dict[str, list[dict]]) -> None:
    total_by_domain: dict[str, int] = defaultdict(int)
    for p in postings:
        total_by_domain[p.get("domain") or "other"] += 1

    all_domains = sorted(total_by_domain, key=lambda d: -len(targets_by_domain.get(d, [])))
    print(f"\n{'=' * 78}\nTASK 1 -- TARGET SELECTION (skill_substantive<=1, by corrected domain)\n{'=' * 78}")
    header = f"{'domain':<26}{'targets':<10}{'total':<10}{'%':<8}{'qualifies (>=15)':<18}"
    print(header)
    print("-" * len(header))
    total_targets = 0
    for domain in all_domains:
        targets = len(targets_by_domain.get(domain, []))
        total = total_by_domain[domain]
        pct = round(targets / total * 100, 1) if total else 0.0
        qualifies = "yes" if (targets >= MIN_TARGET_POSTINGS_PER_DOMAIN and domain not in EXCLUDED_DOMAINS) else (
            "excluded" if domain in EXCLUDED_DOMAINS else "no"
        )
        print(f"{domain:<26}{targets:<10}{total:<10}{pct:<8}{qualifies:<18}")
        total_targets += targets
    print("-" * len(header))
    print(f"Total target postings (skill_substantive<=1): {total_targets} / {len(postings)}")


# --------------------------------------------------------------------------
# Groq plumbing (shared by the extraction pass and the categorization pass)
# --------------------------------------------------------------------------

def _extract_json_array(content: str) -> str:
    """Groq sometimes wraps the requested JSON array in markdown code
    fences and/or leading prose despite being asked for JSON only -- same
    defensive extraction used in drift.py and domain_classifier.py, PLUS
    one more failure mode observed live from the smaller 8b-instant model:
    it sometimes "thinks out loud" through 2-3 revisions in a single
    response, each in its own ```json fence, with prose in between. A
    naive first-'['-to-last-']' span (the original approach) then
    swallows every revision plus the prose into one invalid blob. Fixed
    by (1) preferring the LAST fenced block if multiple exist -- the
    model's final answer -- and (2) falling back to a proper bracket-
    depth scan (respecting string literals) for the FIRST complete
    top-level array when there are no fences at all, rather than assuming
    only one array is present anywhere in the text."""
    text = content.strip()

    fence_matches = re.findall(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence_matches:
        return fence_matches[-1]

    start = text.find("[")
    if start == -1:
        return text

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _groq_call(prompt: str, api_key: str) -> list | None:
    """POST once with Retry-After-aware backoff on 429, capped at
    GROQ_MAX_RETRIES. Returns the parsed JSON array, or None if every
    attempt failed (network error, non-429 HTTP error, or unparseable
    response) -- callers must treat None as "no result for this batch,"
    never crash the whole run over it."""
    for attempt in range(GROQ_MAX_RETRIES):
        try:
            resp = requests.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=90,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else (2 ** attempt) * 3
                logger.warning(f"Groq rate-limited (attempt {attempt + 1}/{GROQ_MAX_RETRIES}), waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            raw = resp.json()
            content = raw["choices"][0]["message"]["content"]
            return json.loads(_extract_json_array(content))
        except Exception as exc:
            logger.error(f"Groq call failed: {exc}")
            return None
    logger.error("Groq call exhausted retries on repeated 429 rate limiting")
    return None


# --------------------------------------------------------------------------
# TASK 2: full-text extraction
# --------------------------------------------------------------------------

EXTRACTION_PROMPT_INSTRUCTIONS = (
    "For each job posting below (id, title, full description), extract the concrete skills, "
    "tools, equipment, techniques, and competencies this employer is asking a candidate to have. "
    "Return a flat list of short noun phrases per posting, using wording that appears in or "
    "closely tracks the posting's own text -- never invent a phrase that isn't grounded in what "
    "this specific posting actually says. Do NOT return generic business verbs (e.g. 'manage', "
    "'coordinate'), soft skills (e.g. 'communication', 'teamwork'), employment conditions (e.g. "
    "'full time', 'on-site'), degrees/education level, or years of experience -- those are "
    "captured elsewhere. Do NOT return single generic nouns (e.g. 'safety', 'equipment') when the "
    "posting states something more specific -- extract the specific phrase the posting actually "
    "uses instead. If a posting genuinely has nothing extractable, return an empty list for it. "
    "Respond with a JSON array of objects with keys: id, skills (array of strings). Every posting "
    "id given below must appear exactly once in your response.\n\n"
)


def _extraction_payload(batch: list[dict]) -> str:
    postings_json = [
        {
            "id": p["id"],
            "title": p.get("title") or "",
            "description": (p.get("description") or "")[:DESCRIPTION_MAX_CHARS],
        }
        for p in batch
    ]
    return EXTRACTION_PROMPT_INSTRUCTIONS + json.dumps(postings_json, ensure_ascii=False)


def extract_skills_for_domain(domain: str, targets: list[dict], report_date: str) -> dict[str, list[str]]:
    """Returns posting_id -> list of raw extracted phrases (unnormalized,
    exactly as Groq returned them) for every target posting in this
    domain. Writes the full batch record log to
    output/skill_extraction_{domain}_{date}.json regardless of outcome."""
    api_key = os.environ.get("GROQ_API_KEY")
    results: dict[str, list[str]] = {}
    batch_records: list[dict] = []

    if not api_key:
        logger.warning(f"[{domain}] GROQ_API_KEY not set -- skipping full-text extraction ({len(targets)} postings)")
        return results

    batches = [targets[i:i + EXTRACTION_BATCH_SIZE] for i in range(0, len(targets), EXTRACTION_BATCH_SIZE)]
    for batch_index, batch in enumerate(batches):
        if batch_index > 0:
            time.sleep(GROQ_BATCH_DELAY_SECONDS)

        prompt = _extraction_payload(batch)
        parsed = _groq_call(prompt, api_key)
        batch_ids = [p["id"] for p in batch]

        if parsed is None:
            logger.error(f"[{domain}] batch {batch_index + 1}/{len(batches)} failed -- no phrases for {len(batch)} postings")
            batch_records.append({"batch_index": batch_index, "posting_ids": batch_ids, "error": "call failed, see log"})
            continue

        batch_records.append({"batch_index": batch_index, "posting_ids": batch_ids, "response": parsed})

        batch_id_set = set(batch_ids)
        for item in parsed:
            pid = item.get("id")
            if pid not in batch_id_set:
                continue
            skills = item.get("skills") or []
            results[pid] = [s for s in skills if isinstance(s, str) and s.strip()]

        logger.info(f"[{domain}] batch {batch_index + 1}/{len(batches)}: {len(parsed)} postings returned")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"skill_extraction_{domain}_{report_date}.json"
    out_path.write_text(json.dumps(batch_records, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[{domain}] raw extraction responses written to {out_path}")

    return results


# --------------------------------------------------------------------------
# TASK 3: aggregation
# --------------------------------------------------------------------------

def _normalize_phrase(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase.strip()).casefold()


def _find_snippet(description: str, phrase: str, window: int = SNIPPET_WINDOW_CHARS) -> str | None:
    """Returns a real excerpt around the FIRST verbatim (case-insensitive,
    whitespace-tolerant) occurrence of phrase in description, or None if
    it genuinely isn't there -- never fabricated."""
    if not description or not phrase:
        return None
    tokens = phrase.strip().split()
    if not tokens:
        return None
    fragment = r"\s+".join(re.escape(t) for t in tokens)
    match = re.search(fragment, description, re.IGNORECASE)
    if not match:
        return None
    start = max(0, match.start() - window)
    end = min(len(description), match.end() + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(description) else ""
    snippet = re.sub(r"\s+", " ", description[start:end].replace("\n", " ")).strip()
    return f"{prefix}{snippet}{suffix}"


class _Candidate:
    __slots__ = ("display", "postings", "companies", "snippets")

    def __init__(self, display: str):
        self.display = display
        self.postings: set[str] = set()
        self.companies: set[str] = set()
        self.snippets: list[str] = []


class AggregationResult:
    __slots__ = ("survivors", "dropped_by_verbatim", "total_proposed", "min_companies_used")

    def __init__(self, survivors: list[_Candidate], dropped_by_verbatim: int, total_proposed: int, min_companies_used: int):
        self.survivors = survivors
        self.dropped_by_verbatim = dropped_by_verbatim
        self.total_proposed = total_proposed
        self.min_companies_used = min_companies_used


def aggregate_domain(domain: str, targets: list[dict], extracted: dict[str, list[str]], min_companies: int) -> AggregationResult:
    """Normalizes/merges (case+whitespace only), then applies the
    verbatim requirement: a (phrase, posting) pair only counts if the
    phrase is found as an exact case-insensitive substring in THAT
    posting's own description -- a phrase proposed for a posting that
    never actually contains it is dropped for that posting, not merely
    flagged. A candidate survives only with >=MIN_VERBATIM_POSTINGS
    verbatim-confirmed postings AND >=min_companies distinct companies
    (the scaled bar -- see _min_companies_for). Because candidates are
    only ever created on a verbatim hit, every surviving candidate's
    snippets are guaranteed real; there is no "no verbatim match" case
    left to display.

    Also tracks dropped_by_verbatim: how many DISTINCT proposed phrases
    (post-normalization) never had even one verbatim match anywhere in
    their claimed postings -- these never became a candidate at all.
    That count is a direct measure of how much the model was inventing
    rather than reading, independent of whether a phrase would have
    cleared the postings/company bars."""
    postings_by_id = {p["id"]: p for p in targets}
    candidates: dict[str, _Candidate] = {}
    all_proposed_keys: set[str] = set()

    for pid, phrases in extracted.items():
        posting = postings_by_id.get(pid)
        if posting is None:
            continue
        company = posting.get("company") or "(unknown company)"
        description = posting.get("description") or ""
        for raw_phrase in phrases:
            key = _normalize_phrase(raw_phrase)
            if not key:
                continue
            all_proposed_keys.add(key)

            snippet = _find_snippet(description, raw_phrase)
            if snippet is None:
                continue  # no verbatim match in THIS posting -- this (phrase, posting) pair does not count

            cand = candidates.setdefault(key, _Candidate(display=raw_phrase.strip()))
            cand.postings.add(pid)
            cand.companies.add(company)
            if len(cand.snippets) < SNIPPETS_PER_CANDIDATE and snippet not in cand.snippets:
                cand.snippets.append(snippet)

    dropped_by_verbatim = len(all_proposed_keys - set(candidates.keys()))

    survivors = [
        c for c in candidates.values()
        if len(c.postings) >= MIN_VERBATIM_POSTINGS and len(c.companies) >= min_companies
    ]
    survivors.sort(key=lambda c: (-len(c.companies), -len(c.postings)))
    return AggregationResult(survivors, dropped_by_verbatim, len(all_proposed_keys), min_companies)


# --------------------------------------------------------------------------
# TASK 4: category proposal + approval sheet
# --------------------------------------------------------------------------

def _category_prompt(domain: str, candidates: list[_Candidate], existing_categories: list[str]) -> str:
    payload = [
        {
            "phrase": c.display,
            "distinct_postings": len(c.postings),
            "distinct_companies": len(c.companies),
        }
        for c in candidates
    ]
    return (
        f"These are candidate skill/tool/equipment phrases mined from real job postings in the "
        f"'{domain}' domain of a Pakistani job-market analytics project. The project maintains a "
        f"fixed skill taxonomy with these existing categories: {', '.join(existing_categories)}. "
        "For each candidate, propose proposed_category (one of the existing categories above if one "
        "genuinely fits, otherwise a new, short, lowercase_with_underscores category name specific "
        "to this domain) and is_new_category (boolean). Respond with a JSON array of objects with "
        "keys: phrase, proposed_category, is_new_category.\n\n" + json.dumps(payload, ensure_ascii=False)
    )


def propose_categories(domain: str, candidates: list[_Candidate], existing_categories: list[str]) -> dict[str, dict]:
    """phrase (display form) -> {"proposed_category", "is_new_category"}.
    Candidates Groq doesn't return a proposal for (call failure, or the
    model simply omitted them) are left out -- the approval sheet marks
    those as unproposed rather than guessing."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or not candidates:
        return {}

    proposals: dict[str, dict] = {}
    batches = [candidates[i:i + CATEGORY_BATCH_SIZE] for i in range(0, len(candidates), CATEGORY_BATCH_SIZE)]
    for batch_index, batch in enumerate(batches):
        if batch_index > 0:
            time.sleep(GROQ_BATCH_DELAY_SECONDS)
        prompt = _category_prompt(domain, batch, existing_categories)
        parsed = _groq_call(prompt, api_key)
        if parsed is None:
            logger.error(f"[{domain}] category-proposal batch {batch_index + 1}/{len(batches)} failed")
            continue
        for item in parsed:
            phrase = item.get("phrase")
            if phrase:
                proposals[phrase] = item

    return proposals


def build_approval_sheet(
    targets_by_domain: dict[str, list[dict]],
    results_by_domain: dict[str, AggregationResult],
    proposals_by_domain: dict[str, dict[str, dict]],
    existing_categories: list[str],
    report_date: str,
) -> Path:
    existing_lower = {c.casefold() for c in existing_categories}
    lines: list[str] = []
    lines.append(f"# Taxonomy v3 Skill-Gap Approval Sheet -- {report_date}\n")
    lines.append(
        "Built for line-by-line human approval, same as taxonomy v1/v2. Candidates come from "
        "full-text LLM extraction over real posting descriptions (NOT n-gram mining), grouped by "
        "the corrected domain classifier output. A candidate must appear as an exact case-"
        "insensitive substring in >=2 distinct source postings (verbatim requirement -- a phrase "
        "the model proposed but never actually wrote is dropped, not shown), and must clear a "
        "distinct-company bar scaled by domain size (>=3 companies for domains with >=60 target "
        "postings, >=2 for smaller domains). Ranked by distinct companies then distinct postings. "
        "Nothing here is applied to any taxonomy file automatically. Every snippet shown is a real "
        "excerpt found verbatim in a contributing posting.\n"
    )

    for domain in sorted(results_by_domain, key=lambda d: -len(results_by_domain[d].survivors)):
        result = results_by_domain[domain]
        survivors = result.survivors[:APPROVAL_SHEET_CAP]
        proposals = proposals_by_domain.get(domain, {})
        target_count = len(targets_by_domain.get(domain, []))
        lines.append(
            f"## {domain} ({target_count} target postings, min_companies_bar={result.min_companies_used}, "
            f"{len(result.survivors)} candidates survived, showing top {len(survivors)})\n"
        )
        for cand in survivors:
            proposal = proposals.get(cand.display)
            if proposal:
                is_new = bool(proposal.get("is_new_category"))
                if is_new is None:
                    is_new = str(proposal.get("proposed_category", "")).strip().casefold() not in existing_lower
                category = str(proposal.get("proposed_category") or "(uncategorized)")
                category_str = f"**{category}** (NEW)" if is_new else category
            else:
                category_str = "(no proposal -- Groq call failed or omitted)"

            snippet_str = " / ".join(f"“{s}”" for s in cand.snippets) if cand.snippets else "(verified verbatim but no snippet captured)"
            lines.append(
                f"- **{cand.display}** -- postings={len(cand.postings)}, companies={len(cand.companies)}, "
                f"category={category_str}\n  {snippet_str}"
            )
        lines.append("")

    OUTPUT_DIR.mkdir(exist_ok=True)
    sheet_path = OUTPUT_DIR / f"taxonomy_v3_skills_{report_date}.md"
    sheet_path.write_text("\n".join(lines), encoding="utf-8")
    return sheet_path


def print_domain_summary(
    domain: str,
    target_count: int,
    result: AggregationResult,
    proposals: dict[str, dict],
    existing_lower: set[str],
) -> None:
    new_categories = set()
    for cand in result.survivors:
        proposal = proposals.get(cand.display)
        if not proposal:
            continue
        is_new = proposal.get("is_new_category")
        if is_new is None:
            is_new = str(proposal.get("proposed_category", "")).strip().casefold() not in existing_lower
        if is_new:
            new_categories.add(str(proposal.get("proposed_category")))

    print(f"\n{'-' * 70}\n{domain}\n{'-' * 70}")
    print(f"  target postings (skill_substantive<=1):     {target_count}")
    print(f"  distinct-company bar used:                  >={result.min_companies_used}")
    print(f"  distinct proposed phrases (pre-verbatim):    {result.total_proposed}")
    print(f"  dropped -- zero verbatim match anywhere:     {result.dropped_by_verbatim} (measures model invention)")
    print(f"  candidates surviving verbatim + company bar: {len(result.survivors)}")
    print(f"  proposed NEW categories:                     {sorted(new_categories) if new_categories else '(none)'}")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(only_domains: set[str] | None = None) -> None:
    from storage import get_postings_for_skill_gap_analysis, get_skill_mentions_for_analysis

    postings = get_postings_for_skill_gap_analysis()
    mentions = get_skill_mentions_for_analysis()
    logger.info(f"Loaded {len(postings)} postings, {len(mentions)} skill_mentions rows")

    targets_by_domain = select_targets(postings, mentions)
    print_target_report(postings, targets_by_domain)

    qualifying_domains = [
        d for d, targets in targets_by_domain.items()
        if len(targets) >= MIN_TARGET_POSTINGS_PER_DOMAIN and d not in EXCLUDED_DOMAINS
    ]
    if only_domains is not None:
        skipped = [d for d in qualifying_domains if d not in only_domains]
        if skipped:
            print(f"\n--domains restricts processing to {sorted(only_domains)} -- skipping otherwise-qualifying domains: {sorted(skipped)}")
        qualifying_domains = [d for d in qualifying_domains if d in only_domains]

    if not qualifying_domains:
        print(f"\nNo domain has >={MIN_TARGET_POSTINGS_PER_DOMAIN} target postings (excluding {sorted(EXCLUDED_DOMAINS)}) after applying --domains, if given. Nothing to extract.")
        return

    report_date = _report_date()
    existing_categories = list(extract_skills._TAXONOMY["categories"].keys())
    existing_lower = {c.casefold() for c in existing_categories}

    results_by_domain: dict[str, AggregationResult] = {}
    proposals_by_domain: dict[str, dict[str, dict]] = {}

    print(f"\n{'=' * 78}\nTASK 2/3/4 -- FULL-TEXT EXTRACTION, AGGREGATION, CATEGORY PROPOSAL\n{'=' * 78}")
    print(f"Processing domains ({len(qualifying_domains)}): {sorted(qualifying_domains)}")

    for domain in qualifying_domains:
        targets = targets_by_domain[domain]
        min_companies = _min_companies_for(len(targets))
        logger.info(f"[{domain}] extracting from {len(targets)} target postings (company bar >={min_companies})")
        extracted = extract_skills_for_domain(domain, targets, report_date)
        result = aggregate_domain(domain, targets, extracted, min_companies)
        proposals = propose_categories(domain, result.survivors[:APPROVAL_SHEET_CAP], existing_categories)

        results_by_domain[domain] = result
        proposals_by_domain[domain] = proposals
        print_domain_summary(domain, len(targets), result, proposals, existing_lower)

    sheet_path = build_approval_sheet(targets_by_domain, results_by_domain, proposals_by_domain, existing_categories, report_date)
    print(f"\n{'=' * 78}\nApproval sheet written to {sheet_path} (proposal only -- nothing applied to any taxonomy file)\n{'=' * 78}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Rukhwise taxonomy v3 skill-gap discovery")
    parser.add_argument("--domains", type=str, default=None, help="Comma-separated domain keys to restrict TASK 2-4 to (still gated by the target-count qualification bar)")
    args = parser.parse_args()

    only_domains = {d.strip() for d in args.domains.split(",")} if args.domains else None
    run(only_domains=only_domains)


if __name__ == "__main__":
    main()
