"""Rukhwise job-family title normalization entrypoint.

  python classify_job_families.py
      INCREMENTAL (the default): only postings with family_method IS NULL
      -- i.e. never classified before. Two-stage classification (see
      rukhwise_scraper/job_family_classifier.py), writes job_family/
      family_method/family_confidence, and prints a report scoped to that
      subset: family distribution, per-method counts, %unmatched, and a
      gap report (unmatched title clusters >=5 postings). This is also
      what .github/workflows/classify.yml runs on its own independent
      daily schedule -- deliberately NOT wired into collect.py/
      collect.yml, so a Groq outage never touches collection (see
      collect.py's module docstring). family_method is NEVER left null by
      a completed classification (an unresolved title gets family_method=
      'unmatched', a real answer), so this scope can never re-touch an
      already-classified row.

  python classify_job_families.py --all
      Full run over EVERY posting in Supabase, including ones already
      classified -- overwrites their job_family/family_method/
      family_confidence from scratch. The deliberate, explicit path for a
      full corpus reclassification (e.g. after job_families.yaml grows);
      never the default, never runs automatically.

  python classify_job_families.py --no-llm
      Runs stage 1 (rules) only; anything it misses stays job_family=NULL,
      family_method='unmatched'. Composable with --all.

  python classify_job_families.py --max-batches 10
      Processes at most 10 GROQ_BATCH_SIZE-sized batches (200 postings)
      this run, then stops and logs how many family_method-null postings
      remain -- a large backlog drains over several scheduled runs
      instead of one run stalling on a long LLM sequence. Since writes
      happen per batch (see _classify_and_write_batched), the unprocessed
      remainder is simply picked up next run; nothing is lost by capping.
      This is what .github/workflows/classify.yml uses.

The gap report is input for a human to review and extend job_families.yaml
by hand -- nothing here adds to the vocabulary automatically.
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

from job_family_classifier import FAMILY_KEYS, GROQ_BATCH_SIZE, classify_postings, _strip_seniority_prefix  # noqa: E402

GAP_MIN_POSTINGS = 5
GAP_EXAMPLES_PER_CLUSTER = 3


def _normalize_for_gap(title: str) -> str:
    stripped = _strip_seniority_prefix(title or "")
    return " ".join(stripped.split()).casefold()


def _classify_and_write_batched(postings: list[dict], with_llm: bool, max_batches: int | None = None) -> tuple[list[dict], int]:
    """Classifies and writes in GROQ_BATCH_SIZE-sized chunks, committing
    each chunk to the database immediately after it's classified --
    durability over round-trip count. Previously classify_postings() ran
    over the ENTIRE input in memory and update_postings_family() wrote
    once at the very end, so an interrupt partway through a long run (a
    Ctrl-C, an OOM-kill, a workflow timeout) lost every already-classified
    row's Groq work along with the rest -- this is exactly what happened
    to an earlier interrupted run. Now an interrupt costs at most one
    batch.

    max_batches caps how many batches this call processes -- the rest of
    `postings` is left untouched (still family_method IS NULL) for a
    future run to pick up; nothing is lost, since every prior batch was
    already written. Returns (results for postings actually processed,
    count of postings left unprocessed this call)."""
    from storage import update_postings_family

    batches = [postings[i:i + GROQ_BATCH_SIZE] for i in range(0, len(postings), GROQ_BATCH_SIZE)]
    run_batches = batches if max_batches is None else batches[:max_batches]
    skipped = sum(len(b) for b in batches[len(run_batches):])

    all_results: list[dict] = []
    for batch_num, batch in enumerate(run_batches, start=1):
        batch_results = classify_postings(batch, with_llm=with_llm)
        write_rows = [
            {"id": r["id"], "job_family": r["job_family"], "family_method": r["family_method"], "family_confidence": r["family_confidence"]}
            for r in batch_results
        ]
        write_result = update_postings_family(write_rows)
        msg = f"batch {batch_num} of {len(run_batches)}: {write_result['updated']} row(s) written ({write_result['failed']} failed)"
        logger.info(msg)
        print(msg)
        all_results.extend(batch_results)
    return all_results, skipped


def run_classification(
    with_llm: bool = True, only_unclassified: bool = True, quiet: bool = False, max_batches: int | None = None,
) -> dict:
    """quiet=True skips every print() (the detailed report is meant for a
    human, not a CI log) and just returns the summary dict -- classify.yml's
    workflow run uses this and logs its own one-line summary instead.
    Returns {"total": int, "method_counts":
    Counter, "unmatched": int, "remaining": int} ("remaining" is a fresh
    post-run count of family_method IS NULL rows, 0 when only_unclassified
    is False). Writes happen per-batch (see _classify_and_write_batched),
    not once at the end, and max_batches caps how much of the backlog this
    call drains."""
    from storage import get_postings_for_family_classification

    postings = get_postings_for_family_classification(only_unclassified=only_unclassified)
    total = len(postings)
    if total == 0:
        if not quiet:
            print("No postings need job-family classification (none with family_method IS NULL).")
        logger.info("No postings need job-family classification")
        return {"total": 0, "method_counts": Counter(), "unmatched": 0, "remaining": 0}

    logger.info(f"Loaded {total} postings for job-family classification ({-(-total // GROQ_BATCH_SIZE)} batch(es))")

    results, skipped = _classify_and_write_batched(postings, with_llm, max_batches=max_batches)
    results_by_id = {r["id"]: r for r in results}
    # Reporting is scoped to what was ACTUALLY processed this call -- with
    # max_batches, that can be a strict subset of `postings`.
    processed_total = len(results)

    method_counts = Counter(r["family_method"] for r in results)
    family_counts = Counter(r["job_family"] for r in results if r["job_family"])

    remaining = len(get_postings_for_family_classification(only_unclassified=True)) if only_unclassified else 0

    if quiet:
        logger.info(f"Job-family classification: {remaining} posting(s) still family_method IS NULL after this run")
        return {"total": total, "method_counts": method_counts, "unmatched": method_counts.get("unmatched", 0), "remaining": remaining}

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    if skipped:
        print(f"\nNOTE: --max-batches capped this run; {skipped} posting(s) of {total} found "
              f"were left unprocessed (still family_method IS NULL) for a future run.")

    print(f"\n{'=' * 78}\nFAMILY DISTRIBUTION ({processed_total} postings)\n{'=' * 78}")
    for family, n in sorted(family_counts.items(), key=lambda kv: -kv[1]):
        pct = round(n / processed_total * 100, 2) if processed_total else 0.0
        print(f"  {family:<32} {n:<6} ({pct}%)")

    print(f"\n{'=' * 78}\nMETHOD SPLIT\n{'=' * 78}")
    for method in ("rule", "llm", "unmatched"):
        n = method_counts.get(method, 0)
        pct = round(n / processed_total * 100, 2) if processed_total else 0.0
        print(f"  {method:<12} {n:<6} ({pct}%)")

    unmatched_pct = round(method_counts.get("unmatched", 0) / processed_total * 100, 2) if processed_total else 0.0
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

    print(f"\n{remaining} posting(s) still family_method IS NULL after this run.")

    return {"total": processed_total, "method_counts": method_counts, "unmatched": method_counts.get("unmatched", 0), "remaining": remaining}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise job-family title normalization")
    parser.add_argument("--no-llm", action="store_true", help="Skip stage 2 (LLM); unresolved titles stay unmatched")
    parser.add_argument(
        "--all", action="store_true",
        help="Full corpus reclassification (overwrites already-classified postings too). "
             "Default is incremental: family_method IS NULL only.",
    )
    parser.add_argument(
        "--max-batches", type=int, default=None,
        help="Cap this run to N GROQ_BATCH_SIZE batches; the rest of the backlog waits for next run.",
    )
    args = parser.parse_args()
    run_classification(with_llm=not args.no_llm, only_unclassified=not args.all, max_batches=args.max_batches)


if __name__ == "__main__":
    main()
