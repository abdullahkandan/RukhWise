"""Rukhwise read API. Read-only, anon-key Supabase access -- see queries.py.

All response shaping/aggregation happens here; queries.py never returns
anything except raw rows. Every route is wrapped in the in-process TTL
cache (cache.py) since the underlying data changes at most daily.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import queries
from cache import cached

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rukhwise_api")

app = FastAPI(title="Rukhwise API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # frontend domain TBD; tighten once known
    allow_methods=["GET"],  # read-only service
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _skills_raw_empty(skills_raw) -> bool:
    """Mirrors rukhwise_scraper/storage.py's _skills_raw_empty -- duplicated
    rather than imported, since api/ is deliberately self-contained (no
    cross-package imports into the scraper, which has its own dependency
    surface like playwright/beautifulsoup this service doesn't need)."""
    if not skills_raw:
        return True
    if isinstance(skills_raw, dict):
        return not skills_raw.get("required_skills_text")
    return False


_EXPERIENCE_BAND_ORDER = [
    "Entry (0-1 yrs)",
    "Junior (2-3 yrs)",
    "Mid (4-6 yrs)",
    "Senior (7+ yrs)",
    "Unspecified",
]


def _experience_band(experience_raw: str | None) -> str:
    if not experience_raw:
        return "Unspecified"
    text = experience_raw.lower()
    if "fresh" in text or "entry" in text or "intern" in text:
        return "Entry (0-1 yrs)"
    m = re.search(r"(\d+)", text)
    if not m:
        return "Unspecified"
    years = int(m.group(1))
    if years <= 1:
        return "Entry (0-1 yrs)"
    if years <= 3:
        return "Junior (2-3 yrs)"
    if years <= 6:
        return "Mid (4-6 yrs)"
    return "Senior (7+ yrs)"


def _median_iqr(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "median": None, "q1": None, "q3": None, "iqr": None}
    values = sorted(values)
    median = statistics.median(values)
    if len(values) >= 4:
        q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    else:
        q1 = q3 = median
    return {
        "count": len(values),
        "median": round(median, 2),
        "q1": round(q1, 2),
        "q3": round(q3, 2),
        "iqr": round(q3 - q1, 2),
    }


def _posting_salary_point(p: dict) -> float | None:
    """Midpoint of (salary_min, salary_max) when both present, else
    whichever one is present."""
    mn, mx = p.get("salary_min"), p.get("salary_max")
    if mn is not None and mx is not None:
        return (mn + mx) / 2
    return mn if mn is not None else mx


def _bucket_key(d: date, granularity: str) -> str:
    if granularity == "day":
        return d.isoformat()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def _bucket_sequence(start: date, end: date, granularity: str) -> list[str]:
    """Complete list of bucket labels from start to end, so trend series
    show real zeros for empty periods instead of silently skipping them."""
    if granularity == "week":
        start = start - timedelta(days=start.weekday())
        end = end - timedelta(days=end.weekday())
        step = timedelta(days=7)
    else:
        step = timedelta(days=1)

    labels = []
    cur = start
    while cur <= end:
        labels.append(cur.isoformat())
        cur += step
    return labels


def _skill_postings_map(mentions: list[dict]) -> dict[str, set[str]]:
    m = defaultdict(set)
    for row in mentions:
        m[row["skill"]].add(row["posting_id"])
    return m


def _require_known_skill(skill: str, taxonomy: dict) -> dict:
    spec = taxonomy["skills"].get(skill)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown skill '{skill}'")
    return spec


# --------------------------------------------------------------------------
# GET /stats/overview
# --------------------------------------------------------------------------

@app.get("/stats/overview")
@cached()
def stats_overview():
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()

    per_source = Counter(p["source"] for p in postings)
    per_category = Counter((p.get("category") or "unlabeled") for p in postings)
    companies = {p["company"] for p in postings if p.get("company")}
    cities = {p["city"] for p in postings if p.get("city")}
    last_seen_values = [p["last_seen_at"] for p in postings if p.get("last_seen_at")]

    return {
        "total_postings": len(postings),
        "per_source": dict(per_source),
        "per_category": dict(per_category),
        "distinct_companies": len(companies),
        "distinct_cities": len(cities),
        "last_collection_at": max(last_seen_values) if last_seen_values else None,
        "skill_mention_total": len(mentions),
    }


# --------------------------------------------------------------------------
# GET /skills/top
# --------------------------------------------------------------------------

@app.get("/skills/top")
@cached()
def skills_top(
    category: str | None = None,
    include_soft: bool = False,
    limit: int = Query(default=25, ge=1, le=100),
):
    taxonomy = queries.get_taxonomy()
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    total_postings = len(postings)

    skill_postings = _skill_postings_map(mentions)

    rows = []
    for skill_key, spec in taxonomy["skills"].items():
        skill_cat = spec["category"]
        if category and skill_cat != category:
            continue
        if skill_cat == "soft" and not include_soft:
            continue
        count = len(skill_postings.get(skill_key, ()))
        if count == 0:
            continue
        rows.append({
            "skill": skill_key,
            "display": spec["display"],
            "category": skill_cat,
            "posting_count": count,
            "share_of_postings": round(count / total_postings, 4) if total_postings else 0.0,
        })

    rows.sort(key=lambda r: -r["posting_count"])
    rows = rows[:limit]
    return {"total_postings": total_postings, "count": len(rows), "skills": rows}


# --------------------------------------------------------------------------
# GET /postings/recent
# --------------------------------------------------------------------------

@app.get("/postings/recent")
@cached()
def postings_recent(limit: int = Query(default=20, ge=1, le=200), source: str | None = None):
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()

    posting_skills = defaultdict(list)
    for m in mentions:
        posting_skills[m["posting_id"]].append(m["skill"])

    filtered = [p for p in postings if source is None or p["source"] == source]
    filtered.sort(key=lambda p: p.get("first_seen_at") or "", reverse=True)
    filtered = filtered[:limit]

    return {
        "count": len(filtered),
        "postings": [
            {
                "title": p.get("title"),
                "company": p.get("company"),
                "city": p.get("city"),
                "posting_date": p.get("posting_date"),
                "source": p.get("source"),
                "detail_url": p.get("detail_url"),
                "skills": sorted(posting_skills.get(p["id"], [])),
            }
            for p in filtered
        ],
    }


# --------------------------------------------------------------------------
# GET /cities/breakdown
# --------------------------------------------------------------------------

@app.get("/cities/breakdown")
@cached()
def cities_breakdown(skill: str | None = None):
    taxonomy = queries.get_taxonomy()
    if skill:
        _require_known_skill(skill, taxonomy)

    postings = queries.get_postings()
    if skill:
        mentions = queries.get_skill_mentions()
        posting_ids = {m["posting_id"] for m in mentions if m["skill"] == skill}
        postings = [p for p in postings if p["id"] in posting_ids]

    buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "raw_variants": set()})
    no_city = 0
    for p in postings:
        raw_city = p.get("city")
        if not raw_city or not raw_city.strip():
            no_city += 1
            continue
        normalized = raw_city.strip().title()
        buckets[normalized]["count"] += 1
        buckets[normalized]["raw_variants"].add(raw_city)

    rows = [
        {"city": city, "count": b["count"], "raw_variants": sorted(b["raw_variants"])}
        for city, b in buckets.items()
    ]
    rows.sort(key=lambda r: -r["count"])

    return {
        "skill": skill,
        "total_postings": len(postings),
        "no_city_count": no_city,
        "cities": rows,
    }


