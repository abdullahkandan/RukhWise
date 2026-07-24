"""Hand-audit of the domain classifier's LLM tier (stage 3).

The first validation round (classify_domains.py's own console report)
only sampled the 62 postings that ended up domain='other' -- it never
looked at whether the 776 postings stage 3 DID confidently assign a
domain to were assigned correctly. Every downstream analysis (including
skill_gap_discovery.py's per-domain grouping) trusts those 776 labels,
so this is the tier that actually needs a human read, not the leftover
bucket.

  python validate_domain_classifier.py
      Samples 40 postings at random from domain_method='llm',
      STRATIFIED across domains (an even quota per domain that actually
      received an LLM classification, not a single pool draw that would
      just resurface the biggest domains repeatedly) so every domain
      gets represented for hand review. Prints title, first 200 chars
      of description, assigned domain, and confidence for each. Also
      reports the full confidence distribution across all 776 and how
      many land in the 0.6-0.7 band specifically (the region right above
      the classifier's own acceptance threshold, where an error is most
      likely to slip through).

Read-only -- no writes anywhere.
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

SAMPLE_SIZE = 40
DESCRIPTION_CHARS = 200
RANDOM_SEED = 2026


def stratified_sample(postings_by_domain: dict[str, list[dict]], total: int, seed: int) -> list[dict]:
    """Even quota per domain first, then round-robin over domains for
    any remainder, so a domain with fewer llm-classified postings than
    its quota doesn't shrink the total sample -- the leftover redistributes
    to domains that have more available."""
    rng = random.Random(seed)
    domains = sorted(postings_by_domain)
    pools = {d: postings_by_domain[d][:] for d in domains}
    for d in domains:
        rng.shuffle(pools[d])

    sample: list[dict] = []
    remaining_total = total
    # Repeated round-robin draws (one posting per domain per round) until
    # the target is hit or every pool is exhausted -- this is what makes
    # it "stratified" rather than a single proportional split.
    while remaining_total > 0 and any(pools.values()):
        for d in domains:
            if remaining_total <= 0:
                break
            if pools[d]:
                sample.append(pools[d].pop())
                remaining_total -= 1
    return sample


def run() -> None:
    from storage import get_postings_for_domain_validation

    postings = get_postings_for_domain_validation()
    llm_postings = [p for p in postings if p.get("domain_method") == "llm"]
    logger.info(f"Loaded {len(postings)} postings, {len(llm_postings)} with domain_method='llm'")

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for p in llm_postings:
        by_domain[p.get("domain") or "(none)"].append(p)

    sample = stratified_sample(by_domain, SAMPLE_SIZE, RANDOM_SEED)

    print(f"\n{'=' * 78}\nSTRATIFIED SAMPLE -- {len(sample)} of {len(llm_postings)} domain_method='llm' postings\n{'=' * 78}")
    print(f"Domains represented in the LLM tier: {sorted(by_domain)} (counts: {dict(sorted(((d, len(v)) for d, v in by_domain.items()), key=lambda kv: -kv[1]))})\n")

    for p in sample:
        title = p.get("title") or "(no title)"
        desc = (p.get("description") or "")[:DESCRIPTION_CHARS].replace("\n", " ")
        domain = p.get("domain")
        conf = p.get("domain_confidence")
        print(f"[{domain:<24} conf={conf}] {title}")
        print(f"    {desc}")
        print()

    confidences = [p.get("domain_confidence") for p in llm_postings if p.get("domain_confidence") is not None]
    print(f"{'=' * 78}\nCONFIDENCE DISTRIBUTION -- all {len(llm_postings)} domain_method='llm' postings\n{'=' * 78}")
    bins = [(0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    for lo, hi in bins:
        n = sum(1 for c in confidences if lo <= c < hi)
        pct = round(n / len(confidences) * 100, 1) if confidences else 0.0
        label = f"[{lo:.1f}, {hi if hi <= 1.0 else 1.0:.1f}]" if hi > 1.0 else f"[{lo:.1f}, {hi:.1f})"
        print(f"  {label:<14} {n:<6} ({pct}%)")
    below_no_conf = len(llm_postings) - len(confidences)
    if below_no_conf:
        print(f"  (no confidence recorded) {below_no_conf}")

    band_0_6_0_7 = sum(1 for c in confidences if 0.6 <= c < 0.7)
    print(f"\nPostings with confidence in [0.6, 0.7): {band_0_6_0_7} / {len(llm_postings)} ({round(band_0_6_0_7/len(llm_postings)*100,1) if llm_postings else 0}%)")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
