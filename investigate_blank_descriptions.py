"""Investigate blank/null descriptions -- upstream of the taxonomy v3
skill-gap work. Read-only; no writes anywhere (the domain_method backfill
this investigation motivates lives in backfill_llm_title_only.py, and the
Rozee detail-page enrichment this investigation scopes is not built here).

  python investigate_blank_descriptions.py
      TASK 1 (quantify): counts postings with null/empty description, by
      source and by corrected domain. For those postings, reports how
      many have non-empty skills_raw (Rozee's own listing-page tags,
      captured at scrape time even though Rozee never gets a
      detail-page description -- see rozee_parser.py/storage.py's
      get_postings_needing_enrichment). Splits the depth metric
      (skill_substantive, taxonomy_v2) three ways: postings WITH a
      description, postings WITHOUT one but WITH skills_raw, and
      postings with NEITHER -- that third bucket cannot be measured by
      any taxonomy no matter how good it is, and reporting it as a
      "coverage failure" would blame the taxonomy for a data-collection
      gap that isn't its problem to solve.

      TASK 3 (scoping): reports the source breakdown of blank
      descriptions and assesses, per source, whether a detail-page
      fetch could fill them in -- with a time/effort estimate. Does
      NOT build anything.
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

_NON_SUBSTANTIVE_CATEGORIES = frozenset({"soft", "office_admin"})
_CURRENT_EXTRACTION_METHOD = "taxonomy_v3"


def _is_blank(text: str | None) -> bool:
    return not (text or "").strip()


def _skills_raw_nonempty(skills_raw) -> bool:
    """Same shape-awareness as storage._skills_raw_empty (inverted):
    Rozee's skills_raw is a JSON list of tag strings (from
    rozee_parser._extract_skills); Mustakbil's is a dict with
    'required_skills_text' (from mustakbil.enrich_jobs). Both are
    handled since a posting's source determines which shape it has."""
    if not skills_raw:
        return False
    if isinstance(skills_raw, dict):
        return bool(skills_raw.get("required_skills_text"))
    return bool(skills_raw)  # non-empty list


def _skill_substantive_counts(mentions: list[dict]) -> dict[str, int]:
    """posting_id -> distinct requirement_type='skill' matches, excluding
    soft/office_admin, from the current (taxonomy_v2) pass only --
    identical definition used throughout this project (compare_taxonomy_
    depth.py, skill_gap_discovery.py)."""
    by_posting: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        if m.get("extraction_method") != _CURRENT_EXTRACTION_METHOD:
            continue
        if m.get("requirement_type") != "skill":
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


