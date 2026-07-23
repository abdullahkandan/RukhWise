"""Rukhwise taxonomy v2 depth comparison: BEFORE (taxonomy_v1 extraction)
vs AFTER (taxonomy_v2 extraction), reported as TWO separate, never-blended
metrics. Read-only.

  python compare_taxonomy_depth.py

Reads STORED skill_mentions rows tagged by extraction_method, never a live
re-match against whichever taxonomy file happens to be loaded right now.
extract_skills.py points at taxonomy_v2.yaml as of this change, so a live
re-match (e.g. drift.py's own compute_depth(), which calls
extract_skills.extract_skills() fresh) would silently contaminate a
"before" baseline with v2 categories -- it would no longer be measuring
what taxonomy v1 could actually see. The only trustworthy "before"
snapshot is the preserved extraction_method='taxonomy_v1' rows themselves
(see output/taxonomy_v2_spec.md section 1: "v1 extractions stay
auditable"). This is why this comparison is its own script rather than a
mode bolted onto drift.py's live compute_depth().

TWO metrics, reported separately, never blended into one number:

skill_substantive: distinct matches with requirement_type='skill',
excluding category in (soft, office_admin). Identical definition on both
sides of the comparison -- v1 rows are all requirement_type='skill' by
construction (the column didn't exist before v2, backfilled to 'skill'),
and v2's skill-type rows use the same 96 skill entries v1 had, unchanged.
This is the metric that tracks the ORIGINAL gap: are the same 96 skills
finding more postings than before, on their own terms. It should move
little, because nothing about the 96 skills changed -- the taxonomy v2
build deliberately did not add new skill entries (see taxonomy v2 spec
section 7: "the skill gap remains open, and needs a different method").

requirement_substantive: every requirement_type (skill, credential,
experience, language, attribute). BEFORE, this is identical to
skill_substantive_before -- v1 had no concept of credential/experience/
language/attribute at all, so there is nothing else to add. AFTER, it is
distinct v2 mentions across ALL types (which already includes language
and attribute mentions, since their categories -- language,
work_arrangement -- aren't excluded) PLUS 1 if the posting has any
credential signal (degree_level is not null OR has_certification is
true) PLUS 1 if it has an experience signal (experience_level is not
null). Credentials and experience are deliberately NOT skill_mentions
rows (see structured_extraction.py), so they can't be counted via the
mentions table at all; added explicitly, one point each, so a posting
stating "3 years, Bachelor's, on-site" counts as 3 substantive facts, not
1. This is the metric that answers "how much do we now know about this
posting overall" -- a broader, different claim from skill_substantive,
reported as its own column so the two are never mistaken for each other.
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from drift import infer_domain  # noqa: E402 -- reuse the exact same domain inference drift.py already uses

_NON_SUBSTANTIVE_CATEGORIES = frozenset({"soft", "office_admin"})


def _substantive_counts(
    mentions: list[dict], extraction_method: str, requirement_types: set[str] | None = None
) -> dict[str, int]:
    """posting_id -> distinct substantive mention count for one extraction
    pass, computed from stored mention rows (never a live re-match).
    category in (soft, office_admin) is always excluded. requirement_types
    optionally restricts to a subset (e.g. {"skill"}); None means every
    type."""
    by_posting: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        if m.get("extraction_method") != extraction_method:
            continue
        if m.get("category") in _NON_SUBSTANTIVE_CATEGORIES:
            continue
        if requirement_types is not None and m.get("requirement_type") not in requirement_types:
            continue
        by_posting[m["posting_id"]].add(m["skill"])
    return {pid: len(skills) for pid, skills in by_posting.items()}


def _row_stats(counts: list[int]) -> dict:
    n = len(counts)
    le1 = sum(1 for c in counts if c <= 1)
    return {
        "n": n,
        "median": round(statistics.median(counts), 2) if counts else 0,
        "pct_le1": round(le1 / n * 100, 2) if n else 0.0,
    }


def run_comparison() -> None:
    from storage import get_postings_for_depth_comparison, get_skill_mentions_for_analysis

    postings = get_postings_for_depth_comparison()
    mentions = get_skill_mentions_for_analysis()
    logger.info(f"Loaded {len(postings)} postings, {len(mentions)} skill_mentions rows")

    # ---- skill_substantive: requirement_type='skill' only, both sides ----
    skill_before_counts = _substantive_counts(mentions, "taxonomy_v1", {"skill"})
    skill_after_counts = _substantive_counts(mentions, "taxonomy_v2", {"skill"})

    # ---- requirement_substantive: every type, both sides ----
    # v1 rows are all requirement_type='skill' by construction, so this
    # "before" is computed independently (not just aliased to
    # skill_before_counts) for correctness, even though it will turn out
    # numerically identical -- v1 genuinely has nothing else to find.
    req_before_taxonomy_counts = _substantive_counts(mentions, "taxonomy_v1", None)
    req_after_taxonomy_counts = _substantive_counts(mentions, "taxonomy_v2", None)

    attribute_posting_ids = {
        m["posting_id"] for m in mentions
        if m.get("extraction_method") == "taxonomy_v2" and m.get("requirement_type") == "attribute"
    }

    skill_before_by_posting: dict[str, int] = {}
    skill_after_by_posting: dict[str, int] = {}
    req_before_by_posting: dict[str, int] = {}
    req_after_by_posting: dict[str, int] = {}
    gained_degree = 0
    gained_experience = 0
    gained_attribute = 0

    for p in postings:
        pid = p["id"]
        skill_before_by_posting[pid] = skill_before_counts.get(pid, 0)
        skill_after_by_posting[pid] = skill_after_counts.get(pid, 0)

        req_before_by_posting[pid] = req_before_taxonomy_counts.get(pid, 0)

        req_after = req_after_taxonomy_counts.get(pid, 0)
        has_credential = bool(p.get("degree_level")) or bool(p.get("has_certification"))
        has_experience = bool(p.get("experience_level"))
        if has_credential:
            req_after += 1
        if has_experience:
            req_after += 1
        req_after_by_posting[pid] = req_after

        if p.get("degree_level"):
            gained_degree += 1
        if p.get("experience_level"):
            gained_experience += 1
        if pid in attribute_posting_ids:
            gained_attribute += 1

    by_source_skill_before: dict[str, list[int]] = defaultdict(list)
    by_source_skill_after: dict[str, list[int]] = defaultdict(list)
    by_source_req_before: dict[str, list[int]] = defaultdict(list)
    by_source_req_after: dict[str, list[int]] = defaultdict(list)
    by_domain_skill_before: dict[str, list[int]] = defaultdict(list)
    by_domain_skill_after: dict[str, list[int]] = defaultdict(list)
    by_domain_req_before: dict[str, list[int]] = defaultdict(list)
    by_domain_req_after: dict[str, list[int]] = defaultdict(list)

    for p in postings:
        pid = p["id"]
        source = p.get("source") or "unknown"
        domain = infer_domain(p.get("title"))

        by_source_skill_before[source].append(skill_before_by_posting[pid])
        by_source_skill_after[source].append(skill_after_by_posting[pid])
        by_source_req_before[source].append(req_before_by_posting[pid])
        by_source_req_after[source].append(req_after_by_posting[pid])

        by_domain_skill_before[domain].append(skill_before_by_posting[pid])
        by_domain_skill_after[domain].append(skill_after_by_posting[pid])
        by_domain_req_before[domain].append(req_before_by_posting[pid])
        by_domain_req_after[domain].append(req_after_by_posting[pid])

    print(f"\n{'#' * 78}\n# SKILL_SUBSTANTIVE -- requirement_type='skill' only (tracks the ORIGINAL gap)\n{'#' * 78}")
    _print_side_by_side("BY SOURCE", by_source_skill_before, by_source_skill_after)
    _print_side_by_side("BY DOMAIN", by_domain_skill_before, by_domain_skill_after)

    print(f"\n{'#' * 78}\n# REQUIREMENT_SUBSTANTIVE -- all types (skill+credential+experience+language+attribute)\n{'#' * 78}")
    _print_side_by_side("BY SOURCE", by_source_req_before, by_source_req_after)
    _print_side_by_side("BY DOMAIN", by_domain_req_before, by_domain_req_after)

    print(f"\n{'=' * 78}\nSTRUCTURED FIELD YIELD\n{'=' * 78}")
    print(f"Postings that gained a degree_level:        {gained_degree} / {len(postings)}")
    print(f"Postings that gained an experience_level:   {gained_experience} / {len(postings)}")
    print(f"Postings that gained >=1 attribute mention: {gained_attribute} / {len(postings)}")


def _print_side_by_side(title: str, before: dict[str, list[int]], after: dict[str, list[int]]) -> None:
    keys = sorted(set(before) | set(after), key=lambda k: -len(after.get(k, [])))
    header = f"{'':<24}{'n':<6}{'median_v1':<11}{'median_v2':<11}{'%<=1_v1':<10}{'%<=1_v2':<10}"
    print(f"\n{'-' * 78}\n{title} -- BEFORE (taxonomy_v1) vs AFTER (taxonomy_v2)\n{'-' * 78}")
    print(header)
    print("-" * len(header))
    for key in keys:
        b = _row_stats(before.get(key, []))
        a = _row_stats(after.get(key, []))
        print(f"{key:<24}{a['n']:<6}{b['median']:<11}{a['median']:<11}{b['pct_le1']:<10}{a['pct_le1']:<10}")


def main() -> None:
    run_comparison()


if __name__ == "__main__":
    main()