# --------------------------------------------------------------------------
# GET /skills/{skill}/trend
# --------------------------------------------------------------------------

def _compute_skill_trend(skill: str, granularity: str) -> dict:
    taxonomy = queries.get_taxonomy()
    spec = _require_known_skill(skill, taxonomy)

    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    posting_ids_with_skill = {m["posting_id"] for m in mentions if m["skill"] == skill}

    dated = [
        (p["id"], _parse_ts(p["first_seen_at"]).date())
        for p in postings if p.get("first_seen_at")
    ]
    if not dated:
        return {"skill": skill, "display": spec["display"], "granularity": granularity, "buckets": []}

    dates = [d for _, d in dated]
    labels = _bucket_sequence(min(dates), max(dates), granularity)

    bucket_totals = Counter()
    bucket_skill_counts = Counter()
    for pid, d in dated:
        key = _bucket_key(d, granularity)
        bucket_totals[key] += 1
        if pid in posting_ids_with_skill:
            bucket_skill_counts[key] += 1

    series = []
    prev_value = None
    for label in labels:
        value = bucket_skill_counts.get(label, 0)
        series.append({
            "bucket": label,
            "postings_with_skill": value,
            "total_postings": bucket_totals.get(label, 0),
            # Previous bucket's value -- the "nothing changed" prediction,
            # here so any consumer comparing an actual forecast against a
            # baseline gets an honest, trivial-to-beat reference for free.
            "naive_baseline": prev_value,
        })
        prev_value = value

    return {
        "skill": skill,
        "display": spec["display"],
        "granularity": granularity,
        "buckets": series,
    }