def run_task1(postings: list[dict], mentions: list[dict]) -> dict[str, list[dict]]:
    """Returns the three buckets (postings lists) for TASK 3 to reuse for
    its source breakdown."""
    substantive = _skill_substantive_counts(mentions)

    blank_by_source: dict[str, int] = defaultdict(int)
    blank_by_domain: dict[str, int] = defaultdict(int)
    total_by_source: dict[str, int] = defaultdict(int)
    total_by_domain: dict[str, int] = defaultdict(int)
    blank_with_skills_raw = 0
    blank_total = 0

    buckets: dict[str, list[dict]] = {"A_has_description": [], "B_no_description_has_skills_raw": [], "C_neither_unmeasurable": []}

    for p in postings:
        source = p.get("source") or "unknown"
        domain = p.get("domain") or "other"
        total_by_source[source] += 1
        total_by_domain[domain] += 1

        has_desc = not _is_blank(p.get("description"))
        has_skills_raw = _skills_raw_nonempty(p.get("skills_raw"))

        if not has_desc:
            blank_total += 1
            blank_by_source[source] += 1
            blank_by_domain[domain] += 1
            if has_skills_raw:
                blank_with_skills_raw += 1

        if has_desc:
            buckets["A_has_description"].append(p)
        elif has_skills_raw:
            buckets["B_no_description_has_skills_raw"].append(p)
        else:
            buckets["C_neither_unmeasurable"].append(p)

    print(f"\n{'=' * 78}\nTASK 1 -- BLANK DESCRIPTION QUANTIFICATION\n{'=' * 78}")
    print(f"Total postings: {len(postings)}")
    print(f"Blank/null description: {blank_total} ({round(blank_total/len(postings)*100,1)}%)")
    print(f"  ...of which have non-empty skills_raw: {blank_with_skills_raw} ({round(blank_with_skills_raw/blank_total*100,1) if blank_total else 0}% of blanks)")

    print(f"\n{'-' * 70}\nBLANK DESCRIPTIONS BY SOURCE\n{'-' * 70}")
    header = f"{'source':<16}{'blank':<8}{'total':<8}{'%blank':<8}"
    print(header)
    for source in sorted(total_by_source, key=lambda s: -blank_by_source.get(s, 0)):
        b, t = blank_by_source.get(source, 0), total_by_source[source]
        print(f"{source:<16}{b:<8}{t:<8}{round(b/t*100,1) if t else 0:<8}")

    print(f"\n{'-' * 70}\nBLANK DESCRIPTIONS BY DOMAIN\n{'-' * 70}")
    print(header.replace("source", "domain "))
    for domain in sorted(total_by_domain, key=lambda d: -blank_by_domain.get(d, 0)):
        b, t = blank_by_domain.get(domain, 0), total_by_domain[domain]
        if b == 0:
            continue
        print(f"{domain:<16}{b:<8}{t:<8}{round(b/t*100,1) if t else 0:<8}")

    print(f"\n{'=' * 78}\nDEPTH METRIC (skill_substantive) -- SPLIT THREE WAYS\n{'=' * 78}")
    print(
        "Bucket C (neither description nor skills_raw) is UNMEASURABLE by any taxonomy -- "
        "excluded from gap analysis, not counted as a coverage failure.\n"
    )
    header2 = f"{'bucket':<38}{'n':<8}{'median':<9}{'%<=1':<8}"
    print(header2)
    print("-" * len(header2))
    for key, label in [
        ("A_has_description", "A: has description"),
        ("B_no_description_has_skills_raw", "B: no description, has skills_raw"),
        ("C_neither_unmeasurable", "C: neither (UNMEASURABLE, excluded)"),
    ]:
        counts = [substantive.get(p["id"], 0) for p in buckets[key]]
        stats = _row_stats(counts)
        print(f"{label:<38}{stats['n']:<8}{stats['median']:<9}{stats['pct_le1']:<8}")

    measurable_total = len(buckets["A_has_description"]) + len(buckets["B_no_description_has_skills_raw"])
    print(
        f"\nMeasurable corpus (A+B): {measurable_total} / {len(postings)} "
        f"({round(measurable_total/len(postings)*100,1)}%). "
        f"Bucket C excluded: {len(buckets['C_neither_unmeasurable'])} "
        f"({round(len(buckets['C_neither_unmeasurable'])/len(postings)*100,1)}%)."
    )

    return buckets


