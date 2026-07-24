"""Diagnostic (not a builder): was the low candidate yield in trades_
technical/healthcare/education/logistics_supply_chain a genuine result,
or an artifact of extracting from postings that had no description text
to extract from? And separately: for the postings that DO have
structured skills_raw tags but no description, what does tag-based
(no-LLM) discovery surface that taxonomy_v2 doesn't already match?

  python skill_gap_source_diagnostic.py
      TASK 1: for each of the 9 domains that qualified for skill_gap_
      discovery.py's extraction pass, reports (over its skill_substantive
      <=1 target postings): count by source, how many have a description
      vs only skills_raw vs neither, and how many contributed >=1
      candidate that survived aggregation (re-derived from the already-
      logged output/skill_extraction_{domain}_*.json raw responses --
      no new Groq calls). Uses the CURRENT verbatim+scaled-bar
      aggregation rule uniformly across all 9 domains for an apples-to-
      apples comparison (four of the nine -- sales_marketing,
      admin_clerical, engineering, technology_it -- were frozen under
      the OLDER flat >=3-company rule in the approved sheet itself; this
      diagnostic recomputes them under the current rule purely for this
      analysis, so its survivor counts for those four may differ
      slightly from output/taxonomy_v3_skills_2026-07-24.md).

      TASK 2: for target postings with skills_raw but no description
      (bucket B), runs tag-based discovery directly against skills_raw
      -- no LLM, no paraphrase risk, since Rozee's skills_raw is already
      a list of discrete employer-declared tags. Reports, per domain,
      which tags taxonomy_v2 does NOT already match, ranked by distinct
      companies, gated by the same scaled company bar skill_gap_
      discovery.py uses (>=3 for domains with >=60 targets, >=2 below).

Nothing here is applied to any taxonomy file.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

import extract_skills  # noqa: E402 -- reuse the already-loaded taxonomy_v2 patterns, one source of truth
from skill_gap_discovery import (  # noqa: E402
    select_targets,
    aggregate_domain,
    _min_companies_for,
    _normalize_phrase,
)

OUTPUT_DIR = Path("output")
QUALIFYING_DOMAINS = [
    "sales_marketing", "admin_clerical", "trades_technical", "customer_support_bpo",
    "education", "healthcare", "engineering", "technology_it", "logistics_supply_chain",
]


def _is_blank(text: str | None) -> bool:
    return not (text or "").strip()


def _skills_raw_nonempty(skills_raw) -> bool:
    if not skills_raw:
        return False
    if isinstance(skills_raw, dict):
        return bool(skills_raw.get("required_skills_text"))
    return bool(skills_raw)


def _bucket(posting: dict) -> str:
    has_desc = not _is_blank(posting.get("description"))
    has_tags = _skills_raw_nonempty(posting.get("skills_raw"))
    if has_desc:
        return "A_has_description"
    if has_tags:
        return "B_no_description_has_skills_raw"
    return "C_neither"


def _load_extracted_from_log(domain: str) -> dict[str, list[str]]:
    """Reconstructs posting_id -> [raw phrases] from the already-logged
    output/skill_extraction_{domain}_*.json batch records -- exactly
    what extract_skills_for_domain would have returned, without calling
    Groq again."""
    candidates = sorted(OUTPUT_DIR.glob(f"skill_extraction_{domain}_*.json"))
    if not candidates:
        logger.warning(f"[{domain}] no skill_extraction log found -- treating as empty")
        return {}
    log_path = candidates[-1]
    batch_records = json.loads(log_path.read_text(encoding="utf-8"))

    extracted: dict[str, list[str]] = {}
    for batch in batch_records:
        response = batch.get("response")
        if not response:
            continue
        batch_ids = set(batch.get("posting_ids", []))
        for item in response:
            pid = item.get("id")
            if pid not in batch_ids:
                continue
            skills = item.get("skills") or []
            extracted[pid] = [s for s in skills if isinstance(s, str) and s.strip()]
    return extracted


def run_task1(postings_by_id: dict[str, dict], targets_by_domain: dict[str, list[dict]]) -> None:
    print(f"\n{'=' * 100}\nTASK 1 -- SOURCE BREAKDOWN OF SKILL-GAP TARGETS (genuine gap vs textless-extraction artifact)\n{'=' * 100}")
    print(
        "NOTE: 'contributed' below is recomputed with the CURRENT verbatim+scaled-bar rule for all 9 "
        "domains (apples-to-apples). For sales_marketing/admin_clerical/engineering/technology_it this "
        "may differ slightly from the already-approved sheet, which used the older flat >=3-company bar.\n"
    )

    for domain in QUALIFYING_DOMAINS:
        targets = targets_by_domain.get(domain, [])
        if not targets:
            print(f"\n{domain}: (no target postings found)")
            continue

        by_source: dict[str, int] = defaultdict(int)
        buckets: dict[str, int] = defaultdict(int)
        for p in targets:
            by_source[p.get("source") or "unknown"] += 1
            buckets[_bucket(p)] += 1

        min_companies = _min_companies_for(len(targets))
        extracted = _load_extracted_from_log(domain)
        result = aggregate_domain(domain, targets, extracted, min_companies)
        contributed_ids: set[str] = set()
        for cand in result.survivors:
            contributed_ids |= cand.postings

        source_str = ", ".join(f"{s}:{n}" for s, n in sorted(by_source.items(), key=lambda kv: -kv[1]))
        print(f"\n{domain} -- {len(targets)} target postings, company bar >={min_companies}")
        print(f"  by source:     {source_str}")
        print(
            f"  by text avail: A(has description)={buckets.get('A_has_description', 0)}, "
            f"B(tags only)={buckets.get('B_no_description_has_skills_raw', 0)}, "
            f"C(neither)={buckets.get('C_neither', 0)}"
        )
        print(f"  contributed >=1 surviving candidate: {len(contributed_ids)}/{len(targets)}")


def run_task2(postings_by_id: dict[str, dict], targets_by_domain: dict[str, list[dict]]) -> None:
    print(f"\n{'=' * 100}\nTASK 2 -- TAG-BASED DISCOVERY ON skills_raw (bucket B: no description, has tags -- no LLM)\n{'=' * 100}")
    print("Nothing here is applied to any taxonomy file -- report only.\n")

    for domain in QUALIFYING_DOMAINS:
        targets = targets_by_domain.get(domain, [])
        bucket_b = [p for p in targets if _bucket(p) == "B_no_description_has_skills_raw"]
        min_companies = _min_companies_for(len(targets))

        print(f"\n{'-' * 80}\n{domain} -- {len(bucket_b)} bucket-B target postings, company bar >={min_companies}\n{'-' * 80}")
        if not bucket_b:
            print("  (none)")
            continue

        candidates: dict[str, dict] = {}  # normalized tag -> {"display", "postings": set, "companies": set}
        skipped_non_list = 0
        tags_seen = 0
        tags_already_matched = 0

        for p in bucket_b:
            skills_raw = p.get("skills_raw")
            if not isinstance(skills_raw, list):
                skipped_non_list += 1
                continue
            company = p.get("company") or "(unknown company)"
            for raw_tag in skills_raw:
                tag = str(raw_tag).strip()
                if not tag:
                    continue
                tags_seen += 1
                already_matched = any(pattern.search(tag.lower()) for _, _, pattern in extract_skills._SKILL_PATTERNS)
                if already_matched:
                    tags_already_matched += 1
                    continue
                key = _normalize_phrase(tag)
                entry = candidates.setdefault(key, {"display": tag, "postings": set(), "companies": set()})
                entry["postings"].add(p["id"])
                entry["companies"].add(company)

        if skipped_non_list:
            print(f"  ({skipped_non_list} bucket-B posting(s) had non-list skills_raw -- a text blob, not discrete tags -- skipped for this method)")

        survivors = [c for c in candidates.values() if len(c["companies"]) >= min_companies]
        survivors.sort(key=lambda c: (-len(c["companies"]), -len(c["postings"])))

        print(
            f"  tags seen: {tags_seen}, already matched by taxonomy_v2: {tags_already_matched}, "
            f"distinct unmatched tags: {len(candidates)}, surviving >={min_companies}-company bar: {len(survivors)}"
        )
        for c in survivors:
            print(f"    - {c['display']:<40} postings={len(c['postings']):<4} companies={len(c['companies'])}")


def main() -> None:
    from storage import get_postings_for_skill_gap_analysis, get_skill_mentions_for_analysis

    postings = get_postings_for_skill_gap_analysis()
    mentions = get_skill_mentions_for_analysis()
    logger.info(f"Loaded {len(postings)} postings, {len(mentions)} skill_mentions rows")

    postings_by_id = {p["id"]: p for p in postings}
    targets_by_domain = select_targets(postings, mentions)

    run_task1(postings_by_id, targets_by_domain)
    run_task2(postings_by_id, targets_by_domain)


if __name__ == "__main__":
    main()
