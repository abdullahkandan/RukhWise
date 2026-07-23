"""Rukhwise skill + structured extraction entrypoint.

  python extract.py --all
      Backfill: extraction_method='taxonomy_v2' over every posting
      currently in Supabase, regardless of when/how it was collected.
      Also runs structured credential/experience extraction
      (structured_extraction.py) over the same postings, writing
      degree_level/degree_field/has_certification/experience_min_years/
      experience_max_years/experience_level directly onto each posting row.

  python extract.py --run-id <run_id>
      Extract only the postings belonging to one scrape run. This is what
      collect.py calls automatically after every collection run, over
      exactly the postings that run touched (inserted or updated).

Re-running extraction over an already-processed posting is always safe and
cheap -- storage.store_skill_mentions() inserts with ON CONFLICT
(posting_id, skill, extraction_method) DO NOTHING, so nothing duplicates.
structured-field writes are a full recomputation each time (unconditional
SET, not coalesce), which is correct: if a posting's description changed
enough to no longer match a prior extraction, the stale value should
clear, not linger.

taxonomy_v1.yaml and every extraction_method='taxonomy_v1' skill_mentions
row are left in place, untouched -- this script only ever ADDS rows under
the new extraction_method, so the v1 pass stays fully auditable (see
output/taxonomy_v2_spec.md section 1).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

EXTRACTION_METHOD = "taxonomy_v2"


def run_extraction(run_id: str | None) -> dict:
    """Extract skills + structured credential/experience fields for
    postings from one run (run_id given) or every posting in Supabase
    (run_id=None).

    Returns {"postings_processed", "mentions_found", "inserted", "skipped",
    "failed", "structured_updated", "structured_failed"}.
    """
    from extract_skills import extract_skills, skill_category, skill_requirement_type
    from storage import get_postings_for_extraction, store_skill_mentions, update_postings_structured
    from structured_extraction import extract_structured_fields

    postings = get_postings_for_extraction(run_id=run_id)
    logger.info(
        f"Extracting for {len(postings)} postings "
        f"({'all postings' if run_id is None else f'run_id={run_id}'})"
    )

    mentions = []
    structured_rows = []
    for posting in postings:
        for skill in extract_skills(posting):
            mentions.append({
                "posting_id": posting["id"],
                "skill": skill,
                "category": skill_category(skill),
                "requirement_type": skill_requirement_type(skill),
            })
        structured_rows.append({"id": posting["id"], **extract_structured_fields(posting)})

    logger.info(f"Found {len(mentions)} skill mentions across {len(postings)} postings")

    counts = store_skill_mentions(mentions, extraction_method=EXTRACTION_METHOD)
    structured_counts = update_postings_structured(structured_rows)

    result = {
        "postings_processed": len(postings),
        "mentions_found": len(mentions),
        **counts,
        "structured_updated": structured_counts["updated"],
        "structured_failed": structured_counts["failed"],
    }
    logger.info(
        f"EXTRACTION SUMMARY postings={result['postings_processed']} "
        f"mentions_found={result['mentions_found']} inserted={result['inserted']} "
        f"skipped={result['skipped']} failed={result['failed']} "
        f"structured_updated={result['structured_updated']} structured_failed={result['structured_failed']}"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise skill + structured extraction")
    parser.add_argument("--all", action="store_true", help="Extract for every posting in Supabase")
    parser.add_argument("--run-id", help="Extract only postings from this scrape_run_id")
    args = parser.parse_args()

    if args.all:
        run_extraction(run_id=None)
    elif args.run_id:
        run_extraction(run_id=args.run_id)
    else:
        logger.error("Specify --all or --run-id <id>")
        sys.exit(1)


if __name__ == "__main__":
    main()
