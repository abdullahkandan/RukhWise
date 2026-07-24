"""One-time backfill: split domain_method='llm' into 'llm' (real
description available) vs 'llm_title_only' (description was blank at
classification time), capping domain_confidence at
domain_classifier.LLM_TITLE_ONLY_CONFIDENCE_CAP for the latter.

domain_classifier.py's classify_postings() now makes this distinction
for every FUTURE run (see its module docstring); this script corrects
the rows already written by the run before that fix existed. Domain
assignment itself is untouched -- only the method label and confidence
value are corrected, via the same update_postings_domain_batch RPC
classify_domains.py already uses (unconditional SET, no new schema).

  python backfill_llm_title_only.py
      Reports the distribution before, applies the correction, reports
      the distribution after.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

from domain_classifier import LLM_TITLE_ONLY_CONFIDENCE_CAP  # noqa: E402


def _is_blank(text: str | None) -> bool:
    return not (text or "").strip()


def run() -> None:
    from storage import get_postings_for_blank_description_audit, update_postings_domain

    postings = get_postings_for_blank_description_audit()
    logger.info(f"Loaded {len(postings)} postings")

    before_counts = Counter(p.get("domain_method") for p in postings)

    to_correct = []
    for p in postings:
        if p.get("domain_method") != "llm":
            continue
        if not _is_blank(p.get("description")):
            continue
        old_conf = p.get("domain_confidence")
        new_conf = min(old_conf, LLM_TITLE_ONLY_CONFIDENCE_CAP) if old_conf is not None else LLM_TITLE_ONLY_CONFIDENCE_CAP
        to_correct.append({
            "id": p["id"],
            "domain": p.get("domain"),
            "domain_method": "llm_title_only",
            "domain_confidence": new_conf,
        })

    print(f"\n{'=' * 78}\nBEFORE\n{'=' * 78}")
    for method, n in sorted(before_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {method:<20} {n}")

    print(f"\nCorrecting {len(to_correct)} row(s): domain_method 'llm' -> 'llm_title_only', confidence capped at {LLM_TITLE_ONLY_CONFIDENCE_CAP}")

    if not to_correct:
        print("Nothing to backfill.")
        return

    result = update_postings_domain(to_correct)
    logger.info(f"Backfill write result: {result}")

    after_postings = get_postings_for_blank_description_audit()
    after_counts = Counter(p.get("domain_method") for p in after_postings)

    print(f"\n{'=' * 78}\nAFTER\n{'=' * 78}")
    for method, n in sorted(after_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {method:<20} {n}")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