@app.get("/skills/{skill}/trend")
@cached()
def skill_trend(skill: str, granularity: str = Query(default="week", pattern="^(day|week)$")):
    return _compute_skill_trend(skill, granularity)


# --------------------------------------------------------------------------
# GET /skills/compare
# --------------------------------------------------------------------------

@app.get("/skills/compare")
@cached()
def skills_compare(a: str, b: str, granularity: str = Query(default="week", pattern="^(day|week)$")):
    return {
        "a": _compute_skill_trend(a, granularity),
        "b": _compute_skill_trend(b, granularity),
    }


# --------------------------------------------------------------------------
# GET /salaries/summary
# --------------------------------------------------------------------------

@app.get("/salaries/summary")
@cached()
def salaries_summary(currency: str = "PKR"):
    postings = queries.get_postings()
    relevant = [p for p in postings if (p.get("currency") or "PKR") == currency]

    with_salary = [(p, _posting_salary_point(p)) for p in relevant]
    with_salary = [(p, v) for p, v in with_salary if v is not None]

    overall = _median_iqr([v for _, v in with_salary])
    overall["postings_with_salary"] = len(with_salary)
    overall["total_postings_this_currency"] = len(relevant)

    bands = defaultdict(list)
    for p, v in with_salary:
        bands[_experience_band(p.get("experience_raw"))].append(v)

    by_band = {
        band: _median_iqr(bands[band])
        for band in _EXPERIENCE_BAND_ORDER
        if band in bands
    }

    return {
        "currency": currency,
        "overall": overall,
        "by_experience_band": by_band,
        "note": (
            "Median and IQR only, never mean -- a handful of mismatched-currency "
            "or data-entry outliers (e.g. a USD salary stored as a raw number) "
            "would make a mean misleading. Salary point per posting is the "
            "midpoint of (salary_min, salary_max) when both are present, else "
            "whichever one is."
        ),
    }


# --------------------------------------------------------------------------
# GET /insights/live
#
# Extensible generator pattern: each _insight_* function takes
# (postings, mentions, taxonomy) and returns either None ("not noteworthy
# right now, don't show it") or a dict with headline/detail/value/
# computed_at plus an internal "score" used only for ranking -- score is
# stripped before the response goes out, since it's a ranking mechanism,
# not part of the documented insight shape. Adding a new insight later is
# just adding a function to _INSIGHT_GENERATORS.
# --------------------------------------------------------------------------

