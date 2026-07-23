"""Rukhwise read API. Read-only, anon-key Supabase access -- see queries.py.

All response shaping/aggregation happens here; queries.py never returns
anything except raw rows. Every route is wrapped in the in-process TTL
cache (cache.py) since the underlying data changes at most daily.
"""

from __future__ import annotations

import itertools
import logging
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Makes `import queries` / `from cache import cached` below resolve
# regardless of how this module was invoked -- `cd api && uvicorn main:app`
# (cwd already on sys.path) or `uvicorn api.main:app` from the repo root
# (cwd is the repo root, this file's own directory is not otherwise on
# sys.path). Must run before the local imports just below.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import queries
from cache import cached

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rukhwise_api")

app = FastAPI(title="Rukhwise API", version="0.1.0")

_allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
ALLOWED_ORIGINS = [origin.strip() for origin in _allowed_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],  # read-only service, except /coverage's
    # POST -- it still performs no writes, POST here is purely because the
    # request needs a body (a skill list) too large/structured for a query
    # string.
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


def _posting_skills_map(
    mentions: list[dict], taxonomy: dict, include_soft: bool = False
) -> dict[str, set[str]]:
    """posting_id -> set of skill keys mentioned, restricted to skills that
    still exist in the current taxonomy (guards against stale mention rows
    from a retired skill) and optionally excluding soft-category skills."""
    skills_meta = taxonomy["skills"]
    out: dict[str, set[str]] = defaultdict(set)
    for row in mentions:
        spec = skills_meta.get(row["skill"])
        if spec is None:
            continue
        if spec["category"] == "soft" and not include_soft:
            continue
        out[row["posting_id"]].add(row["skill"])
    return out


def _normalize_company_key(name: str) -> str:
    """Trim + collapse internal whitespace + case-fold, purely for grouping.
    Display always uses the most common raw form seen (_company_summary),
    never this key."""
    return " ".join(name.split()).casefold()


def _skill_company_map(postings: list[dict], mentions: list[dict]) -> dict[str, set[str]]:
    """skill -> set of normalized company keys among postings that mention
    it -- the bulk-poster-resistant companion to plain posting_count: a
    single high-volume employer repeating the same skill list inflates
    posting_count but not company_count."""
    posting_company: dict[str, str] = {}
    for p in postings:
        if p.get("company"):
            posting_company[p["id"]] = _normalize_company_key(p["company"])
    out: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        company_key = posting_company.get(m["posting_id"])
        if company_key:
            out[m["skill"]].add(company_key)
    return out


_BULK_THRESHOLD = 0.25


def _bulk_company_keys(postings: list[dict]) -> set[str]:
    """Company (normalized) keys holding > 25% of all postings -- "bulk
    posters" whose volume can single-handedly manufacture skill/pairing
    signal that has nothing to do with broader market demand. Always
    computed against the full, unfiltered posting list passed in, so the
    threshold doesn't drift if called after some other filtering."""
    total = len(postings)
    if total == 0:
        return set()
    counts: dict[str, int] = defaultdict(int)
    for p in postings:
        if p.get("company"):
            counts[_normalize_company_key(p["company"])] += 1
    return {key for key, count in counts.items() if count / total > _BULK_THRESHOLD}


def _apply_bulk_exclusion(
    postings: list[dict], mentions: list[dict], exclude_bulk: bool
) -> tuple[list[dict], list[dict]]:
    """When exclude_bulk, drops every posting (and its mentions) from any
    bulk poster (see _bulk_company_keys) -- a single high-volume employer
    can otherwise manufacture skill/pairing signal that says more about
    that one employer's template than about market demand. Bulk-share is
    always computed against the full corpus passed in here, before any
    exclusion, so the 25% threshold is stable."""
    if not exclude_bulk:
        return postings, mentions
    bulk_keys = _bulk_company_keys(postings)
    if not bulk_keys:
        return postings, mentions
    filtered_postings = [
        p for p in postings
        if not p.get("company") or _normalize_company_key(p["company"]) not in bulk_keys
    ]
    valid_ids = {p["id"] for p in filtered_postings}
    filtered_mentions = [m for m in mentions if m["posting_id"] in valid_ids]
    return filtered_postings, filtered_mentions


