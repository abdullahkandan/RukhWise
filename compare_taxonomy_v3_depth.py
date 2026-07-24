"""Rukhwise taxonomy v3 depth comparison: BEFORE (taxonomy_v2 extraction)
vs AFTER (taxonomy_v3 extraction), reported as TWO separate, never-blended
metrics -- same convention as compare_taxonomy_depth.py's v1-vs-v2
comparison (kept as its own permanent script, untouched). Read-only.

  python compare_taxonomy_v3_depth.py

Reads STORED skill_mentions rows tagged by extraction_method, never a live
re-match -- extract_skills.py now points at taxonomy_v3.yaml, so a live
re-match would silently contaminate the "before" (v2) baseline. The only
trustworthy "before" snapshot is the preserved extraction_method='taxonomy_v2'
rows themselves (taxonomy_v2.yaml is untouched on disk; those rows are the
historical record of what it could see).

Domain grouping uses postings.domain -- the CORRECTED three-stage
classifier output (domain_classifier.py), not drift.py's older title-only
infer_domain() -- matching the convention skill_gap_discovery.py already
established for taxonomy v3 work.

skill_substantive: distinct matches with requirement_type='skill',
excluding category in (soft, office_admin). This is the metric that has
not moved through any previous round (v2 deliberately added zero new
skill entries) -- it is the one taxonomy v3 exists to move.

requirement_substantive: every requirement_type (skill, credential,
experience, language, attribute) -- identical definition to
compare_taxonomy_depth.py's, extended transparently since v3 didn't touch
the credential/experience structured-field mechanism at all.
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

_NON_SUBSTANTIVE_CATEGORIES = frozenset({"soft", "office_admin"})
NEW_V3_CATEGORIES = frozenset({
    "health_clinical", "lab_science", "safety_compliance", "supply_chain",
    "electrical_mechanical", "teaching", "customer_service",
})


def _substantive_counts(
    mentions: list[dict], extraction_method: str, requirement_types: set[str] | None = None
) -> dict[str, int]:
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
    from storage import get_postings_for_v3_depth_comparison, get_skill_mentions_for_analysis

    postings = get_postings_for_v3_depth_comparison()
    mentions = get_skill_mentions_for_analysis()
    logger.info(f"Loaded {len(postings)} postings, {len(mentions)} skill_mentions rows")

    # ---- skill_substantive: requirement_type='skill' only, both sides ----
    skill_before_counts = _substantive_counts(mentions, "taxonomy_v2", {"skill"})
    skill_after_counts = _substantive_counts(mentions, "taxonomy_v3", {"skill"})

    # ---- requirement_substantive: every type, both sides ----
    req_before_taxonomy_counts = _substantive_counts(mentions, "taxonomy_v2", None)
    req_after_taxonomy_counts = _substantive_counts(mentions, "taxonomy_v3", None)

    skill_before_by_posting: dict[str, int] = {}
    skill_after_by_posting: dict[str, int] = {}
    req_before_by_posting: dict[str, int] = {}
    req_after_by_posting: dict[str, int] = {}

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
        domain = p.get("domain") or "other"

        by_source_skill_before[source].append(skill_before_by_posting[pid])
        by_source_skill_after[source].append(skill_after_by_posting[pid])
        by_source_req_before[source].append(req_before_by_posting[pid])
        by_source_req_after[source].append(req_after_by_posting[pid])

        by_domain_skill_before[domain].append(skill_before_by_posting[pid])
        by_domain_skill_after[domain].append(skill_after_by_posting[pid])
        by_domain_req_before[domain].append(req_before_by_posting[pid])
        by_domain_req_after[domain].append(req_after_by_posting[pid])

    print(f"\n{'#' * 78}\n# SKILL_SUBSTANTIVE -- requirement_type='skill' only (the metric that hadn't moved)\n{'#' * 78}")
    _print_side_by_side("BY SOURCE", by_source_skill_before, by_source_skill_after)
    _print_side_by_side("BY DOMAIN", by_domain_skill_before, by_domain_skill_after)

    print(f"\n{'#' * 78}\n# REQUIREMENT_SUBSTANTIVE -- all types (skill+credential+experience+language+attribute)\n{'#' * 78}")
    _print_side_by_side("BY SOURCE", by_source_req_before, by_source_req_after)
    _print_side_by_side("BY DOMAIN", by_domain_req_before, by_domain_req_after)

    # ---- new-category vs existing-category split, per domain ----
    postings_by_id = {p["id"]: p for p in postings}
    new_cat_by_domain: dict[str, int] = defaultdict(int)
    existing_cat_by_domain: dict[str, int] = defaultdict(int)
    for m in mentions:
        if m.get("extraction_method") != "taxonomy_v3":
            continue
        if m.get("category") in _NON_SUBSTANTIVE_CATEGORIES:
            continue
        posting = postings_by_id.get(m["posting_id"])
        domain = (posting.get("domain") or "other") if posting else "other"
        if m.get("category") in NEW_V3_CATEGORIES:
            new_cat_by_domain[domain] += 1
        else:
            existing_cat_by_domain[domain] += 1

    print(f"\n{'=' * 78}\nSUBSTANTIVE v3 MENTIONS BY DOMAIN -- NEW CATEGORIES vs ADDITIONS TO EXISTING\n{'=' * 78}")
    header = f"{'domain':<26}{'new categories':<16}{'existing categories':<20}{'total':<8}"
    print(header)
    print("-" * len(header))
    all_domains = sorted(set(new_cat_by_domain) | set(existing_cat_by_domain), key=lambda d: -(new_cat_by_domain.get(d, 0) + existing_cat_by_domain.get(d, 0)))
    for domain in all_domains:
        n, e = new_cat_by_domain.get(domain, 0), existing_cat_by_domain.get(domain, 0)
        print(f"{domain:<26}{n:<16}{e:<20}{n + e:<8}")


def _print_side_by_side(title: str, before: dict[str, list[int]], after: dict[str, list[int]]) -> None:
    keys = sorted(set(before) | set(after), key=lambda k: -len(after.get(k, [])))
    header = f"{'':<24}{'n':<6}{'median_v2':<11}{'median_v3':<11}{'%<=1_v2':<10}{'%<=1_v3':<10}"
    print(f"\n{'-' * 78}\n{title} -- BEFORE (taxonomy_v2) vs AFTER (taxonomy_v3)\n{'-' * 78}")
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