def _insight_top_mover(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(a) Skill with the biggest week-over-week posting-count change."""
    dated = [(p["id"], _parse_ts(p["first_seen_at"]).date()) for p in postings if p.get("first_seen_at")]
    if not dated:
        return None

    week_of = {pid: d - timedelta(days=d.weekday()) for pid, d in dated}
    distinct_weeks = sorted(set(week_of.values()))
    if len(distinct_weeks) < 2:
        return None  # not enough calendar weeks of data to compare yet

    last_week, prev_week = distinct_weeks[-1], distinct_weeks[-2]
    skill_postings = _skill_postings_map(mentions)

    best = None  # (skill_key, delta, cur, prev)
    for skill_key, pids in skill_postings.items():
        cur = sum(1 for pid in pids if week_of.get(pid) == last_week)
        prev = sum(1 for pid in pids if week_of.get(pid) == prev_week)
        delta = cur - prev
        if delta != 0 and (best is None or abs(delta) > abs(best[1])):
            best = (skill_key, delta, cur, prev)

    if best is None:
        return None

    skill_key, delta, cur, prev = best
    display = taxonomy["skills"][skill_key]["display"]
    direction = "up" if delta > 0 else "down"
    return {
        "headline": f"{display} postings moved {direction} by {abs(delta)} week-over-week",
        "detail": (
            f"{display} appeared in {cur} postings first seen the week of {last_week} "
            f"vs {prev} the week of {prev_week}."
        ),
        "value": {"skill": skill_key, "current_week": cur, "previous_week": prev, "delta": delta},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": abs(delta),
    }


def _insight_templated_share(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(b) Share of Rozee postings whose skill-tag block is an exact
    duplicate of another posting's -- evidence of templated listings."""
    rozee = [p for p in postings if p["source"] == "rozee"]
    if len(rozee) < 5:
        return None

    posting_tagsets = {}
    for p in rozee:
        sr = p.get("skills_raw")
        if isinstance(sr, list) and sr:
            posting_tagsets[p["id"]] = tuple(sorted(sr))

    if not posting_tagsets:
        return None

    groups = defaultdict(list)
    for pid, tagset in posting_tagsets.items():
        groups[tagset].append(pid)

    templated = sum(len(v) for v in groups.values() if len(v) > 1)
    total_tagged = len(posting_tagsets)
    share = templated / total_tagged
    if share == 0:
        return None

    return {
        "headline": f"{share * 100:.0f}% of Rozee postings share an identical skill-tag block with another posting",
        "detail": (
            f"{templated} of {total_tagged} Rozee postings with skill tags have the exact same tag set "
            f"as at least one other posting -- evidence of templated/duplicated listings from a small "
            f"number of employers, not independently authored tag choices."
        ),
        "value": {"templated_postings": templated, "total_tagged_postings": total_tagged, "share": round(share, 4)},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": share * 100,
    }


def _insight_posting_lifespan(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(c) Median (last_seen_at - first_seen_at) for postings that dropped
    out of the latest scrape run, overall and per city."""
    dated = [p for p in postings if p.get("scrape_run_id") and p.get("last_seen_at") and p.get("first_seen_at")]
    if not dated:
        return None

    latest_posting = max(dated, key=lambda p: p["last_seen_at"])
    latest_run_id = latest_posting["scrape_run_id"]

    dropped = [p for p in dated if p["scrape_run_id"] != latest_run_id]
    if len(dropped) < 5:
        return None

    def lifespan_days(p: dict) -> float:
        return (_parse_ts(p["last_seen_at"]) - _parse_ts(p["first_seen_at"])).total_seconds() / 86400

    overall_days = [lifespan_days(p) for p in dropped]
    overall_median = statistics.median(overall_days)

    by_city = defaultdict(list)
    for p in dropped:
        city = (p.get("city") or "").strip().title()
        if city:
            by_city[city].append(lifespan_days(p))
    city_medians = {c: round(statistics.median(v), 2) for c, v in by_city.items() if len(v) >= 3}

    return {
        "headline": f"Postings no longer seen in the latest run stayed live a median of {overall_median:.1f} days",
        "detail": (
            f"Based on {len(dropped)} postings present in an earlier run but not the latest one, "
            f"comparing first_seen_at to last_seen_at."
        ),
        "value": {
            "overall_median_days": round(overall_median, 2),
            "sample_size": len(dropped),
            "by_city_median_days": city_medians,
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": overall_median,
    }


def _insight_dominance_ratio(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(d) Largest posting-count ratio between two skills in the same
    (non-soft) category, e.g. SQL vs Tableau."""
    skill_postings = _skill_postings_map(mentions)

    by_category = defaultdict(list)
    for skill_key, spec in taxonomy["skills"].items():
        if spec["category"] == "soft":
            continue
        count = len(skill_postings.get(skill_key, ()))
        if count >= 5:  # minimum sample for the ratio to mean anything
            by_category[spec["category"]].append((skill_key, count))

    best = None  # (ratio, category, leader_key, leader_count, other_key, other_count)
    for cat, items in by_category.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: -x[1])
        leader_key, leader_count = items[0]
        for other_key, other_count in items[1:]:
            if other_count == 0:
                continue
            ratio = leader_count / other_count
            if best is None or ratio > best[0]:
                best = (ratio, cat, leader_key, leader_count, other_key, other_count)

    if best is None or best[0] < 1.5:
        return None

    ratio, cat, leader_key, leader_count, other_key, other_count = best
    leader_display = taxonomy["skills"][leader_key]["display"]
    other_display = taxonomy["skills"][other_key]["display"]

    return {
        "headline": f"{leader_display} outmentions {other_display} {ratio:.1f}x within {cat}",
        "detail": f"{leader_display} appears in {leader_count} postings vs {other_count} for {other_display}.",
        "value": {
            "category": cat,
            "leader": leader_key,
            "leader_count": leader_count,
            "runner_up": other_key,
            "runner_up_count": other_count,
            "ratio": round(ratio, 2),
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": ratio,
    }


def _insight_foreign_currency(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(e) Postings priced in a non-PKR currency, and what they are."""
    foreign = [p for p in postings if p.get("currency") and p["currency"] != "PKR"]
    if not foreign:
        return None

    breakdown = Counter(p["currency"] for p in foreign)
    examples = [
        {"title": p.get("title"), "currency": p["currency"], "detail_url": p.get("detail_url")}
        for p in foreign[:10]
    ]

    return {
        "headline": f"{len(foreign)} postings are priced in a non-PKR currency",
        "detail": (
            f"Breakdown: {dict(breakdown)}. These would silently skew salary aggregates "
            f"if not filtered by currency."
        ),
        "value": {"count": len(foreign), "by_currency": dict(breakdown), "examples": examples},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": len(foreign),
    }


def _insight_zero_technical(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(f) Share of postings mentioning zero technical (non-soft) skills."""
    if not postings:
        return None

    technical_posting_ids = {m["posting_id"] for m in mentions if m["category"] != "soft"}
    zero_technical = sum(1 for p in postings if p["id"] not in technical_posting_ids)
    share = zero_technical / len(postings)
    if share == 0:
        return None

    return {
        "headline": f"{share * 100:.0f}% of postings mention zero technical skills from the taxonomy",
        "detail": (
            f"{zero_technical} of {len(postings)} postings have no taxonomy-recognized technical "
            f"(non-soft) skill mention at all."
        ),
        "value": {"zero_technical_count": zero_technical, "total_postings": len(postings), "share": round(share, 4)},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": share * 100,
    }


_INSIGHT_GENERATORS = [
    _insight_top_mover,
    _insight_templated_share,
    _insight_posting_lifespan,
    _insight_dominance_ratio,
    _insight_foreign_currency,
    _insight_zero_technical,
]


@app.get("/insights/live")
@cached()
def insights_live():
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    taxonomy = queries.get_taxonomy()

    results = []
    for gen in _INSIGHT_GENERATORS:
        try:
            result = gen(postings, mentions, taxonomy)
        except Exception as exc:
            logger.warning(f"Insight generator {gen.__name__} failed: {exc}")
            continue
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: -r["score"])
    top = results[:6]
    for r in top:
        r.pop("score", None)

    return {"count": len(top), "insights": top}


# --------------------------------------------------------------------------
# GET /system/health
# --------------------------------------------------------------------------

@app.get("/system/health")
@cached()
def system_health():
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    now = datetime.now(timezone.utc)

    last_run_per_source: dict[str, str] = {}
    for p in postings:
        src, last_seen = p["source"], p.get("last_seen_at")
        if last_seen and (src not in last_run_per_source or last_seen > last_run_per_source[src]):
            last_run_per_source[src] = last_seen

    added_24h = sum(
        1 for p in postings
        if p.get("first_seen_at") and _parse_ts(p["first_seen_at"]) >= now - timedelta(hours=24)
    )
    added_7d = sum(
        1 for p in postings
        if p.get("first_seen_at") and _parse_ts(p["first_seen_at"]) >= now - timedelta(days=7)
    )

    mustakbil = [p for p in postings if p["source"] == "mustakbil"]
    enriched = sum(1 for p in mustakbil if not _skills_raw_empty(p.get("skills_raw")))
    enrichment_coverage = round(enriched / len(mustakbil), 4) if mustakbil else None

    posting_ids_with_mention = {m["posting_id"] for m in mentions}
    extraction_coverage = round(len(posting_ids_with_mention) / len(postings), 4) if postings else None

    last_seen_values = [p["last_seen_at"] for p in postings if p.get("last_seen_at")]
    if last_seen_values:
        most_recent = max(_parse_ts(v) for v in last_seen_values)
        freshness_hours = round((now - most_recent).total_seconds() / 3600, 2)
    else:
        freshness_hours = None

    return {
        "last_successful_run_per_source": last_run_per_source,
        "postings_added_24h": added_24h,
        "postings_added_7d": added_7d,
        "enrichment_coverage_mustakbil": enrichment_coverage,
        "extraction_coverage": extraction_coverage,
        "data_freshness_hours": freshness_hours,
        "table_sizes": {
            "postings": len(postings),
            "skill_mentions": len(mentions),
        },
        "checked_at": now.isoformat(),
    }


# --------------------------------------------------------------------------
# GET / -- minimal liveness/index, not part of the spec but near-zero cost
# --------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "rukhwise-api",
        "endpoints": [
            "/stats/overview", "/skills/top", "/skills/{skill}/trend", "/skills/compare",
            "/postings/recent", "/cities/breakdown", "/salaries/summary",
            "/insights/live", "/system/health",
        ],
    }