def run_task3(buckets: dict[str, list[dict]]) -> None:
    blank_postings = buckets["B_no_description_has_skills_raw"] + buckets["C_neither_unmeasurable"]
    by_source: dict[str, int] = defaultdict(int)
    for p in blank_postings:
        by_source[p.get("source") or "unknown"] += 1

    print(f"\n{'=' * 78}\nTASK 3 -- CAN THE BLANKS BE FILLED? (scoping only, nothing built)\n{'=' * 78}")
    print(f"Blank-description postings by source: {dict(sorted(by_source.items(), key=lambda kv: -kv[1]))}\n")

    print(
        "rozee: has a detail_url for every posting and an existing Playwright+stealth\n"
        "  fetcher (fetcher.py) already proven against Rozee's Cloudflare challenge for\n"
        "  LISTING pages. No detail-PAGE parser exists yet (rozee_parser.py only parses\n"
        "  search-results cards -- title/company/city/salary/tags/detail_url, no\n"
        "  description field at all). storage.py's own comment already flags this as\n"
        "  the known gap: 'Rozee rows legitimately have null descriptions until Rozee\n"
        "  gets its own detail-enrichment path.' This IS the 'future option' -- feasible,\n"
        "  not yet built. New work needed: (a) a `rozee_detail_parser.py` to extract the\n"
        "  description from a job detail page's HTML (unknown markup -- needs one real\n"
        "  page inspected first), (b) a `fetch_rozee_details()`/`enrich_rozee_jobs()`\n"
        "  pair mirroring mustakbil.py's fetch_job_detail/enrich_jobs, (c) a storage.py\n"
        "  read/write pair mirroring get_postings_needing_enrichment/enrich_postings\n"
        "  scoped to source='rozee' instead of 'mustakbil'.\n"
    )
    print(
        "mustakbil: ALREADY has a working detail-enrichment path (fetch_job_detail,\n"
        "  enrich_jobs, get_postings_needing_enrichment, enrich_postings -- see\n"
        "  collect.py's --enrich-all). Any Mustakbil rows still blank are rows that\n"
        "  were never enriched (or whose enrichment call failed) -- no new capability\n"
        "  needed, just re-running the existing pipeline: `python collect.py\n"
        "  --enrich-all`.\n"
    )
    print(
        "indeed: jobspy's Indeed stream returns full description text directly from\n"
        "  Indeed's own search-results response -- no separate detail-page round trip\n"
        "  exists in jobspy for Indeed (unlike LinkedIn, see below), so a blank Indeed\n"
        "  description means Indeed itself didn't have one for that listing. There is\n"
        "  no additional fetch to make here; re-scraping the same job would very likely\n"
        "  return the same blank.\n"
    )
    print(
        "linkedin: jobspy_source.py already passes linkedin_fetch_description=True,\n"
        "  meaning jobspy already performs the extra per-listing detail-page fetch\n"
        "  LinkedIn requires. A blank LinkedIn description despite that flag means that\n"
        "  specific detail-page fetch failed or the listing had nothing to return\n"
        "  (removed/expired). A retry might recover some, but LinkedIn is the source\n"
        "  this codebase's own docs already flag as the most anti-bot-sensitive (geo-\n"
        "  leak issues, staleness) -- re-hitting detail pages for already-collected,\n"
        "  possibly-stale postings carries more account-flagging risk than it's worth\n"
        "  for what's likely a small population.\n"
    )

    rozee_count = by_source.get("rozee", 0)
    print(f"{'-' * 70}\nCOST ESTIMATE -- Rozee detail-page fetch (the one real build option)\n{'-' * 70}")
    print(
        f"Target population: {rozee_count} Rozee posting(s) with no description.\n"
        "Per-page cost, based on fetcher.py's existing timing (same Cloudflare-gated\n"
        "path already used for listing pages): ~2.5s fixed post-load wait + 2-4s\n"
        "randomized inter-request delay + actual page-render time (~2-5s observed for\n"
        "listing pages) = roughly 7-12 seconds per detail page, SEQUENTIAL (Fetcher\n"
        "wraps one browser page, no concurrency in the current design)."
    )
    if rozee_count:
        low_s, high_s = rozee_count * 7, rozee_count * 12
        print(
            f"Estimated run time: {round(low_s/60,1)}-{round(high_s/60,1)} minutes for "
            f"{rozee_count} pages, plus normal scraper retry/backoff overhead on failures."
        )
    print(
        "Development cost (not run cost): a detail-page parser needs at least one real\n"
        "Rozee job detail page inspected to find the description markup -- unlike\n"
        "Mustakbil's enrichment (which reused an already-documented API response\n"
        "shape), Rozee has no API, only rendered HTML, so this follows the same\n"
        "reverse-engineering path rozee_parser.py's own listing-card parser did."
    )


def main() -> None:
    from storage import get_postings_for_blank_description_audit, get_skill_mentions_for_analysis

    postings = get_postings_for_blank_description_audit()
    mentions = get_skill_mentions_for_analysis()
    logger.info(f"Loaded {len(postings)} postings, {len(mentions)} skill_mentions rows")

    buckets = run_task1(postings, mentions)
    run_task3(buckets)


if __name__ == "__main__":
    main()
