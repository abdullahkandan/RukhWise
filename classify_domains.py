"""Rukhwise domain classification entrypoint.

  python classify_domains.py
      Full run over every posting in Supabase: three-stage classification
      (see rukhwise_scraper/domain_classifier.py), writes domain/
      domain_method/domain_confidence, and prints a validation report:
      before/after distribution, per-stage counts, a random sample of
      LLM-classified postings, and the final 'other' rate.

  python classify_domains.py --no-llm
      Runs stages 1-2 only; anything neither rule stage resolves stays
      domain='other', domain_method='unclassified'. Useful for a fast
      rules-only pass, or when GROQ_API_KEY isn't set (stage 3 would
      no-op the same way regardless, this just skips the network calls
      outright).

Idempotent and fully re-runnable: every run recomputes and overwrites
domain/domain_method/domain_confidence for every posting from scratch
(the update RPC sets all three fields unconditionally) -- there is no
incremental/partial mode, unlike skill extraction's ON CONFLICT DO
NOTHING. Re-running after a domains.yaml edit is the expected way to
apply a vocabulary change to the whole corpus.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from drift import infer_domain as infer_domain_before  # noqa: E402 -- the old title-only, four-source inference, kept unmodified as the "before" baseline
from domain_classifier import DOMAIN_KEYS, classify_postings  # noqa: E402

SAMPLE_SIZE = 30
OTHER_SAMPLE_SIZE = 30


def _distribution(domains: list[str]) -> Counter:
    return Counter(domains)


def _print_distribution(title: str, counts: Counter, total: int) -> None:
    print(f"\n{'-' * 70}\n{title}\n{'-' * 70}")
    for domain, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        pct = round(n / total * 100, 2) if total else 0.0
        print(f"  {domain:<26} {n:<6} ({pct}%)")


def run_classification(with_llm: bool = True) -> None:
    from storage import get_postings_for_domain_classification, update_postings_domain

    postings = get_postings_for_domain_classification()
    total = len(postings)
    logger.info(f"Loaded {total} postings for domain classification")

    # ---- BEFORE: the old title-only, four-source-convention inference ----
    before_domains = [infer_domain_before(p.get("title")) for p in postings]
    before_counts = _distribution(before_domains)
    before_other = before_counts.get("other_unclassified", 0)

    # ---- Classify (three stages) ----
    results = classify_postings(postings, with_llm=with_llm)
    results_by_id = {r["id"]: r for r in results}

    after_domains = [results_by_id[p["id"]]["domain"] for p in postings]
    after_counts = _distribution(after_domains)
    after_other = after_counts.get("other", 0)

    method_counts = Counter(r["domain_method"] for r in results)

    # ---- Write ----
    write_rows = [
        {
            "id": r["id"],
            "domain": r["domain"],
            "domain_method": r["domain_method"],
            "domain_confidence": r["domain_confidence"],
        }
        for r in results
    ]
    write_result = update_postings_domain(write_rows)
    logger.info(f"Wrote domain classification for {write_result['updated']} postings ({write_result['failed']} failed)")

    # ------------------------------------------------------------------
    # Validation report
    # ------------------------------------------------------------------

    print(f"\n{'=' * 70}\nDOMAIN DISTRIBUTION -- BEFORE (title-only) vs AFTER (three-stage)\n{'=' * 70}")
    _print_distribution(f"BEFORE ({total} postings, old title-only inference)", before_counts, total)
    _print_distribution(f"AFTER ({total} postings, three-stage classifier)", after_counts, total)

    print(f"\n{'=' * 70}\nPER-STAGE COUNTS\n{'=' * 70}")
    for method in ("rule_title", "rule_description", "llm", "unclassified"):
        n = method_counts.get(method, 0)
        pct = round(n / total * 100, 2) if total else 0.0
        print(f"  {method:<20} {n:<6} ({pct}%)")

    llm_rows = [r for r in results if r["domain_method"] == "llm"]
    print(f"\n{'=' * 70}\nRANDOM SAMPLE OF {SAMPLE_SIZE} LLM-CLASSIFIED POSTINGS\n{'=' * 70}")
    if not llm_rows:
        print("  (none -- stage 3 classified nothing, either --no-llm was passed, "
              "GROQ_API_KEY is unset, or every stage-3 candidate came back below "
              f"the {0.6} confidence threshold)")
    else:
        postings_by_id = {p["id"]: p for p in postings}
        random.seed(2026)
        sample = random.sample(llm_rows, min(SAMPLE_SIZE, len(llm_rows)))
        for r in sample:
            title = postings_by_id[r["id"]].get("title") or "(no title)"
            print(f"  [{r['domain']:<22} conf={r['domain_confidence']}] {title}")

    other_pct = round(after_other / total * 100, 2) if total else 0.0
    print(f"\n{'=' * 70}\n'OTHER' RATE\n{'=' * 70}")
    print(f"Before: {before_other}/{total} ({round(before_other / total * 100, 2) if total else 0.0}%) "
          f"-- category 'other_unclassified' under the old title-only inference")
    print(f"After:  {after_other}/{total} ({other_pct}%) -- domain='other' under the three-stage classifier")

    other_rows = [r for r in results if r["domain"] == "other"]
    postings_by_id = {p["id"]: p for p in postings}
    print(f"\n{'-' * 70}\nSample of {OTHER_SAMPLE_SIZE} postings still 'other' (for ambiguous-vs-vocabulary-gap review)\n{'-' * 70}")
    random.seed(99)
    other_sample = random.sample(other_rows, min(OTHER_SAMPLE_SIZE, len(other_rows)))
    for r in other_sample:
        title = postings_by_id[r["id"]].get("title") or "(no title)"
        method = r["domain_method"]
        conf = r["domain_confidence"]
        print(f"  [{method:<12} conf={conf}] {title}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise three-stage domain classification")
    parser.add_argument("--no-llm", action="store_true", help="Skip stage 3 (LLM); unresolved postings stay 'other'")
    args = parser.parse_args()
    run_classification(with_llm=not args.no_llm)


if __name__ == "__main__":
    main()
