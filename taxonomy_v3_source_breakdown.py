"""Per-domain breakdown of where v3's extra mentions actually came from,
and a skill_substantive delta with the one reclassified entry excluded --
isolating genuine new coverage (new categories + new entries in existing
categories) from a pure relabeling effect (customer_service moving from
'soft' to the new 'customer_service' category with its aliases unchanged,
which means it was already matching in v2 too, just never counted toward
skill_substantive there).

Key sets are derived programmatically by diffing taxonomy_v2.yaml against
taxonomy_v3.yaml (never hand-maintained), so this can't silently drift out
of sync with the taxonomy files:
  bucket A -- keys that exist in v3 but not v2, category is one of the 7 new ones
  bucket B -- keys that exist in v3 but not v2, category already existed in v2
  bucket C -- keys that exist in BOTH v2 and v3 but whose category changed
              (currently just customer_service: soft -> customer_service)

Read-only.
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

NEW_V3_CATEGORIES = frozenset({
    "health_clinical", "lab_science", "safety_compliance", "supply_chain",
    "electrical_mechanical", "teaching", "customer_service",
})
_NON_SUBSTANTIVE_CATEGORIES = frozenset({"soft", "office_admin"})


def _derive_key_sets() -> tuple[set[str], set[str], set[str]]:
    v2 = yaml.safe_load(open("rukhwise_scraper/taxonomy_v2.yaml", encoding="utf-8"))
    v3 = yaml.safe_load(open("rukhwise_scraper/taxonomy_v3.yaml", encoding="utf-8"))
    v2_keys, v3_keys = set(v2["skills"]), set(v3["skills"])
    new_keys = v3_keys - v2_keys

    bucket_a = {k for k in new_keys if v3["skills"][k]["category"] in NEW_V3_CATEGORIES}
    bucket_b = new_keys - bucket_a
    bucket_c = {k for k in (v2_keys & v3_keys) if v2["skills"][k]["category"] != v3["skills"][k]["category"]}
    return bucket_a, bucket_b, bucket_c


def _row_stats(counts: list[int]) -> dict:
    n = len(counts)
    le1 = sum(1 for c in counts if c <= 1)
    return {"n": n, "median": round(statistics.median(counts), 2) if counts else 0, "pct_le1": round(le1 / n * 100, 2) if n else 0.0}


def main() -> None:
    from storage import get_postings_for_v3_depth_comparison, get_skill_mentions_for_analysis

    bucket_a, bucket_b, bucket_c = _derive_key_sets()
    logger.info(f"Bucket A (new keys, new categories): {len(bucket_a)}")
    logger.info(f"Bucket B (new keys, existing categories): {len(bucket_b)}")
    logger.info(f"Bucket C (reclassified existing keys): {len(bucket_c)} -> {sorted(bucket_c)}")

    postings = get_postings_for_v3_depth_comparison()
    mentions = get_skill_mentions_for_analysis()
    logger.info(f"Loaded {len(postings)} postings, {len(mentions)} skill_mentions rows")

    postings_by_id = {p["id"]: p for p in postings}

    # ---- PART 1: per-domain mention counts by bucket ----
    counts_by_domain: dict[str, dict[str, int]] = defaultdict(lambda: {"a": 0, "b": 0, "c": 0, "other": 0})
    for m in mentions:
        if m.get("extraction_method") != "taxonomy_v3":
            continue
        posting = postings_by_id.get(m["posting_id"])
        domain = (posting.get("domain") or "other") if posting else "other"
        skill = m["skill"]
        if skill in bucket_a:
            counts_by_domain[domain]["a"] += 1
        elif skill in bucket_b:
            counts_by_domain[domain]["b"] += 1
        elif skill in bucket_c:
            counts_by_domain[domain]["c"] += 1
        else:
            counts_by_domain[domain]["other"] += 1

    print(f"\n{'=' * 100}\nPART 1 -- v3 MENTIONS PER DOMAIN, BY SOURCE\n{'=' * 100}")
    print(
        "a) new categories (excl. the reclassified customer_service entry)  "
        "b) new entries in existing categories  c) reclassified existing entry (customer_service)  "
        "other) unchanged v1/v2 entries\n"
    )
    header = f"{'domain':<26}{'a) new cat':<12}{'b) new entry':<14}{'c) reclassified':<17}{'other (unchanged)':<19}{'total':<8}"
    print(header)
    print("-" * len(header))
    ordered = sorted(counts_by_domain, key=lambda d: -sum(counts_by_domain[d].values()))
    for domain in ordered:
        c = counts_by_domain[domain]
        total = sum(c.values())
        print(f"{domain:<26}{c['a']:<12}{c['b']:<14}{c['c']:<17}{c['other']:<19}{total:<8}")
    totals = {k: sum(counts_by_domain[d][k] for d in counts_by_domain) for k in ("a", "b", "c", "other")}
    print("-" * len(header))
    print(f"{'TOTAL':<26}{totals['a']:<12}{totals['b']:<14}{totals['c']:<17}{totals['other']:<19}{sum(totals.values()):<8}")

    # ---- PART 2: skill_substantive delta with bucket C excluded ----
    def substantive_counts(extraction_method: str, exclude_keys: set[str] = frozenset()) -> dict[str, int]:
        by_posting: dict[str, set[str]] = defaultdict(set)
        for m in mentions:
            if m.get("extraction_method") != extraction_method:
                continue
            if m.get("requirement_type") != "skill":
                continue
            if m.get("category") in _NON_SUBSTANTIVE_CATEGORIES:
                continue
            if m["skill"] in exclude_keys:
                continue
            by_posting[m["posting_id"]].add(m["skill"])
        return {pid: len(skills) for pid, skills in by_posting.items()}

    v2_counts = substantive_counts("taxonomy_v2")
    v3_full_counts = substantive_counts("taxonomy_v3")
    v3_excl_c_counts = substantive_counts("taxonomy_v3", exclude_keys=bucket_c)

    by_domain_v2: dict[str, list[int]] = defaultdict(list)
    by_domain_v3_full: dict[str, list[int]] = defaultdict(list)
    by_domain_v3_excl_c: dict[str, list[int]] = defaultdict(list)

    for p in postings:
        pid = p["id"]
        domain = p.get("domain") or "other"
        by_domain_v2[domain].append(v2_counts.get(pid, 0))
        by_domain_v3_full[domain].append(v3_full_counts.get(pid, 0))
        by_domain_v3_excl_c[domain].append(v3_excl_c_counts.get(pid, 0))

    print(f"\n{'=' * 100}\nPART 2 -- skill_substantive: v2 vs v3(full) vs v3(reclassification excluded)\n{'=' * 100}")
    print(
        "v3(excl. c) treats the reclassified customer_service entry as if it were still 'soft' -- same "
        "aliases as v2, so it was already matching then, just never counted toward skill_substantive. "
        "The gap between v3(full) and v3(excl. c) is pure relabeling; the gap between v3(excl. c) and v2 "
        "is genuine new coverage from buckets A+B only.\n"
    )
    header2 = f"{'domain':<26}{'n':<6}{'%<=1_v2':<10}{'%<=1_v3_full':<14}{'%<=1_v3_excl_c':<16}{'relabel pts':<12}"
    print(header2)
    print("-" * len(header2))
    ordered2 = sorted(by_domain_v3_full, key=lambda d: -len(by_domain_v3_full[d]))
    for domain in ordered2:
        s2 = _row_stats(by_domain_v2.get(domain, []))
        s3f = _row_stats(by_domain_v3_full.get(domain, []))
        s3e = _row_stats(by_domain_v3_excl_c.get(domain, []))
        relabel_pts = round(s3e["pct_le1"] - s3f["pct_le1"], 2)
        print(f"{domain:<26}{s3f['n']:<6}{s2['pct_le1']:<10}{s3f['pct_le1']:<14}{s3e['pct_le1']:<16}{relabel_pts:<12}")


if __name__ == "__main__":
    main()
