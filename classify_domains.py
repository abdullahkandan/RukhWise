"""Rukhwise domain classification entrypoint.

  python classify_domains.py
      INCREMENTAL (the default): only postings with domain IS NULL --
      i.e. never classified before. Three-stage classification (see
      rukhwise_scraper/domain_classifier.py), writes domain/domain_method/
      domain_confidence, and prints a validation report scoped to that
      subset. This is also what .github/workflows/classify.yml runs on
      its own independent daily schedule -- deliberately NOT wired into
      collect.py/collect.yml, so a Groq outage never touches collection
      (see collect.py's module docstring). domain is NEVER left null by a
      completed classification (an unresolved posting gets domain=
      'other', a real answer), so this scope can never re-touch an
      already-classified row, and running it twice in a row the second
      time finds nothing left to do.

  python classify_domains.py --all
      Full run over EVERY posting in Supabase, including ones already
      classified -- overwrites their domain/domain_method/domain_confidence
      from scratch. This is the deliberate, explicit path for a full
      corpus reclassification (e.g. after editing domains.yaml); it is
      never the default, and never runs automatically.

  python classify_domains.py --no-llm
      Runs stages 1-2 only; anything neither rule stage resolves stays
      domain='other', domain_method='unclassified'. Composable with
      --all. Useful for a fast rules-only pass, or when GROQ_API_KEY
      isn't set (stage 3 would no-op the same way regardless, this just
      skips the network calls outright).

  python classify_domains.py --max-batches 10
      Processes at most 10 GROQ_BATCH_SIZE-sized batches (200 postings)
      this run, then stops and logs how many domain-null postings remain
      -- a large backlog drains over several scheduled runs instead of
      one run stalling on a long LLM sequence. Since writes happen per
      batch (see _classify_and_write_batched), the unprocessed remainder
      is simply picked up next run; nothing is lost by capping. This is
      what .github/workflows/classify.yml uses.

Every posting always gets domain/domain_method/domain_confidence written
together in one update (the RPC sets all three fields unconditionally),
so there's no risk of a partially-classified row -- --all overwrites
existing answers on purpose; the default incremental path only ever adds
answers where there were none.
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
from domain_classifier import DOMAIN_KEYS, GROQ_BATCH_SIZE, classify_postings  # noqa: E402

SAMPLE_SIZE = 30
OTHER_SAMPLE_SIZE = 30


def _distribution(domains: list[str]) -> Counter:
    return Counter(domains)


def _print_distribution(title: str, counts: Counter, total: int) -> None:
    print(f"\n{'-' * 70}\n{title}\n{'-' * 70}")
    for domain, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        pct = round(n / total * 100, 2) if total else 0.0
        print(f"  {domain:<26} {n:<6} ({pct}%)")


def _classify_and_write_batched(postings: list[dict], with_llm: bool, max_batches: int | None = None) -> tuple[list[dict], int]:
    """Classifies and writes in GROQ_BATCH_SIZE-sized chunks, committing
    each chunk to the database immediately after it's classified --
    durability over round-trip count. Previously classify_postings() ran
    over the ENTIRE input in memory and update_postings_domain() wrote
    once at the very end, so an interrupt partway through a long run (a
    Ctrl-C, an OOM-kill, a workflow timeout) lost every already-classified
    row's Groq work along with the rest. Now an interrupt costs at most
    one batch.

    max_batches caps how many batches this call processes -- the rest of
    `postings` is left untouched (still domain IS NULL) for a future run
    to pick up; nothing is lost, since every prior batch was already
    written. Returns (results for postings actually processed,
    count of postings left unprocessed this call)."""
    from storage import update_postings_domain

    batches = [postings[i:i + GROQ_BATCH_SIZE] for i in range(0, len(postings), GROQ_BATCH_SIZE)]
    run_batches = batches if max_batches is None else batches[:max_batches]
    skipped = sum(len(b) for b in batches[len(run_batches):])

    all_results: list[dict] = []
    for batch_num, batch in enumerate(run_batches, start=1):
        batch_results = classify_postings(batch, with_llm=with_llm)
        write_rows = [
            {"id": r["id"], "domain": r["domain"], "domain_method": r["domain_method"], "domain_confidence": r["domain_confidence"]}
            for r in batch_results
        ]
        write_result = update_postings_domain(write_rows)
        msg = f"batch {batch_num} of {len(run_batches)}: {write_result['updated']} row(s) written ({write_result['failed']} failed)"
        logger.info(msg)
        print(msg)
        all_results.extend(batch_results)
    return all_results, skipped


def run_classification(
    with_llm: bool = True, only_unclassified: bool = True, quiet: bool = False, max_batches: int | None = None,
) -> dict:
    """quiet=True skips every print() (the detailed before/after report is
    meant for a human reading a terminal, not a CI log) and just returns
    the summary dict -- classify.yml's workflow run uses this and logs
    its own one-line summary instead. Returns
    {"total": int, "method_counts": Counter, "remaining": int} either way
    ("remaining" is a fresh post-run count of domain IS NULL rows, 0 when
    only_unclassified is False since --all touches everything). Writes
    happen per-batch (see _classify_and_write_batched), not once at the
    end, and max_batches caps how much of the backlog this call drains."""
    from storage import get_postings_for_domain_classification

    postings = get_postings_for_domain_classification(only_unclassified=only_unclassified)
    total = len(postings)
    if total == 0:
        if not quiet:
            print("No postings need domain classification (none with domain IS NULL).")
        logger.info("No postings need domain classification")
        return {"total": 0, "method_counts": Counter(), "remaining": 0}

    logger.info(f"Loaded {total} postings for domain classification ({-(-total // GROQ_BATCH_SIZE)} batch(es))")

    if quiet:
        results, _ = _classify_and_write_batched(postings, with_llm, max_batches=max_batches)
        remaining = len(get_postings_for_domain_classification(only_unclassified=True)) if only_unclassified else 0
        logger.info(f"Domain classification: {remaining} posting(s) still domain IS NULL after this run")
        return {"total": total, "method_counts": Counter(r["domain_method"] for r in results), "remaining": remaining}

    # ---- Classify + write, batched (see _classify_and_write_batched) ----
    results, skipped = _classify_and_write_batched(postings, with_llm, max_batches=max_batches)
    results_by_id = {r["id"]: r for r in results}
    # Reporting is scoped to what was ACTUALLY processed this call -- with
    # max_batches, that can be a strict subset of `postings` (the rest is
    # left domain IS NULL for a future run, see skipped above).
    processed_postings = [p for p in postings if p["id"] in results_by_id]
    processed_total = len(processed_postings)

    # ---- BEFORE: the old title-only, four-source-convention inference ----
    before_domains = [infer_domain_before(p.get("title")) for p in processed_postings]
    before_counts = _distribution(before_domains)
    before_other = before_counts.get("other_unclassified", 0)

    after_domains = [results_by_id[p["id"]]["domain"] for p in processed_postings]
    after_counts = _distribution(after_domains)
    after_other = after_counts.get("other", 0)

    method_counts = Counter(r["domain_method"] for r in results)

    # ------------------------------------------------------------------
    # Validation report
    # ------------------------------------------------------------------

    if skipped:
        print(f"\nNOTE: --max-batches capped this run; {skipped} posting(s) of {total} found "
              f"were left unprocessed (still domain IS NULL) for a future run.")

    print(f"\n{'=' * 70}\nDOMAIN DISTRIBUTION -- BEFORE (title-only) vs AFTER (three-stage)\n{'=' * 70}")
    _print_distribution(f"BEFORE ({processed_total} postings, old title-only inference)", before_counts, processed_total)
    _print_distribution(f"AFTER ({processed_total} postings, three-stage classifier)", after_counts, processed_total)

    print(f"\n{'=' * 70}\nPER-STAGE COUNTS\n{'=' * 70}")
    for method in ("rule_title", "rule_description", "llm", "unclassified"):
        n = method_counts.get(method, 0)
        pct = round(n / processed_total * 100, 2) if processed_total else 0.0
        print(f"  {method:<20} {n:<6} ({pct}%)")

    llm_rows = [r for r in results if r["domain_method"] == "llm"]
    print(f"\n{'=' * 70}\nRANDOM SAMPLE OF {SAMPLE_SIZE} LLM-CLASSIFIED POSTINGS\n{'=' * 70}")
    if not llm_rows:
        print("  (none -- stage 3 classified nothing, either --no-llm was passed, "
              "GROQ_API_KEY is unset, or every stage-3 candidate came back below "
              f"the {0.6} confidence threshold)")
    else:
        postings_by_id = {p["id"]: p for p in processed_postings}
        random.seed(2026)
        sample = random.sample(llm_rows, min(SAMPLE_SIZE, len(llm_rows)))
        for r in sample:
            title = postings_by_id[r["id"]].get("title") or "(no title)"
            print(f"  [{r['domain']:<22} conf={r['domain_confidence']}] {title}")

    other_pct = round(after_other / processed_total * 100, 2) if processed_total else 0.0
    print(f"\n{'=' * 70}\n'OTHER' RATE\n{'=' * 70}")
    print(f"Before: {before_other}/{processed_total} ({round(before_other / processed_total * 100, 2) if processed_total else 0.0}%) "
          f"-- category 'other_unclassified' under the old title-only inference")
    print(f"After:  {after_other}/{processed_total} ({other_pct}%) -- domain='other' under the three-stage classifier")

    other_rows = [r for r in results if r["domain"] == "other"]
    postings_by_id = {p["id"]: p for p in processed_postings}
    print(f"\n{'-' * 70}\nSample of {OTHER_SAMPLE_SIZE} postings still 'other' (for ambiguous-vs-vocabulary-gap review)\n{'-' * 70}")
    random.seed(99)
    other_sample = random.sample(other_rows, min(OTHER_SAMPLE_SIZE, len(other_rows)))
    for r in other_sample:
        title = postings_by_id[r["id"]].get("title") or "(no title)"
        method = r["domain_method"]
        conf = r["domain_confidence"]
        print(f"  [{method:<12} conf={conf}] {title}")

    remaining = len(get_postings_for_domain_classification(only_unclassified=True)) if only_unclassified else 0
    print(f"\n{remaining} posting(s) still domain IS NULL after this run.")

    return {"total": processed_total, "method_counts": method_counts, "remaining": remaining}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise three-stage domain classification")
    parser.add_argument("--no-llm", action="store_true", help="Skip stage 3 (LLM); unresolved postings stay 'other'")
    parser.add_argument(
        "--all", action="store_true",
        help="Full corpus reclassification (overwrites already-classified postings too). "
             "Default is incremental: domain IS NULL only.",
    )
    parser.add_argument(
        "--max-batches", type=int, default=None,
        help="Cap this run to N GROQ_BATCH_SIZE batches; the rest of the backlog waits for next run.",
    )
    args = parser.parse_args()
    run_classification(with_llm=not args.no_llm, only_unclassified=not args.all, max_batches=args.max_batches)


if __name__ == "__main__":
    main()
