"""Taxonomy v3 consolidated approval sheet: merges the tag-based
(skills_raw) and text-based (LLM, description) discovery passes into one
deduplicated, categorized, human-reviewable sheet. PROPOSAL ONLY -- no
write path to any taxonomy file.

  python taxonomy_v3_consolidate.py
      TASK 1: recomputes both passes from already-logged/stored data (no
      new full-corpus Groq extraction calls -- text-pass candidates come
      from the existing output/skill_extraction_{domain}_*.json logs,
      tag-pass candidates come directly from skills_raw) and merges them
      per domain by normalized phrase, marking which pass (tag | text |
      both) produced each candidate. A phrase found by both passes is
      the strongest evidence and is marked accordingly.

      TASK 2: every merged candidate gets ONE requirement_type and ONE
      category, decided GLOBALLY per distinct phrase (not per domain --
      "what the phrase IS, not which domain it came from"), so the same
      phrase never gets categorized differently just because it showed
      up in two different domains. A hand-authored heuristic first
      forces obvious generic workplace qualities to 'soft' and general
      computer-literacy/clerical-routine phrases to 'office_admin'
      regardless of what an LLM call would guess; everything else goes
      to one small Groq categorization pass (phrase+counts only, no raw
      text) with the routing rule stated explicitly, and the returned
      category is still passed through a synonym-canonicalization safety
      net (e.g. 'soft_skills' -> 'soft') in case the model dodges the
      rule with a differently-named equivalent category. substantive =
      category not in {soft, office_admin} -- the same definition used
      everywhere else in this project.

      TASK 3: proposes (never applies) near-duplicate merge groups --
      pairs/chains of candidates whose word sets (ignoring parentheticals
      and common stopwords) are in a subset relation with a small word-
      count delta, e.g. "Electronic Health Records (EHR)" and
      "...Management". Canonical = the shortest phrase in each group.

      TASK 4: writes output/taxonomy_v3_consolidated_{date}.md, grouped
      by domain, and prints a console summary (totals, substantive vs
      soft/office_admin split overall and per domain, merge groups
      proposed).

Requires GROQ_API_KEY for the (small) categorization pass only -- both
discovery passes themselves are recomputed from data already collected,
no new extraction calls.
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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

import extract_skills  # noqa: E402
from skill_gap_discovery import (  # noqa: E402
    select_targets,
    aggregate_domain,
    _min_companies_for,
    _normalize_phrase,
    _groq_call,
    GROQ_BATCH_DELAY_SECONDS,
    CATEGORY_BATCH_SIZE,
)
from skill_gap_source_diagnostic import (  # noqa: E402
    _load_extracted_from_log,
    _bucket,
    QUALIFYING_DOMAINS,
)

OUTPUT_DIR = Path("output")
APPROVAL_SHEET_CAP_PER_DOMAIN = 40

_NON_SUBSTANTIVE = frozenset({"soft", "office_admin"})

# --------------------------------------------------------------------------
# Heuristic routing overrides (TASK 2) -- hard rule, wins regardless of what
# a Groq call returns. Hand-authored from generic workplace-quality /
# clerical-routine vocabulary, cross-checked against what actually showed up
# in the tag-based discovery pass (see skill_gap_source_diagnostic.py's
# output). Matched as whole-phrase substrings, same word-boundary regex
# convention as extract_skills.py.
# --------------------------------------------------------------------------

_SOFT_OVERRIDE_PHRASES = [
    "attention to detail", "organizational skill", "organisational skill",
    "interpersonal skill", "multitasking", "multi tasking", "multi-tasking",
    "conflict resolution", "active listening", "empathy", "problem resolution",
    "adaptability", "professionalism", "positive attitude", "flexibility",
    "target achievement", "community engagement", "work ethic",
]
_OFFICE_ADMIN_OVERRIDE_PHRASES = [
    "basic computer skill", "computer literacy", "keyboard shortcut",
    "email management", "calendar management", "scheduling",
    "appointment scheduling", "document preparation", "document management",
    "record keeping", "office administration", "filing", "office supplies",
    "administrative support", "clerical", "typing skill",
]

_CATEGORY_SYNONYM_MAP = {
    "soft_skills": "soft", "soft_skill": "soft", "interpersonal_skills": "soft",
    "workplace_skills": "soft", "personal_skills": "soft", "behavioral_skills": "soft",
    "workplace_conduct": "soft", "workplace_qualities": "soft", "soft_competencies": "soft",
    "office_skills": "office_admin", "administrative_skills": "office_admin",
    "clerical_skills": "office_admin", "computer_literacy": "office_admin",
    "basic_computing": "office_admin", "office_tasks": "office_admin",
    "admin_skills": "office_admin", "clerical_administration": "office_admin",
}


def _phrase_regex(fragment: str) -> "re.Pattern[str]":
    tokens = fragment.split()
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<!\w)" + pattern + r"(?!\w)", re.IGNORECASE)


_SOFT_OVERRIDE_PATTERNS = [_phrase_regex(p) for p in _SOFT_OVERRIDE_PHRASES]
_OFFICE_ADMIN_OVERRIDE_PATTERNS = [_phrase_regex(p) for p in _OFFICE_ADMIN_OVERRIDE_PHRASES]


def _heuristic_category(phrase: str) -> str | None:
    if any(p.search(phrase) for p in _SOFT_OVERRIDE_PATTERNS):
        return "soft"
    if any(p.search(phrase) for p in _OFFICE_ADMIN_OVERRIDE_PATTERNS):
        return "office_admin"
    return None


def _canonicalize_category(category: str) -> str:
    key = category.strip().casefold().replace(" ", "_").replace("-", "_")
    return _CATEGORY_SYNONYM_MAP.get(key, category)


# --------------------------------------------------------------------------
# TASK 1: merge tag-based + text-based passes, per domain
# --------------------------------------------------------------------------

class MergedCandidate:
    __slots__ = ("key", "display", "domain", "passes", "postings", "companies", "example")

    def __init__(self, key: str, display: str, domain: str):
        self.key = key
        self.display = display
        self.domain = domain
        self.passes: set[str] = set()
        self.postings: set[str] = set()
        self.companies: set[str] = set()
        self.example: str | None = None  # one verbatim snippet (text pass) or tag string (tag pass)


def _tag_pass_candidates(bucket_b: list[dict], min_companies: int) -> dict[str, MergedCandidate]:
    """Same logic as skill_gap_source_diagnostic.run_task2, but returns
    the raw per-candidate data (postings/companies/example) instead of
    printing, and does NOT yet apply the company bar -- callers merge
    first, then filter."""
    raw: dict[str, MergedCandidate] = {}
    for p in bucket_b:
        skills_raw = p.get("skills_raw")
        if not isinstance(skills_raw, list):
            continue
        company = p.get("company") or "(unknown company)"
        for raw_tag in skills_raw:
            tag = str(raw_tag).strip()
            if not tag:
                continue
            already_matched = any(pattern.search(tag.lower()) for _, _, pattern in extract_skills._SKILL_PATTERNS)
            if already_matched:
                continue
            key = _normalize_phrase(tag)
            cand = raw.setdefault(key, MergedCandidate(key, tag, p.get("domain") or "other"))
            cand.passes.add("tag")
            cand.postings.add(p["id"])
            cand.companies.add(company)
            if cand.example is None:
                cand.example = f"(tag) {tag}"
    return raw


def merge_domain(domain: str, targets: list[dict], min_companies: int) -> list[MergedCandidate]:
    bucket_b = [p for p in targets if _bucket(p) == "B_no_description_has_skills_raw"]
    tag_candidates = _tag_pass_candidates(bucket_b, min_companies)

    extracted = _load_extracted_from_log(domain)
    text_result = aggregate_domain(domain, targets, extracted, min_companies)

    merged: dict[str, MergedCandidate] = {}
    for key, cand in tag_candidates.items():
        merged[key] = cand

    for text_cand in text_result.survivors:
        key = _normalize_phrase(text_cand.display)
        if key in merged:
            m = merged[key]
            m.passes.add("text")
            m.postings |= text_cand.postings
            m.companies |= text_cand.companies
            if text_cand.snippets:
                m.example = f"(verbatim) {text_cand.snippets[0]}"
        else:
            m = MergedCandidate(key, text_cand.display, domain)
            m.passes.add("text")
            m.postings |= text_cand.postings
            m.companies |= text_cand.companies
            if text_cand.snippets:
                m.example = f"(verbatim) {text_cand.snippets[0]}"
            merged[key] = m

    survivors = [c for c in merged.values() if len(c.companies) >= min_companies]
    survivors.sort(key=lambda c: (-len(c.companies), -len(c.postings)))
    return survivors


# --------------------------------------------------------------------------
# TASK 2: global (domain-blind) requirement_type + category routing
# --------------------------------------------------------------------------

def _category_prompt(candidates: list[dict], existing_categories: list[str]) -> str:
    payload = [
        {"phrase": c["phrase"], "distinct_postings": c["postings"], "distinct_companies": c["companies"]}
        for c in candidates
    ]
    return (
        "You are categorizing candidate skill/requirement phrases for a Pakistani job-market analytics "
        "project's taxonomy. Existing categories: " + ", ".join(existing_categories) + ". "
        "MANDATORY ROUTING RULE: categorize a phrase by what it fundamentally IS, never by which job "
        "domain it happened to be mentioned in. Generic workplace qualities and soft dispositions "
        "(e.g. attention to detail, teamwork, communication, organizational skills, multitasking, "
        "conflict resolution, time management, adaptability, professionalism, leadership) MUST be "
        "'soft', regardless of frequency or domain. General computer literacy and routine clerical/"
        "office tasks (e.g. basic computer skills, email/calendar management, scheduling, filing, data "
        "entry, record keeping, document preparation) MUST be 'office_admin'. Only propose a new, "
        "specific category for a phrase naming a concrete domain-specific tool, technique, equipment, "
        "or specialized process -- never for a generic trait or routine office task. For each candidate, "
        "return proposed_requirement_type (one of skill, credential, experience, language, attribute), "
        "proposed_category (one of the existing categories if genuinely fitting, otherwise a new short "
        "lowercase_with_underscores name), and is_new_category (boolean). Respond as a JSON array of "
        "objects with keys: phrase, proposed_requirement_type, proposed_category, "
        "is_new_category.\n\n" + json.dumps(payload, ensure_ascii=False)
    )


def categorize_globally(all_candidates_by_key: dict[str, MergedCandidate], existing_categories: list[str]) -> dict[str, dict]:
    """Returns key -> {"requirement_type", "category", "substantive", "source": "heuristic"|"llm"|"uncategorized"}."""
    results: dict[str, dict] = {}
    to_query: list[dict] = []

    for key, cand in all_candidates_by_key.items():
        heuristic = _heuristic_category(cand.display)
        if heuristic:
            results[key] = {"requirement_type": "skill", "category": heuristic, "substantive": False, "source": "heuristic"}
        else:
            to_query.append({"key": key, "phrase": cand.display, "postings": len(cand.postings), "companies": len(cand.companies)})

    api_key = os.environ.get("GROQ_API_KEY")
    if api_key and to_query:
        batches = [to_query[i:i + CATEGORY_BATCH_SIZE] for i in range(0, len(to_query), CATEGORY_BATCH_SIZE)]
        for batch_index, batch in enumerate(batches):
            if batch_index > 0:
                time.sleep(GROQ_BATCH_DELAY_SECONDS)
            prompt = _category_prompt(batch, existing_categories)
            parsed = _groq_call(prompt, api_key)
            if parsed is None:
                logger.error(f"Categorization batch {batch_index + 1}/{len(batches)} failed ({len(batch)} candidates)")
                continue
            by_phrase = {item["phrase"]: item for item in batch}
            for item in parsed:
                phrase = item.get("phrase")
                if phrase not in by_phrase:
                    continue
                key = by_phrase[phrase]["key"]
                category = _canonicalize_category(str(item.get("proposed_category") or "(uncategorized)"))
                req_type = item.get("proposed_requirement_type") or "skill"
                # Heuristic override still wins even post-LLM, as a final safety net
                override = _heuristic_category(all_candidates_by_key[key].display)
                if override:
                    category = override
                results[key] = {
                    "requirement_type": req_type,
                    "category": category,
                    "substantive": category.casefold() not in _NON_SUBSTANTIVE,
                    "source": "llm",
                }
    elif not api_key:
        logger.warning(f"GROQ_API_KEY not set -- {len(to_query)} candidate(s) left uncategorized")

    for key, cand in all_candidates_by_key.items():
        if key not in results:
            results[key] = {"requirement_type": None, "category": None, "substantive": None, "source": "uncategorized"}

    return results


# --------------------------------------------------------------------------
# TASK 3: near-duplicate merge proposals (global, proposal only)
# --------------------------------------------------------------------------

_STOPWORDS = frozenset({"to", "of", "the", "and", "a", "an", "for", "in", "on", "with"})
_MAX_WORD_DELTA = 2
_MIN_MEANINGFUL_WORDS = 2


def _word_set(phrase: str) -> set[str]:
    text = re.sub(r"\([^)]*\)", " ", phrase).casefold()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return {w for w in text.split() if w and w not in _STOPWORDS}


def propose_merge_groups(all_candidates_by_key: dict[str, MergedCandidate]) -> list[list[str]]:
    """Returns a list of groups (each a list of candidate keys, canonical
    first) where one phrase's meaningful-word set is a subset of
    another's with a small word-count delta -- e.g. "Electronic Health
    Records (EHR)" subset-of "...Management". Union-find over pairwise
    subset relations, canonical = fewest words in the group."""
    keys = list(all_candidates_by_key.keys())
    word_sets = {k: _word_set(all_candidates_by_key[k].display) for k in keys}

    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(keys)):
        wi = word_sets[keys[i]]
        if len(wi) < _MIN_MEANINGFUL_WORDS:
            continue
        for j in range(i + 1, len(keys)):
            wj = word_sets[keys[j]]
            if len(wj) < _MIN_MEANINGFUL_WORDS:
                continue
            if wi == wj:
                continue  # identical normalized keys are already merged upstream
            smaller, larger = (wi, wj) if len(wi) <= len(wj) else (wj, wi)
            if smaller.issubset(larger) and (len(larger) - len(smaller)) <= _MAX_WORD_DELTA:
                union(keys[i], keys[j])

    groups: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        groups[find(k)].append(k)

    proposals = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda k: len(word_sets[k]))
        proposals.append(members)
    return proposals


# --------------------------------------------------------------------------
# TASK 4: sheet + console summary
# --------------------------------------------------------------------------

def _report_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build_sheet(
    domain_candidates: dict[str, list[MergedCandidate]],
    categorization: dict[str, dict],
    merge_groups: list[list[str]],
    all_candidates_by_key: dict[str, MergedCandidate],
    report_date: str,
) -> Path:
    merge_group_of: dict[str, int] = {}
    for i, group in enumerate(merge_groups):
        for key in group:
            merge_group_of[key] = i

    lines: list[str] = []
    lines.append(f"# Taxonomy v3 Consolidated Approval Sheet -- {report_date}\n")
    lines.append(
        "Merges the tag-based (skills_raw) and text-based (LLM, description) discovery passes, "
        "deduplicated by normalized phrase. 'both' means the SAME phrase survived independently in "
        "both passes -- the strongest evidence. Category/requirement_type are decided GLOBALLY per "
        "phrase (domain-blind, per instruction) via a heuristic-first, Groq-fallback routing pass; "
        "substantive = category not in {soft, office_admin}. Near-duplicate merge groups are proposed, "
        "never applied -- see the MERGE GROUPS section. Nothing in this sheet is applied to any "
        "taxonomy file.\n"
    )

    if merge_groups:
        lines.append("## Proposed near-duplicate merge groups (proposal only)\n")
        for i, group in enumerate(merge_groups):
            canonical = all_candidates_by_key[group[0]].display
            aliases = [all_candidates_by_key[k].display for k in group[1:]]
            lines.append(f"- **{canonical}** <- alias(es): {', '.join(aliases)}")
        lines.append("")

    for domain in sorted(domain_candidates, key=lambda d: -len(domain_candidates[d])):
        candidates = domain_candidates[domain][:APPROVAL_SHEET_CAP_PER_DOMAIN]
        lines.append(f"## {domain} ({len(domain_candidates[domain])} candidates, showing top {len(candidates)})\n")
        for c in candidates:
            cat_info = categorization.get(c.key, {})
            req_type = cat_info.get("requirement_type") or "(uncategorized)"
            category = cat_info.get("category") or "(uncategorized)"
            substantive = cat_info.get("substantive")
            substantive_str = "unknown" if substantive is None else ("yes" if substantive else "no")
            pass_str = "both" if len(c.passes) == 2 else next(iter(c.passes))
            merge_note = f" [merge-group #{merge_group_of[c.key]}]" if c.key in merge_group_of else ""
            example = c.example or "(no example captured)"
            lines.append(
                f"- **{c.display}**{merge_note} -- pass={pass_str}, postings={len(c.postings)}, "
                f"companies={len(c.companies)}, type={req_type}, category={category}, substantive={substantive_str}\n"
                f"  {example}"
            )
        lines.append("")

    OUTPUT_DIR.mkdir(exist_ok=True)
    sheet_path = OUTPUT_DIR / f"taxonomy_v3_consolidated_{report_date}.md"
    sheet_path.write_text("\n".join(lines), encoding="utf-8")
    return sheet_path


def print_console_summary(domain_candidates: dict[str, list[MergedCandidate]], categorization: dict[str, dict], merge_groups: list[list[str]]) -> None:
    print(f"\n{'=' * 90}\nCONSOLIDATED TAXONOMY V3 -- CONSOLE SUMMARY\n{'=' * 90}")

    total = 0
    total_substantive = 0
    total_soft_admin = 0
    total_unknown = 0

    print(f"{'domain':<24}{'total':<8}{'substantive':<13}{'soft/office_admin':<19}{'unknown':<9}")
    print("-" * 73)
    for domain in sorted(domain_candidates, key=lambda d: -len(domain_candidates[d])):
        candidates = domain_candidates[domain]
        n = len(candidates)
        subst = sum(1 for c in candidates if categorization.get(c.key, {}).get("substantive") is True)
        soft_admin = sum(1 for c in candidates if categorization.get(c.key, {}).get("substantive") is False)
        unknown = n - subst - soft_admin
        print(f"{domain:<24}{n:<8}{subst:<13}{soft_admin:<19}{unknown:<9}")
        total += n
        total_substantive += subst
        total_soft_admin += soft_admin
        total_unknown += unknown

    print("-" * 73)
    print(f"{'TOTAL':<24}{total:<8}{total_substantive:<13}{total_soft_admin:<19}{total_unknown:<9}")
    print(
        f"\nOf {total} total candidates: {total_substantive} substantive ({round(total_substantive/total*100,1) if total else 0}%), "
        f"{total_soft_admin} soft/office_admin ({round(total_soft_admin/total*100,1) if total else 0}%), "
        f"{total_unknown} uncategorized."
    )
    print(
        f"\nThis split is the honest forecast of how much skill_substantive can actually move: only the "
        f"{total_substantive} substantive candidates would count toward it if approved."
    )
    print(f"\nMerge groups proposed: {len(merge_groups)}")


def main() -> None:
    from storage import get_postings_for_skill_gap_analysis, get_skill_mentions_for_analysis

    postings = get_postings_for_skill_gap_analysis()
    mentions = get_skill_mentions_for_analysis()
    logger.info(f"Loaded {len(postings)} postings, {len(mentions)} skill_mentions rows")

    targets_by_domain = select_targets(postings, mentions)
    existing_categories = list(extract_skills._TAXONOMY["categories"].keys())

    domain_candidates: dict[str, list[MergedCandidate]] = {}
    for domain in QUALIFYING_DOMAINS:
        targets = targets_by_domain.get(domain, [])
        if not targets:
            continue
        min_companies = _min_companies_for(len(targets))
        logger.info(f"[{domain}] merging tag+text passes ({len(targets)} targets, company bar >={min_companies})")
        domain_candidates[domain] = merge_domain(domain, targets, min_companies)

    all_candidates_by_key: dict[str, MergedCandidate] = {}
    for candidates in domain_candidates.values():
        for c in candidates:
            all_candidates_by_key[c.key] = c

    logger.info(f"Categorizing {len(all_candidates_by_key)} distinct global candidates...")
    categorization = categorize_globally(all_candidates_by_key, existing_categories)

    logger.info("Detecting near-duplicate merge groups...")
    merge_groups = propose_merge_groups(all_candidates_by_key)

    report_date = _report_date()
    sheet_path = build_sheet(domain_candidates, categorization, merge_groups, all_candidates_by_key, report_date)
    print_console_summary(domain_candidates, categorization, merge_groups)
    print(f"\nSheet written to {sheet_path} (proposal only -- nothing applied to any taxonomy file)")


if __name__ == "__main__":
    main()