def _bucket_is_partial(label: str, granularity: str, today: date) -> bool:
    """True for the single bucket, if any, representing a period still in
    progress (today, or this week) -- its count is an undercount by
    construction and must never be visually compared against a complete
    bucket in a chart."""
    bucket_date = date.fromisoformat(label)
    if granularity == "day":
        return bucket_date == today
    current_week_start = today - timedelta(days=today.weekday())
    return bucket_date == current_week_start


def _is_templated_share(subset: list[dict]) -> dict:
    """Same exact-skill-tagset-duplicate detection as
    _insight_templated_share below, generalized to an arbitrary posting
    subset (here, one company's postings) rather than hardcoded to Rozee."""
    tagsets: dict[str, tuple] = {}
    for p in subset:
        sr = p.get("skills_raw")
        if isinstance(sr, list) and sr:
            tagsets[p["id"]] = tuple(sorted(sr))

    if not tagsets:
        return {"tagged_count": 0, "templated_count": 0, "share": None}

    groups: dict[tuple, list[str]] = defaultdict(list)
    for pid, tagset in tagsets.items():
        groups[tagset].append(pid)

    templated = sum(len(v) for v in groups.values() if len(v) > 1)
    return {
        "tagged_count": len(tagsets),
        "templated_count": templated,
        "share": round(templated / len(tagsets), 4),
    }


# --------------------------------------------------------------------------
# Skill co-occurrence -- shared by /skills/cooccurrence, /skills/{skill}/
# companions, and two of the insight generators near the bottom of this file.
# --------------------------------------------------------------------------

_MIN_JOINT_COUNT = 5  # pairs below this are noise, not signal


def _compute_skill_cooccurrence(
    mentions: list[dict], taxonomy: dict, total_postings: int, include_soft: bool = False
) -> list[dict]:
    """Every skill pair that co-occurs on >= _MIN_JOINT_COUNT postings, with
    joint count, both conditional probabilities, and lift.

    P(skill) is defined against `total_postings` (every tracked posting,
    matching the /skills/top convention) rather than just postings that
    mention any skill at all -- so these probabilities are directly
    comparable to share_of_postings elsewhere in the API. Lift = P(A and B)
    / (P(A) * P(B)): 1.0 means no relationship beyond chance, higher means
    the pair is demanded together more than base rates would predict.
    """
    posting_skills = _posting_skills_map(mentions, taxonomy, include_soft)
    skill_postings = _skill_postings_map(
        [
            m
            for m in mentions
            if (spec := taxonomy["skills"].get(m["skill"])) is not None
            and (include_soft or spec["category"] != "soft")
        ]
    )

    joint_counts: Counter[tuple[str, str]] = Counter()
    for skills in posting_skills.values():
        for a, b in itertools.combinations(sorted(skills), 2):
            joint_counts[(a, b)] += 1

    pairs = []
    for (a, b), joint in joint_counts.items():
        if joint < _MIN_JOINT_COUNT:
            continue
        count_a = len(skill_postings.get(a, ()))
        count_b = len(skill_postings.get(b, ()))
        if count_a == 0 or count_b == 0 or total_postings == 0:
            continue
        p_a = count_a / total_postings
        p_b = count_b / total_postings
        p_joint = joint / total_postings
        lift = p_joint / (p_a * p_b)
        pairs.append({
            "skill_a": a,
            "display_a": taxonomy["skills"][a]["display"],
            "skill_b": b,
            "display_b": taxonomy["skills"][b]["display"],
            "joint_count": joint,
            "count_a": count_a,
            "count_b": count_b,
            "p_b_given_a": round(joint / count_a, 4),
            "p_a_given_b": round(joint / count_b, 4),
            "lift": round(lift, 3),
        })
    return pairs


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

    company_counts: dict[str, int] = defaultdict(int)
    for p in postings:
        if p.get("company"):
            company_counts[_normalize_company_key(p["company"])] += 1
    top_company_count = max(company_counts.values(), default=0)
    top_company_share = round(top_company_count / len(postings), 4) if postings else 0.0

    return {
        "total_postings": len(postings),
        "per_source": dict(per_source),
        "per_category": dict(per_category),
        "distinct_companies": len(companies),
        "distinct_cities": len(cities),
        "last_collection_at": max(last_seen_values) if last_seen_values else None,
        "skill_mention_total": len(mentions),
        # Concentration disclosure -- the single highest-volume employer's
        # share of all tracked postings. A single bulk poster can dominate
        # skill/pairing signal; this makes that risk visible at the top
        # level rather than requiring a trip to /companies/top to notice it.
        "top_company_share": top_company_share,
    }


