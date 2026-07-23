"""Rukhwise taxonomy v2 depth comparison: BEFORE (taxonomy_v1 extraction)
vs AFTER (taxonomy_v2 extraction, including credential/experience/
language/attribute signal). Read-only.

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

Substantive match, BEFORE: distinct taxonomy_v1 skill_mentions rows
excluding category in (soft, office_admin) -- exactly drift.py's original
depth-metric definition, computed here from the v1-tagged rows already
preserved in the database.

Substantive match, AFTER: distinct taxonomy_v2 skill_mentions rows
excluding category in (soft, office_admin) -- this already includes
language and attribute mentions automatically, since their categories
(language, work_arrangement) are not excluded -- PLUS 1 if the posting
has any credential signal (degree_level is not null OR has_certification
is true) PLUS 1 if it has an experience signal (experience_level is not
null). Credentials and experience are deliberately NOT skill_mentions
rows (see structured_extraction.py / taxonomy v2 spec sections 2-3), so
they can't be counted via the mentions table at all; they're added here
explicitly, one point each, so a posting stating "3 years, Bachelor's,
on-site" counts as 3 substantive facts (experience + credential +
attribute), not 1. A posting's credential signal is one point regardless
of whether it has a degree_level, a has_certification flag, or both --
these represent one underlying "this posting states a credential
requirement" fact, not two independent ones.
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


def _substantive_counts_by_method(mentions: list[dict], extraction_method: str) -> dict[str, int]:
    """posting_id -> distinct substantive skill count for one extraction
    pass, computed from stored mention rows (never a live re-match)."""
    by_posting: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        if m.get("extraction_method") != extraction_method:
            continue
        if m.get("category") in _NON_SUBSTANTIVE_CATEGORIES:
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

    before_taxonomy_counts = _substantive_counts_by_method(mentions, "taxonomy_v1")
    after_taxonomy_counts = _substantive_counts_by_method(mentions, "taxonomy_v2")
    attribute_posting_ids = {
        m["posting_id"] for m in mentions
        if m.get("extraction_method") == "taxonomy_v2" and m.get("requirement_type") == "attribute"
    }

    before_by_posting: dict[str, int] = {}
    after_by_posting: dict[str, int] = {}
    gained_degree = 0
    gained_experience = 0
    gained_attribute = 0

    for p in postings:
        pid = p["id"]
        before_by_posting[pid] = before_taxonomy_counts.get(pid, 0)

        after = after_taxonomy_counts.get(pid, 0)
        has_credential = bool(p.get("degree_level")) or bool(p.get("has_certification"))
        has_experience = bool(p.get("experience_level"))
        if has_credential:
            after += 1
        if has_experience:
            after += 1
        after_by_posting[pid] = after

        if p.get("degree_level"):
            gained_degree += 1
        if p.get("experience_level"):
            gained_experience += 1
        if pid in attribute_posting_ids:
            gained_attribute += 1

    by_source_before: dict[str, list[int]] = defaultdict(list)
    by_source_after: dict[str, list[int]] = defaultdict(list)
    by_domain_before: dict[str, list[int]] = defaultdict(list)
    by_domain_after: dict[str, list[int]] = defaultdict(list)

    for p in postings:
        pid = p["id"]
        source = p.get("source") or "unknown"
        domain = infer_domain(p.get("title"))
        by_source_before[source].append(before_by_posting[pid])
        by_source_after[source].append(after_by_posting[pid])
        by_domain_before[domain].append(before_by_posting[pid])
        by_domain_after[domain].append(after_by_posting[pid])

    _print_side_by_side("BY SOURCE", by_source_before, by_source_after)
    _print_side_by_side("BY DOMAIN", by_domain_before, by_domain_after)

    print(f"\n{'=' * 78}\nSTRUCTURED FIELD YIELD\n{'=' * 78}")
    print(f"Postings that gained a degree_level:        {gained_degree} / {len(postings)}")
    print(f"Postings that gained an experience_level:   {gained_experience} / {len(postings)}")
    print(f"Postings that gained >=1 attribute mention: {gained_attribute} / {len(postings)}")


def _print_side_by_side(title: str, before: dict[str, list[int]], after: dict[str, list[int]]) -> None:
    keys = sorted(set(before) | set(after), key=lambda k: -len(after.get(k, [])))
    header = f"{'':<24}{'n':<6}{'median_v1':<11}{'median_v2':<11}{'%<=1_v1':<10}{'%<=1_v2':<10}"
    print(f"\n{'=' * 78}\n{title} -- BEFORE (taxonomy_v1) vs AFTER (taxonomy_v2)\n{'=' * 78}")
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
