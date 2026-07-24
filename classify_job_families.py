"""Rukhwise job-family title normalization entrypoint.

  python classify_job_families.py
      Full run over every posting currently in Supabase: two-stage
      classification (see rukhwise_scraper/job_family_classifier.py),
      writes job_family/family_method/family_confidence, and prints:
      family distribution, per-method counts, overall %unmatched, and a
      gap report -- every DISTINCT unmatched title (seniority-prefix
      stripped, case/whitespace normalized) that recurs across >=5
      postings, with example titles. That gap list is input for a human
      to review and extend job_families.yaml by hand -- nothing here
      adds to the vocabulary automatically.

  python classify_job_families.py --no-llm
      Runs stage 1 (rules) only; anything it misses stays job_family=NULL,
      family_method='unmatched'.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from job_family_classifier import FAMILY_KEYS, classify_postings, _strip_seniority_prefix  # noqa: E402

GAP_MIN_POSTINGS = 5
GAP_EXAMPLES_PER_CLUSTER = 3


def _normalize_for_gap(title: str) -> str:
    stripped = _strip_seniority_prefix(title or "")
    return " ".join(stripped.split()).casefold()


def run_classification(with_llm: bool = True) -> None:
    from storage import get_postings_for_family_classification, update_postings_family

    postings = get_postings_for_family_classification()
    total = len(postings)
    logger.info(f"Loaded {total} postings for job-family classification")

    results = classify_postings(postings, with_llm=with_llm)
    results_by_id = {r["id"]: r for r in results}

    method_counts = Counter(r["family_method"] for r in results)
    family_counts = Counter(r["job_family"] for r in results if r["job_family"])

    write_rows = [
        {"id": r["id"], "job_family": r["job_family"], "family_method": r["family_method"], "family_confidence": r["family_confidence"]}
        for r in results
    ]
    write_result = update_postings_family(write_rows)
    logger.info(f"Wrote job-family classification for {write_result['updated']} postings ({write_result['failed']} failed)")

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    print(f"\n{'=' * 78}\nFAMILY DISTRIBUTION ({total} postings)\n{'=' * 78}")
    for family, n in sorted(family_counts.items(), key=lambda kv: -kv[1]):
        pct = round(n / total * 100, 2) if total else 0.0
        print(f"  {family:<32} {n:<6} ({pct}%)")

    print(f"\n{'=' * 78}\nMETHOD SPLIT\n{'=' * 78}")
    for method in ("rule", "llm", "unmatched"):
        n = method_counts.get(method, 0)
        pct = round(n / total * 100, 2) if total else 0.0
        print(f"  {method:<12} {n:<6} ({pct}%)")

    unmatched_pct = round(method_counts.get("unmatched", 0) / total * 100, 2) if total else 0.0
    print(f"\n%unmatched: {unmatched_pct}%")

    # ---- gap report: unmatched titles, normalized, clustered by exact match ----
    postings_by_id = {p["id"]: p for p in postings}
    unmatched_ids = [pid for pid, r in results_by_id.items() if r["family_method"] == "unmatched"]

    clusters: dict[str, list[str]] = defaultdict(list)  # normalized -> [raw titles]
    for pid in unmatched_ids:
        title = postings_by_id[pid].get("title") or "(no title)"
        norm = _normalize_for_gap(title)
        if norm:
            clusters[norm].append(title)

    gap_clusters = [(norm, titles) for norm, titles in clusters.items() if len(titles) >= GAP_MIN_POSTINGS]
    gap_clusters.sort(key=lambda kv: -len(kv[1]))

    print(f"\n{'=' * 78}\nGAP REPORT -- unmatched title clusters with >={GAP_MIN_POSTINGS} postings\n{'=' * 78}")
    print(f"({len(gap_clusters)} cluster(s) found, out of {len(unmatched_ids)} unmatched posting(s) total)\n")
    if not gap_clusters:
        print("(none -- no unmatched title recurs >=5 times)")
    else:
        random.seed(2026)
        for norm, titles in gap_clusters:
            examples = random.sample(titles, min(GAP_EXAMPLES_PER_CLUSTER, len(titles)))
            print(f"  [{len(titles):<4} postings] {norm!r}")
            for ex in examples:
                print(f"      e.g. {ex!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise job-family title normalization")
    parser.add_argument("--no-llm", action="store_true", help="Skip stage 2 (LLM); unresolved titles stay unmatched")
    args = parser.parse_args()
    run_classification(with_llm=not args.no_llm)


if __name__ == "__main__":
    main()