# --------------------------------------------------------------------------
# GET /skills/top
# --------------------------------------------------------------------------

@app.get("/skills/top")
@cached()
def skills_top(
    category: str | None = None,
    include_soft: bool = False,
    exclude_bulk: bool = False,
    limit: int = Query(default=25, ge=1, le=100),
):
    taxonomy = queries.get_taxonomy()
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    postings, mentions = _apply_bulk_exclusion(postings, mentions, exclude_bulk)
    total_postings = len(postings)

    skill_postings = _skill_postings_map(mentions)
    skill_companies = _skill_company_map(postings, mentions)

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
            # Distinct companies demanding this skill -- the bulk-poster-
            # resistant companion to posting_count: a single repeat-poster
            # inflates the latter but not this.
            "company_count": len(skill_companies.get(skill_key, ())),
            "share_of_postings": round(count / total_postings, 4) if total_postings else 0.0,
        })

    rows.sort(key=lambda r: -r["posting_count"])
    rows = rows[:limit]
    return {
        "total_postings": total_postings,
        "exclude_bulk": exclude_bulk,
        "count": len(rows),
        "skills": rows,
    }


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
    company_count = len(_skill_company_map(postings, mentions).get(skill, ()))

    dated = [
        (p["id"], _parse_ts(p["first_seen_at"]).date())
        for p in postings if p.get("first_seen_at")
    ]
    if not dated:
        return {
            "skill": skill,
            "display": spec["display"],
            "granularity": granularity,
            "company_count": company_count,
            "buckets": [],
        }

    dates = [d for _, d in dated]
    labels = _bucket_sequence(min(dates), max(dates), granularity)
    today = datetime.now(timezone.utc).date()

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
            # True only for a bucket still in progress (this week / today).
            # Its count is an undercount by construction -- charts must
            # never plot it as if it were comparable to a complete bucket.
            "is_partial": _bucket_is_partial(label, granularity, today),
        })
        prev_value = value

    return {
        "skill": skill,
        "display": spec["display"],
        "granularity": granularity,
        "company_count": company_count,
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
# GET /skills/cooccurrence, GET /skills/{skill}/companions
# --------------------------------------------------------------------------

@app.get("/skills/cooccurrence")
@cached()
def skills_cooccurrence(
    include_soft: bool = False,
    exclude_bulk: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    taxonomy = queries.get_taxonomy()
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    postings, mentions = _apply_bulk_exclusion(postings, mentions, exclude_bulk)

    pairs = _compute_skill_cooccurrence(mentions, taxonomy, len(postings), include_soft)
    pairs.sort(key=lambda p: -p["joint_count"])
    pairs = pairs[:limit]

    return {
        "total_postings": len(postings),
        "min_joint_count": _MIN_JOINT_COUNT,
        "exclude_bulk": exclude_bulk,
        "count": len(pairs),
        "pairs": pairs,
    }


@app.get("/skills/{skill}/companions")
@cached()
def skill_companions(
    skill: str,
    include_soft: bool = False,
    exclude_bulk: bool = False,
    limit: int = Query(default=10, ge=1, le=50),
):
    taxonomy = queries.get_taxonomy()
    _require_known_skill(skill, taxonomy)
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    postings, mentions = _apply_bulk_exclusion(postings, mentions, exclude_bulk)

    pairs = _compute_skill_cooccurrence(mentions, taxonomy, len(postings), include_soft)
    companions = []
    for p in pairs:
        if p["skill_a"] == skill:
            companions.append({
                "skill": p["skill_b"],
                "display": p["display_b"],
                "joint_count": p["joint_count"],
                "p_companion_given_skill": p["p_b_given_a"],
                "p_skill_given_companion": p["p_a_given_b"],
                "lift": p["lift"],
            })
        elif p["skill_b"] == skill:
            companions.append({
                "skill": p["skill_a"],
                "display": p["display_a"],
                "joint_count": p["joint_count"],
                "p_companion_given_skill": p["p_a_given_b"],
                "p_skill_given_companion": p["p_b_given_a"],
                "lift": p["lift"],
            })

    # "Most likely to be demanded alongside it" is literally P(companion |
    # skill) -- ranked by that, not by lift, so a ubiquitous but genuinely
    # frequent companion outranks a rarer but more "surprising" one.
    companions.sort(key=lambda c: -c["p_companion_given_skill"])
    companions = companions[:limit]

    return {
        "skill": skill,
        "display": taxonomy["skills"][skill]["display"],
        "exclude_bulk": exclude_bulk,
        "count": len(companions),
        "companions": companions,
    }


# --------------------------------------------------------------------------
# GET /salaries/summary
# --------------------------------------------------------------------------

@app.get("/salaries/summary")
@cached()
def salaries_summary(currency: str = "PKR", skill: str | None = None):
    taxonomy = queries.get_taxonomy()
    if skill:
        _require_known_skill(skill, taxonomy)

    postings = queries.get_postings()
    relevant = [p for p in postings if (p.get("currency") or "PKR") == currency]

    if skill:
        mentions = queries.get_skill_mentions()
        posting_ids = {m["posting_id"] for m in mentions if m["skill"] == skill}
        relevant = [p for p in relevant if p["id"] in posting_ids]

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
        "skill": skill,
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
# POST /coverage
#
# Redesigned around the question a job seeker actually asks -- "how many
# jobs am I a strong candidate for," an absolute count -- rather than the
# economist's-eye-view percentage the first version led with. Percentages
# still exist, demoted to a `stats` footnote.
# --------------------------------------------------------------------------

class CoverageRequest(BaseModel):
    skills: list[str]


_STRONG_MATCH_THRESHOLD = 0.7
_STRONG_MATCH_POSTINGS_SHOWN = 50


@app.post("/coverage")
@cached()
def coverage(payload: CoverageRequest, exclude_bulk: bool = False):
    taxonomy = queries.get_taxonomy()
    skills_meta = taxonomy["skills"]

    unknown = sorted({s for s in payload.skills if s not in skills_meta})
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown skills: {unknown}")

    # A posting is never considered to require a soft skill in this
    # computation (see technical_postings below), so soft entries in the
    # input are silently excluded from the working set rather than rejected
    # -- they're valid taxonomy skills, just not ones "match strength" is
    # about.
    user_skills = {s for s in payload.skills if skills_meta[s]["category"] != "soft"}
    ignored_soft = sorted(set(payload.skills) - user_skills)

    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    postings, mentions = _apply_bulk_exclusion(postings, mentions, exclude_bulk)
    postings_by_id = {p["id"]: p for p in postings}

    posting_skills = _posting_skills_map(mentions, taxonomy, include_soft=False)

    # Denominator: postings with at least one recognized technical skill
    # mention. A posting with zero technical mentions has no match-strength
    # question to answer -- it would trivially read as "100% covered" by an
    # empty requirement set, which is meaningless, so it's excluded rather
    # than counted as a free win.
    technical_postings = {pid: skills for pid, skills in posting_skills.items() if skills}
    total = len(technical_postings)

    strong_match_ids: list[str] = []
    full_matched = 0
    delta_counter: Counter[str] = Counter()

    for pid, skills in technical_postings.items():
        covered = skills & user_skills
        missing = skills - user_skills
        current_fraction = len(covered) / len(skills)

        if not missing:
            full_matched += 1
        if current_fraction >= _STRONG_MATCH_THRESHOLD:
            strong_match_ids.append(pid)
            continue  # already strong -- not eligible for "unlock" credit

        # Would learning exactly one more skill push this posting from
        # below-threshold to a strong match? Any one of its missing skills
        # raises covered-by-exactly-one, so a posting sitting right at the
        # boundary credits every one of its missing skills, not just one --
        # each of them really would, on its own, cross the line.
        new_fraction = (len(covered) + 1) / len(skills)
        if new_fraction >= _STRONG_MATCH_THRESHOLD:
            for skill_key in missing:
                delta_counter[skill_key] += 1

    strong_match_ids.sort(key=lambda pid: postings_by_id[pid].get("first_seen_at") or "", reverse=True)
    strong_count = len(strong_match_ids)

    strong_match_postings = [
        {
            "title": postings_by_id[pid].get("title"),
            "company": postings_by_id[pid].get("company"),
            "city": postings_by_id[pid].get("city"),
            "detail_url": postings_by_id[pid].get("detail_url"),
        }
        for pid in strong_match_ids[:_STRONG_MATCH_POSTINGS_SHOWN]
    ]

    delta_ranking = [
        {
            "skill": skill_key,
            "display": skills_meta[skill_key]["display"],
            "additional_strong_matches_if_added": count,
        }
        for skill_key, count in delta_counter.most_common(10)
    ]

    strong_pct = round(strong_count / total * 100, 1) if total else 0.0
    full_pct = round(full_matched / total * 100, 1) if total else 0.0

    return {
        "input_skills": sorted(user_skills),
        "ignored_soft_skills": ignored_soft,
        "exclude_bulk": exclude_bulk,
        "total_postings_considered": total,
        "strong_matches": {
            "count": strong_count,
            "postings_shown": len(strong_match_postings),
            "postings": strong_match_postings,
        },
        "full_matches": {"count": full_matched},
        "delta_ranking": delta_ranking,
        "stats": {
            "strong_match_percent": strong_pct,
            "full_match_percent": full_pct,
        },
        "note": (
            "A 'strong match' is a posting, among those with at least one "
            "taxonomy-recognized technical (non-soft) skill mention, where "
            "your skill set covers 70% or more of what it asks for -- this "
            "is the headline count above, and is meant to answer 'how many "
            "jobs am I a strong candidate for,' not an economy-wide share. "
            "A 'full match' additionally requires covering every technical "
            "skill the posting mentions, no exceptions; every full match is "
            "also a strong match. Up to 50 strong-match postings are listed, "
            "most recent first, out of the true count above. Postings with "
            "zero recognized technical skill mentions are excluded from the "
            "denominator entirely -- they have no match-strength question "
            "to answer. The delta ranking simulates learning exactly one "
            "more skill at a time: for each candidate not already in your "
            "set, it counts postings currently below the 70% threshold that "
            "would cross it the moment you learned that one skill. "
            "Percentages, where shown, are in `stats` and are secondary to "
            "the counts above."
        ),
    }


# --------------------------------------------------------------------------
# GET /companies/top, GET /companies/{name}
# --------------------------------------------------------------------------

def _company_summary(
    postings_subset: list[dict], mentions: list[dict], taxonomy: dict, include_soft: bool
) -> dict:
    raw_names = Counter(p["company"] for p in postings_subset if p.get("company"))
    display_name = raw_names.most_common(1)[0][0] if raw_names else "Unknown"

    posting_ids = {p["id"] for p in postings_subset}
    cities = sorted({
        p["city"].strip().title() for p in postings_subset if p.get("city") and p["city"].strip()
    })

    relevant_mentions = [m for m in mentions if m["posting_id"] in posting_ids]
    skill_counts: Counter[str] = Counter()
    skill_display: dict[str, str] = {}
    for m in relevant_mentions:
        spec = taxonomy["skills"].get(m["skill"])
        if spec is None:
            continue
        if spec["category"] == "soft" and not include_soft:
            continue
        skill_counts[m["skill"]] += 1
        skill_display[m["skill"]] = spec["display"]

    top_skills = [
        {"skill": s, "display": skill_display[s], "count": c}
        for s, c in skill_counts.most_common(5)
    ]

    pkr_postings = [p for p in postings_subset if (p.get("currency") or "PKR") == "PKR"]
    salary_values = [v for v in (_posting_salary_point(p) for p in pkr_postings) if v is not None]
    salary_pkr = _median_iqr(salary_values)

    return {
        "company": display_name,
        "posting_count": len(postings_subset),
        "cities": cities,
        "top_skills": top_skills,
        "salary_pkr": salary_pkr,
        "templated": _is_templated_share(postings_subset),
    }


@app.get("/companies/top")
@cached()
def companies_top(include_soft: bool = False, limit: int = Query(default=25, ge=1, le=100)):
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    taxonomy = queries.get_taxonomy()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for p in postings:
        if p.get("company"):
            grouped[_normalize_company_key(p["company"])].append(p)

    summaries = [
        _company_summary(subset, mentions, taxonomy, include_soft) for subset in grouped.values()
    ]
    summaries.sort(key=lambda c: -c["posting_count"])
    summaries = summaries[:limit]

    return {"count": len(summaries), "companies": summaries}


@app.get("/companies/{name:path}")
@cached()
def company_detail(
    name: str, include_soft: bool = False, recent_limit: int = Query(default=20, ge=1, le=100)
):
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    taxonomy = queries.get_taxonomy()

    key = _normalize_company_key(name)
    subset = [p for p in postings if p.get("company") and _normalize_company_key(p["company"]) == key]
    if not subset:
        raise HTTPException(status_code=404, detail=f"No postings found for company '{name}'")

    summary = _company_summary(subset, mentions, taxonomy, include_soft)

    posting_skills = defaultdict(list)
    for m in mentions:
        posting_skills[m["posting_id"]].append(m["skill"])

    recent = sorted(subset, key=lambda p: p.get("first_seen_at") or "", reverse=True)[:recent_limit]
    summary["recent_postings"] = [
        {
            "title": p.get("title"),
            "city": p.get("city"),
            "posting_date": p.get("posting_date"),
            "source": p.get("source"),
            "detail_url": p.get("detail_url"),
            "skills": sorted(posting_skills.get(p["id"], [])),
        }
        for p in recent
    ]
    return summary


# --------------------------------------------------------------------------
# GET /postings/foreign-currency
# --------------------------------------------------------------------------

@app.get("/postings/foreign-currency")
@cached()
def postings_foreign_currency():
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    taxonomy = queries.get_taxonomy()

    foreign = [p for p in postings if p.get("currency") and p["currency"] != "PKR"]
    posting_skills = _posting_skills_map(mentions, taxonomy, include_soft=False)

    breakout: Counter[str] = Counter()
    rows = []
    for p in foreign:
        skills = sorted(posting_skills.get(p["id"], ()))
        for s in skills:
            breakout[s] += 1
        rows.append({
            "currency": p["currency"],
            "salary_min": p.get("salary_min"),
            "salary_max": p.get("salary_max"),
            "title": p.get("title"),
            "company": p.get("company"),
            "city": p.get("city"),
            "detail_url": p.get("detail_url"),
            "skills": skills,
        })

    breakout_stack = [
        {"skill": s, "display": taxonomy["skills"][s]["display"], "count": c}
        for s, c in breakout.most_common()
    ]

    return {
        "count": len(rows),
        "postings": rows,
        "breakout_stack": breakout_stack,
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
    """(a) Skill with the biggest week-over-week posting-count change.

    Guarded against the in-progress week: only ever compares the two most
    recently COMPLETE calendar weeks (never the still-forming current week
    against a full one), and refuses to activate at all until at least 3
    complete weeks of history exist -- so the "previous" week in the
    comparison is never itself the very first, possibly-partial week of
    collection.

    Audit note: every other generator in this file was checked for the same
    partial-bucket vulnerability. None of the others compare across
    calendar-time buckets the way this one does -- they're all either
    static snapshots (dominance ratio, foreign currency, zero-technical,
    strongest pairing, top companion, top company) or scrape-run membership
    checks (posting lifespan), not week-over-week deltas, so this guard is
    the only one needed.
    """
    dated = [(p["id"], _parse_ts(p["first_seen_at"]).date()) for p in postings if p.get("first_seen_at")]
    if not dated:
        return None

    today = datetime.now(timezone.utc).date()
    current_week_start = today - timedelta(days=today.weekday())

    week_of = {pid: d - timedelta(days=d.weekday()) for pid, d in dated}
    complete_weeks = sorted({w for w in week_of.values() if w < current_week_start})
    if len(complete_weeks) < 3:
        return None  # not enough complete weeks of history to trust a comparison

    last_week, prev_week = complete_weeks[-1], complete_weeks[-2]
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
            f"vs {prev} the week of {prev_week} -- both complete calendar weeks."
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


def _insight_strongest_pairing(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(g) Highest-lift skill pair this period -- the two skills demanded
    together far more than their individual base rates would predict.

    Phrasing guard: lift explodes for rare skills (two skills each mentioned
    a handful of times can produce a 50x+ lift from pure small-sample noise),
    so below a joint_count of 15 the headline leads with the plain count,
    never the multiplier -- the multiplier is still reported in `value` for
    anyone who wants it, just not foregrounded in prose."""
    pairs = _compute_skill_cooccurrence(mentions, taxonomy, len(postings), include_soft=False)
    if not pairs:
        return None

    best = max(pairs, key=lambda p: p["lift"])
    if best["joint_count"] < 15:
        headline = (
            f"{best['display_a']} and {best['display_b']} appear together in "
            f"{best['joint_count']} postings so far"
        )
        detail = (
            f"A small but consistent pairing -- {best['lift']:.1f}x above chance -- though the "
            f"sample ({best['joint_count']} postings) is still thin enough to watch, not trust yet."
        )
    else:
        headline = f"{best['display_a']} and {best['display_b']} are the strongest skill pairing right now"
        detail = (
            f"{best['joint_count']} postings mention both -- {best['lift']:.1f}x more often than "
            f"chance alone would predict if the two skills were demanded independently."
        )

    return {
        "headline": headline,
        "detail": detail,
        "value": {
            "skill_a": best["skill_a"],
            "skill_b": best["skill_b"],
            "joint_count": best["joint_count"],
            "lift": best["lift"],
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": best["lift"],
    }


def _insight_top_skill_companion(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(h) Most demanded companion of the single most-mentioned technical
    skill -- "employers asking for X also ask for Y", applied to whichever
    X currently leads the market."""
    technical_mentions = [
        m for m in mentions
        if (spec := taxonomy["skills"].get(m["skill"])) is not None and spec["category"] != "soft"
    ]
    skill_postings = _skill_postings_map(technical_mentions)
    if not skill_postings:
        return None

    top_skill = max(skill_postings, key=lambda s: len(skill_postings[s]))
    pairs = _compute_skill_cooccurrence(mentions, taxonomy, len(postings), include_soft=False)

    companions = []
    for p in pairs:
        if p["skill_a"] == top_skill:
            companions.append((p["skill_b"], p["display_b"], p["p_b_given_a"], p["joint_count"]))
        elif p["skill_b"] == top_skill:
            companions.append((p["skill_a"], p["display_a"], p["p_a_given_b"], p["joint_count"]))
    if not companions:
        return None

    companion_skill, companion_display, p_given, joint = max(companions, key=lambda c: c[2])
    top_display = taxonomy["skills"][top_skill]["display"]

    return {
        "headline": f"Employers asking for {top_display} most often also ask for {companion_display}",
        "detail": (
            f"{round(p_given * 100)}% of postings mentioning {top_display} also mention "
            f"{companion_display} ({joint} postings)."
        ),
        "value": {
            "skill": top_skill,
            "companion": companion_skill,
            "p_companion_given_skill": p_given,
            "joint_count": joint,
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": p_given * 100,
    }


def _insight_top_company(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(i) Top hiring company by distinct posting count."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for p in postings:
        if p.get("company"):
            grouped[_normalize_company_key(p["company"])].append(p)
    if not grouped:
        return None

    _, subset = max(grouped.items(), key=lambda kv: len(kv[1]))
    if len(subset) < 3:
        return None  # a "top company" with one or two postings isn't noteworthy

    display_name = Counter(p["company"] for p in subset).most_common(1)[0][0]
    return {
        "headline": f"{display_name} is the most active hirer tracked right now",
        "detail": f"{len(subset)} distinct postings from {display_name} across the tracked window.",
        "value": {"company": display_name, "posting_count": len(subset)},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "score": len(subset),
    }


_INSIGHT_GENERATORS = [
    _insight_top_mover,
    _insight_templated_share,
    _insight_posting_lifespan,
    _insight_dominance_ratio,
    _insight_foreign_currency,
    _insight_strongest_pairing,
    _insight_top_skill_companion,
    _insight_top_company,
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

    # first_seen_at is always "when we collected it", not "when it was
    # posted" -- accurate as a proxy for posting recency for Mustakbil/
    # Indeed (collected same-day) but NOT for LinkedIn, where a listing
    # can be weeks old when first_seen_at is stamped today (see
    # jobspy_source.py's staleness handling). These two counts measure
    # collection/ingestion activity across all sources, not "how many new
    # jobs appeared" -- forecast.py's weekly actuals are the metric that
    # actually needs posting-recency accuracy, which is why it's scoped to
    # AUTOMATED_SOURCES and excludes LinkedIn entirely.
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
# GET /forecasts/pending, GET /forecasts/accuracy
# --------------------------------------------------------------------------

def _forecast_display(target_type: str, target_key: str, taxonomy: dict) -> str:
    if target_type == "volume":
        return "All postings (Mustakbil)"
    spec = taxonomy["skills"].get(target_key)
    return spec["display"] if spec else target_key


@app.get("/forecasts/pending")
@cached()
def forecasts_pending():
    """Ungraded forecasts for the current/future weeks -- whatever's been
    logged by --predict but hasn't had its target week complete yet."""
    taxonomy = queries.get_taxonomy()
    forecasts = queries.get_forecasts()
    pending = [f for f in forecasts if f.get("graded_at") is None]
    pending.sort(key=lambda f: (f["target_week_start"], f["target_type"], f["target_key"]))

    rows = [
        {
            "target_type": f["target_type"],
            "target_key": f["target_key"],
            "display": _forecast_display(f["target_type"], f["target_key"], taxonomy),
            "target_week_start": f["target_week_start"],
            "model_version": f["model_version"],
            "predicted": f["predicted"],
            "interval_low": f["interval_low"],
            "interval_high": f["interval_high"],
            "baseline_predicted": f["baseline_predicted"],
            "created_at": f["created_at"],
            "run_id": f["run_id"],
        }
        for f in pending
    ]

    return {"count": len(rows), "forecasts": rows}


@app.get("/forecasts/accuracy")
@cached()
def forecasts_accuracy():
    """Every graded forecast, plus a summary: overall MAE, beat-baseline
    rate overall and by target_type, and the graded count. MAPE is
    deliberately not in the summary (a handful of low-count skill targets
    would make it wildly noisy) -- per-row pct_error is still included
    for rows where actual > 0, so the detail is there without the
    misleading headline aggregate."""
    taxonomy = queries.get_taxonomy()
    forecasts = queries.get_forecasts()
    graded = [f for f in forecasts if f.get("graded_at") is not None]
    graded.sort(key=lambda f: (f["target_week_start"], f["target_type"], f["target_key"]))

    rows = []
    for f in graded:
        actual = f["actual"]
        pct_error = round(f["abs_error"] / actual * 100, 2) if actual else None
        rows.append({
            "target_type": f["target_type"],
            "target_key": f["target_key"],
            "display": _forecast_display(f["target_type"], f["target_key"], taxonomy),
            "target_week_start": f["target_week_start"],
            "model_version": f["model_version"],
            "predicted": f["predicted"],
            "baseline_predicted": f["baseline_predicted"],
            "actual": actual,
            "abs_error": f["abs_error"],
            "baseline_abs_error": f["baseline_abs_error"],
            "beat_baseline": f["beat_baseline"],
            "pct_error": pct_error,
            "graded_at": f["graded_at"],
        })

    count_graded = len(graded)
    mae_overall = round(statistics.fmean(f["abs_error"] for f in graded), 4) if graded else None
    beat_overall = (
        round(sum(1 for f in graded if f["beat_baseline"]) / count_graded, 4) if graded else None
    )

    by_type: dict[str, list[dict]] = defaultdict(list)
    for f in graded:
        by_type[f["target_type"]].append(f)
    beat_by_type = {
        t: round(sum(1 for f in fs if f["beat_baseline"]) / len(fs), 4)
        for t, fs in by_type.items()
    }

    return {
        "forecasts": rows,
        "summary": {
            "count_graded": count_graded,
            "mae_overall": mae_overall,
            "beat_baseline_rate_overall": beat_overall,
            "beat_baseline_rate_by_type": beat_by_type,
        },
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
            "/skills/cooccurrence", "/skills/{skill}/companions",
            "/postings/recent", "/postings/foreign-currency",
            "/cities/breakdown", "/salaries/summary", "/coverage",
            "/companies/top", "/companies/{name}",
            "/forecasts/pending", "/forecasts/accuracy",
            "/insights/live", "/system/health",
        ],
    }
