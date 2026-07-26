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


# --------------------------------------------------------------------------
# Substantive-skill filter -- the ONE definition of "a skill that belongs
# in an unscoped, aggregate market ranking" (an insight headline, a "top
# skill"/"market leader" stat, a curriculum gap). requirement_type=='skill'
# excludes 'attribute' (work_arrangement entries: on_site, full_time,
# remote, morning_shift, ...) and 'language'; category not in
# SUBSTANTIVE_SKILL_EXCLUDED_CATEGORIES excludes soft skills (near-
# universal, low-signal) and office_admin likewise.
#
# This exact filter has leaked FOUR separate times from independent,
# incomplete reimplementations that each checked only `category != 'soft'`
# (missing office_admin, and missing requirement_type entirely -- which is
# how a work_arrangement ATTRIBUTE like On-Site ends up presented as the
# market's "top skill"): in briefing.py/forecast.py's top-skills and
# forecast-target selection, in /insights/live and the homepage's "market
# leader" stat, in /coverage's "learn this next" ranking, and in the
# curriculum alignment lists -- where the fix was applied to only ONE of
# the three derived lists, so Communication and Documentation headlined
# "taught and demanded" and "taught, barely visible" was almost entirely
# soft entries. Every one of those call sites now goes through this helper,
# and the curriculum builder applies it at the INPUT sets rather than
# per-list, so a partial fix isn't expressible there anymore.
#
# NOT applied to /skills/top's own category-scoped or include_soft-toggled
# output, or to _posting_skills_map/_compute_skill_cooccurrence's default
# behavior -- those are the user's own explicit choice (picking a category,
# flipping the soft-skills toggle), not an aggregate claim, and are a
# deliberately different, legitimate use of the same taxonomy.
SUBSTANTIVE_SKILL_EXCLUDED_CATEGORIES = frozenset({"soft", "office_admin"})


def _has_skill_requirement_type(skill_key: str, taxonomy: dict) -> bool:
    """requirement_type == 'skill' (default when absent) -- the primitive
    _is_substantive_skill composes from. Used standalone only by /coverage,
    which needs the attribute/credential/language exclusion (you can't learn
    'on-site') but must NOT drop soft or office_admin skills: those are real
    things a person can learn and legitimately appear in its advice."""
    return taxonomy["skills"].get(skill_key, {}).get("requirement_type", "skill") == "skill"


def _is_substantive_skill(skill_key: str, taxonomy: dict) -> bool:
    spec = taxonomy["skills"].get(skill_key)
    if spec is None or not _has_skill_requirement_type(skill_key, taxonomy):
        return False
    return spec.get("category") not in SUBSTANTIVE_SKILL_EXCLUDED_CATEGORIES


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

# forecast.py's BULK_COMPANY_KEY, duplicated here rather than imported --
# same reasoning as briefing.py's _outcome not importing api/main.py's
# _forecast_outcome: api/ and the root-level scripts are deliberately
# independent dependency surfaces. Confirmed bulk by direct investigation
# (a templated-listing poster), not by crossing _BULK_THRESHOLD or the
# exact-tagset-duplicate share (see _is_templated_share) -- at current
# volume this company sits at ~22% of the corpus, under the 25% cutoff,
# and well under half its own postings are exact tagset duplicates, so
# neither of this file's own heuristics catches it. A name-based
# exclusion is needed alongside _bulk_company_keys' threshold, not
# instead of it -- some future bulk poster may cross the threshold
# without ever being individually confirmed.
KNOWN_BULK_COMPANY_KEYS = frozenset({"naseeb enterprise inc"})


def _bulk_company_keys(postings: list[dict]) -> set[str]:
    """Company (normalized) keys holding > 25% of all postings, UNION
    KNOWN_BULK_COMPANY_KEYS -- "bulk posters" whose volume (or confirmed
    templated-listing behavior) can single-handedly manufacture skill/
    pairing signal that has nothing to do with broader market demand.
    Threshold share is always computed against the full, unfiltered
    posting list passed in, so it doesn't drift if called after some
    other filtering."""
    total = len(postings)
    if total == 0:
        return set()
    counts: dict[str, int] = defaultdict(int)
    for p in postings:
        if p.get("company"):
            counts[_normalize_company_key(p["company"])] += 1
    threshold_keys = {key for key, count in counts.items() if count / total > _BULK_THRESHOLD}
    return threshold_keys | (KNOWN_BULK_COMPANY_KEYS & counts.keys())


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
            # Exposed so callers doing their own "top skill" style claim
            # (e.g. the homepage's market-leader stat) can apply the full
            # substantive-skill filter client-side -- category alone
            # (checked above only against the include_soft toggle) isn't
            # enough to tell a work_arrangement attribute like On-Site
            # apart from an actual skill; see _is_substantive_skill.
            "requirement_type": spec.get("requirement_type", "skill"),
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

    # Workplace attributes (on-site, night shift, full-time), credentials and
    # languages are requirement_type != "skill". They are real things postings
    # ask for, but they are not things a person LEARNS -- and this endpoint's
    # entire output is learning advice. Leaving them in did two visible kinds
    # of damage: the delta ranking recommended "learn On-Site next" above every
    # genuine skill (it is near-universal, so it always won on raw count), and
    # they inflated the denominator of every posting's requirement set, so
    # covering the actual technical asks still failed the 70% bar. Filtered
    # with the same predicate _build_curriculum_alignment already uses for the
    # same reason -- a curriculum can't teach an attribute either.
    user_skills = {s for s in user_skills if _has_skill_requirement_type(s, taxonomy)}
    ignored_non_skill = sorted(set(payload.skills) - user_skills - set(ignored_soft))

    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    postings, mentions = _apply_bulk_exclusion(postings, mentions, exclude_bulk)
    postings_by_id = {p["id"]: p for p in postings}

    posting_skills = {
        pid: {s for s in skills if _has_skill_requirement_type(s, taxonomy)}
        for pid, skills in _posting_skills_map(mentions, taxonomy, include_soft=False).items()
    }

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
        "ignored_non_skill_requirements": ignored_non_skill,
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
            "to answer. Workplace attributes (on-site, shift, full-time), "
            "credentials and languages are excluded from both your skill set "
            "and each posting's requirement set: they are not things a person "
            "learns, and this endpoint's whole output is learning advice. "
            "The delta ranking simulates learning exactly one "
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
# computed_at/audience plus two internal-only fields, "score" (ranking)
# and "sample_size" (sanity-gate input) -- both stripped before the
# response goes out in _generate_insights, since neither is part of the
# documented insight shape. Adding a new insight is: write the function,
# declare its minimum sample size as a named _XXX_MIN_* constant, add it
# to _INSIGHT_GENERATORS AND _INSIGHT_MIN_SAMPLE, and return "sample_size"
# in the result. Skipping that last part isn't a silent bug -- the sanity
# gate treats a missing sample_size as "not checked", not "checked and
# fine", but every existing generator sets it, and a reviewer should
# expect any new one to as well.
#
# audience is 'market' (a finding for someone looking for work -- shown on
# the homepage) or 'system' (a finding about the pipeline/data itself --
# shown on /engine, where pipeline self-monitoring already lives). Every
# generator sets this explicitly; nothing infers it from a value shape.
# Two generators are 'system' by nature: the non-PKR-currency finding and
# the Rozee templated-tagset finding are both about data quality, not
# market demand -- genuinely useful, just the wrong audience for a
# homepage aimed at a job seeker who has never seen this site.
#
# Every generator's output, regardless of what it already checked
# internally, passes through _insight_sanity_gate before publication --
# see that function's own docstring for what it checks and why a second,
# generator-agnostic layer exists at all instead of trusting each
# generator's own logic.
# --------------------------------------------------------------------------

_TOP_MOVER_MIN_COMPLETE_WEEKS = 3  # fewer than this and the "previous" week could be the first, partial week


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
    if len(complete_weeks) < _TOP_MOVER_MIN_COMPLETE_WEEKS:
        return None  # not enough complete weeks of history to trust a comparison

    last_week, prev_week = complete_weeks[-1], complete_weeks[-2]
    # Substantive skills only -- see _is_substantive_skill's own comment.
    # Without this, the biggest "mover" is routinely a work_arrangement
    # attribute (On-Site, Full-Time) riding ordinary volume noise, not a
    # market shift in what employers are asking for.
    substantive_mentions = [m for m in mentions if _is_substantive_skill(m["skill"], taxonomy)]
    skill_postings = _skill_postings_map(substantive_mentions)

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
    direction = "risen" if delta > 0 else "fallen"
    return {
        "headline": f"Demand for {display} has {direction} this week",
        "detail": (
            f"{display} appeared in {cur} postings this week, versus {prev} the week before -- "
            f"a change of {abs(delta)}."
        ),
        "value": {"skill": skill_key, "current_week": cur, "previous_week": prev, "delta": delta},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "audience": "market",
        "score": abs(delta),
        "sample_size": len(complete_weeks),
    }


_TEMPLATED_SHARE_MIN_POSTINGS = 5


def _insight_templated_share(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(b) Share of Rozee postings whose skill-tag block is an exact
    duplicate of another posting's -- evidence of templated listings."""
    rozee = [p for p in postings if p["source"] == "rozee"]
    if len(rozee) < _TEMPLATED_SHARE_MIN_POSTINGS:
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
        "audience": "system",
        "score": share * 100,
        "sample_size": total_tagged,
    }


_LIFESPAN_MIN_DROPPED_POSTINGS = 5
_LIFESPAN_MIN_CITY_SAMPLE = 3


def _insight_posting_lifespan(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(c) Median (last_seen_at - first_seen_at) for postings that dropped
    out of the latest scrape run, overall and per city."""
    dated = [p for p in postings if p.get("scrape_run_id") and p.get("last_seen_at") and p.get("first_seen_at")]
    if not dated:
        return None

    latest_posting = max(dated, key=lambda p: p["last_seen_at"])
    latest_run_id = latest_posting["scrape_run_id"]

    dropped = [p for p in dated if p["scrape_run_id"] != latest_run_id]
    if len(dropped) < _LIFESPAN_MIN_DROPPED_POSTINGS:
        return None

    def lifespan_days(p: dict) -> float:
        return (_parse_ts(p["last_seen_at"]) - _parse_ts(p["first_seen_at"])).total_seconds() / 86400

    overall_days = [lifespan_days(p) for p in dropped]
    overall_median = statistics.median(overall_days)
    # No bespoke "median < 1" guard here anymore -- most "dropped" postings
    # right now were seen in exactly one scrape run (first_seen_at ==
    # last_seen_at), which floors the median at 0, and a proxy threshold
    # like this one is exactly the kind of ad-hoc, generator-local guard
    # that individually missed cases before. The sanity gate's rendered-
    # zero check (see _insight_sanity_gate) catches "about 0 days" more
    # precisely -- against the actual displayed text, not a stand-in cutoff.

    by_city = defaultdict(list)
    for p in dropped:
        city = (p.get("city") or "").strip().title()
        if city:
            by_city[city].append(lifespan_days(p))
    city_medians = {c: round(statistics.median(v), 2) for c, v in by_city.items() if len(v) >= _LIFESPAN_MIN_CITY_SAMPLE}

    return {
        "headline": f"Job postings here typically stay listed for about {overall_median:.0f} days",
        "detail": (
            f"Based on {len(dropped)} postings that have since been taken down or replaced, "
            f"measured from when each one first appeared to when it was last seen."
        ),
        "value": {
            "overall_median_days": round(overall_median, 2),
            "sample_size": len(dropped),
            "by_city_median_days": city_medians,
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "audience": "market",
        "score": overall_median,
        "sample_size": len(dropped),
    }


_DOMINANCE_MIN_SKILL_COUNT = 5  # minimum sample for either side of the ratio to mean anything
_DOMINANCE_MIN_RATIO = 1.5


def _insight_dominance_ratio(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(d) Largest posting-count ratio between two substantive skills in
    the same category, e.g. SQL vs Tableau. Substantive-skill filter (see
    _is_substantive_skill) applies here for the same reason it applies
    everywhere else in this file: without it, the biggest "dominance" is
    routinely a work_arrangement attribute (On-Site over Contract) or an
    office_admin skill, not a finding about the technical market."""
    skill_postings = _skill_postings_map(mentions)

    by_category = defaultdict(list)
    for skill_key, spec in taxonomy["skills"].items():
        if not _is_substantive_skill(skill_key, taxonomy):
            continue
        count = len(skill_postings.get(skill_key, ()))
        if count >= _DOMINANCE_MIN_SKILL_COUNT:
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

    if best is None or best[0] < _DOMINANCE_MIN_RATIO:
        return None

    ratio, cat, leader_key, leader_count, other_key, other_count = best
    leader_display = taxonomy["skills"][leader_key]["display"]
    other_display = taxonomy["skills"][other_key]["display"]

    return {
        # No raw category key in user-facing text (e.g. "data_ml") -- the
        # comparison stands on its own without naming the grouping.
        "headline": f"{leader_display} is asked for far more often than {other_display}",
        "detail": (
            f"{leader_display} appears in {leader_count} postings, versus {other_count} for "
            f"{other_display} -- roughly {ratio:.0f} times as many."
        ),
        "value": {
            "category": cat,
            "leader": leader_key,
            "leader_count": leader_count,
            "runner_up": other_key,
            "runner_up_count": other_count,
            "ratio": round(ratio, 2),
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "audience": "market",
        "score": ratio,
        "sample_size": other_count,
    }


_FOREIGN_CURRENCY_MIN_COUNT = 1


def _insight_foreign_currency(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(e) Postings priced in a non-PKR currency, and what they are."""
    foreign = [p for p in postings if p.get("currency") and p["currency"] != "PKR"]
    if len(foreign) < _FOREIGN_CURRENCY_MIN_COUNT:
        return None

    breakdown = Counter(p["currency"] for p in foreign)
    examples = [
        {"title": p.get("title"), "currency": p["currency"], "detail_url": p.get("detail_url")}
        for p in foreign[:10]
    ]
    breakdown_str = ", ".join(f"{currency}: {count}" for currency, count in breakdown.most_common())

    return {
        "headline": f"{len(foreign)} postings are priced in a non-PKR currency",
        # System-audience detail: names the actual pipeline risk (averaging
        # a foreign-currency figure into a PKR salary statistic without
        # converting or excluding it first) rather than dumping the raw
        # Counter repr, which read as a data structure, not a sentence.
        "detail": (
            f"By currency -- {breakdown_str}. These postings' salary figures aren't in PKR; "
            f"any salary statistic that doesn't filter or convert by currency first would mix "
            f"them in with PKR salaries and be wrong."
        ),
        "value": {"count": len(foreign), "by_currency": dict(breakdown), "examples": examples},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "audience": "system",
        "score": len(foreign),
        "sample_size": len(foreign),
    }


_ZERO_TECHNICAL_MIN_COUNT = 1


def _insight_zero_technical(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(f) Share of postings mentioning zero substantive skills -- see
    _is_substantive_skill. Was previously "category != soft", which
    counted a posting as having a "technical skill" for mentioning nothing
    but On-Site and Full-Time -- exactly the leak this file's other
    generators had too."""
    if not postings:
        return None

    substantive_posting_ids = {
        m["posting_id"] for m in mentions if _is_substantive_skill(m["skill"], taxonomy)
    }
    zero_technical = sum(1 for p in postings if p["id"] not in substantive_posting_ids)
    if zero_technical < _ZERO_TECHNICAL_MIN_COUNT:
        return None
    share = zero_technical / len(postings)

    return {
        "headline": f"{share * 100:.0f}% of postings don't list a specific skill requirement",
        "detail": (
            f"{zero_technical} of {len(postings)} postings mention no particular skill at all -- "
            f"just a job title and, often, general requirements like experience or education."
        ),
        "value": {"zero_technical_count": zero_technical, "total_postings": len(postings), "share": round(share, 4)},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "audience": "market",
        "score": share * 100,
        "sample_size": zero_technical,
    }


_PAIRING_MIN_JOINT_COUNT = 15  # below this, the pairing is noise, not a finding -- suppressed, not hedged


def _insight_strongest_pairing(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(g) The skill pair demanded together far more than chance would
    predict, restricted to substantive skills (see _is_substantive_skill).

    Below _PAIRING_MIN_JOINT_COUNT joint postings, the pairing is
    genuinely too thin a sample to present as a finding (the underlying
    ratio can swing wildly from a couple of postings) -- suppressed
    entirely (returns None) rather than shown with a caveat, per the same
    reasoning _insight_top_mover applies to incomplete weeks."""
    substantive_mentions = [m for m in mentions if _is_substantive_skill(m["skill"], taxonomy)]
    pairs = _compute_skill_cooccurrence(substantive_mentions, taxonomy, len(postings), include_soft=False)
    if not pairs:
        return None

    best = max(pairs, key=lambda p: p["lift"])
    if best["joint_count"] < _PAIRING_MIN_JOINT_COUNT:
        return None

    return {
        "headline": f"{best['display_a']} and {best['display_b']} are commonly asked for together",
        "detail": f"{best['joint_count']} postings mention both {best['display_a']} and {best['display_b']}.",
        "value": {
            "skill_a": best["skill_a"],
            "skill_b": best["skill_b"],
            "joint_count": best["joint_count"],
            "lift": best["lift"],
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "audience": "market",
        "score": best["lift"],
        "sample_size": best["joint_count"],
    }


# Matches _MIN_JOINT_COUNT (the cooccurrence helper's own floor, which
# already keeps thin pairs out of `pairs` before this function ever sees
# them) -- declared again here, explicitly, so this generator's minimum
# is self-documenting and independently checkable rather than an inherited
# side effect of a different function's constant.
_TOP_SKILL_COMPANION_MIN_JOINT_COUNT = _MIN_JOINT_COUNT


def _insight_top_skill_companion(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(h) Most demanded companion of the single most-mentioned substantive
    skill (see _is_substantive_skill) -- "employers asking for X also ask
    for Y", applied to whichever X currently leads the market."""
    substantive_mentions = [m for m in mentions if _is_substantive_skill(m["skill"], taxonomy)]
    skill_postings = _skill_postings_map(substantive_mentions)
    if not skill_postings:
        return None

    top_skill = max(skill_postings, key=lambda s: len(skill_postings[s]))
    pairs = _compute_skill_cooccurrence(substantive_mentions, taxonomy, len(postings), include_soft=False)

    companions = []
    for p in pairs:
        if p["skill_a"] == top_skill:
            companions.append((p["skill_b"], p["display_b"], p["p_b_given_a"], p["joint_count"]))
        elif p["skill_b"] == top_skill:
            companions.append((p["skill_a"], p["display_a"], p["p_a_given_b"], p["joint_count"]))
    if not companions:
        return None

    companion_skill, companion_display, p_given, joint = max(companions, key=lambda c: c[2])
    if joint < _TOP_SKILL_COMPANION_MIN_JOINT_COUNT:
        return None
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
        "audience": "market",
        "score": p_given * 100,
        "sample_size": joint,
    }


_TOP_COMPANY_MIN_POSTINGS = 20  # below this, "most active hirer" is too thin a sample -- suppressed, not hedged


def _insight_top_company(postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict | None:
    """(i) Top hiring company by distinct posting count -- bulk posters
    excluded entirely (see _bulk_company_keys), not just caveated. A
    company holding >25% of all postings names a templated-listing
    pattern (see the "templated share" finding on /engine), not a company
    actually hiring at that scale; excluding is the honest answer for a
    homepage claim, where there's no room for the concentration caveat
    /methodology already carries."""
    bulk_keys = _bulk_company_keys(postings)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for p in postings:
        if p.get("company") and _normalize_company_key(p["company"]) not in bulk_keys:
            grouped[_normalize_company_key(p["company"])].append(p)
    if not grouped:
        return None

    _, subset = max(grouped.items(), key=lambda kv: len(kv[1]))
    if len(subset) < _TOP_COMPANY_MIN_POSTINGS:
        # Same suppression pattern as the thin-pairing insight: a single
        # digit-count "most active hirer" is too thin a sample for a
        # headline claim, and the top company (already the max by
        # definition) is the only candidate that could clear the bar --
        # if it doesn't, nothing else does either, so this suppresses the
        # insight entirely rather than falling back to a smaller runner-up.
        return None

    display_name = Counter(p["company"] for p in subset).most_common(1)[0][0]
    return {
        "headline": f"{display_name} is the most active hirer tracked right now",
        "detail": f"{len(subset)} distinct postings from {display_name} across the tracked window.",
        "value": {"company": display_name, "posting_count": len(subset)},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "audience": "market",
        "score": len(subset),
        "sample_size": len(subset),
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

# Every generator's declared minimum, keyed by function -- see each
# generator's own _XXX_MIN_* constant. The sanity gate re-checks
# insight["sample_size"] against this INDEPENDENTLY of whatever internal
# threshold the generator itself already applied, on the theory that a
# generator's own check is exactly the thing that has already been wrong:
# three separate insight bugs shipped in one session (a 0-day median, an
# 11-posting headline hirer, a bulk poster that passed a 25%-of-corpus
# threshold at 22%) because each generator's own ad-hoc guard was the ONLY
# check, and it individually missed a case every time. This registry is
# what makes "every generator must declare its minimum as a named
# constant" actually enforced, not just documented.
_INSIGHT_MIN_SAMPLE = {
    _insight_top_mover: _TOP_MOVER_MIN_COMPLETE_WEEKS,
    _insight_templated_share: _TEMPLATED_SHARE_MIN_POSTINGS,
    _insight_posting_lifespan: _LIFESPAN_MIN_DROPPED_POSTINGS,
    _insight_dominance_ratio: _DOMINANCE_MIN_SKILL_COUNT,
    _insight_foreign_currency: _FOREIGN_CURRENCY_MIN_COUNT,
    _insight_strongest_pairing: _PAIRING_MIN_JOINT_COUNT,
    _insight_top_skill_companion: _TOP_SKILL_COMPANION_MIN_JOINT_COUNT,
    _insight_top_company: _TOP_COMPANY_MIN_POSTINGS,
    _insight_zero_technical: _ZERO_TECHNICAL_MIN_COUNT,
}

_GATE_NUMERIC_TOKEN_RE = re.compile(r"-?\d+(?:\.\d+)?")
_GATE_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
_GATE_RATIO_LIKE_KEYS = ("ratio", "lift")


def _insight_sanity_gate(insight: dict, min_sample: int) -> str | None:
    """The last line of defense before an insight reaches a reader, applied
    to every generator's output regardless of what that generator already
    checked internally. Returns a short reason string if the insight
    should be suppressed, or None if it clears every check:

      1. Sample too thin: insight["sample_size"] below the generator's own
         declared minimum (see _INSIGHT_MIN_SAMPLE).
      2. Renders as zero: any numeral in the headline or detail text is
         literally "0" (or "0.0", "0%", ...) at the precision actually
         shown. "About 0 days" or "0 postings" is never publishable, even
         if the true underlying value is a small positive number that
         happened to round down -- see the posting-lifespan bug this gate
         was built to catch, which lived in the HEADLINE, not the detail,
         which is why both are scanned.
      3. Percentage out of range: any "N%" in the rendered text with N
         outside [0, 100].
      4. Negative ratio/lift: any value-dict key naming a ratio-like
         quantity (matched by substring, so "ratio", "runner_up_ratio",
         "lift" all count) that's negative.

    None of these trust the generator's own arithmetic -- they check the
    OUTPUT, the same way the bugs that motivated this function were only
    visible in the rendered text, not in isolated unit logic.
    """
    sample_size = insight.get("sample_size")
    if sample_size is not None and sample_size < min_sample:
        return f"sample_size={sample_size} below minimum {min_sample}"

    rendered = f"{insight['headline']} {insight['detail']}"

    for token in _GATE_NUMERIC_TOKEN_RE.findall(rendered):
        if float(token) == 0.0:
            return f"rendered text contains a zero-valued number ({token!r})"

    for pct in _GATE_PERCENT_RE.findall(rendered):
        pct_val = float(pct)
        if pct_val > 100 or pct_val < 0:
            return f"percentage {pct_val} outside [0, 100]"

    for key, val in insight.get("value", {}).items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        if any(marker in key.lower() for marker in _GATE_RATIO_LIKE_KEYS) and val < 0:
            return f"{key}={val} is negative"

    return None


def _generate_insights(postings: list[dict], mentions: list[dict], taxonomy: dict) -> tuple[list[dict], list[dict]]:
    """Runs every generator, applies the sanity gate to each result, and
    returns (published, suppressions). insights_live() only ever returns
    `published` to API clients -- `suppressions` exists so the gate's
    decisions are inspectable (directly, by calling this function, or via
    the logger.info line below) rather than a card just silently not
    appearing. Each suppression entry is {"generator": ..., "reason": ...}."""
    results = []
    suppressions: list[dict] = []
    for gen in _INSIGHT_GENERATORS:
        try:
            result = gen(postings, mentions, taxonomy)
        except Exception as exc:
            logger.warning(f"Insight generator {gen.__name__} failed: {exc}")
            suppressions.append({"generator": gen.__name__, "reason": f"exception: {exc}"})
            continue
        if result is None:
            continue

        reason = _insight_sanity_gate(result, _INSIGHT_MIN_SAMPLE[gen])
        if reason is not None:
            logger.info(f"Insight suppressed by sanity gate: generator={gen.__name__} reason={reason}")
            suppressions.append({"generator": gen.__name__, "reason": reason})
            continue

        results.append(result)

    results.sort(key=lambda r: -r["score"])
    top = results[:6]
    for r in top:
        r.pop("score", None)
        r.pop("sample_size", None)

    return top, suppressions


@app.get("/insights/live")
@cached()
def insights_live():
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()
    taxonomy = queries.get_taxonomy()

    top, _suppressions = _generate_insights(postings, mentions, taxonomy)
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


# Forecast batches logged BEFORE forecast.py's AUTOMATED_COLLECTION_START
# fix, whose trailing-mean/baseline history was contaminated by Mustakbil's
# one-time 2026-07-11/07-12 bulk backfill week -- confirmed by direct
# recomputation, not inference: the 2026-07-20 batch's entire history was
# that single backfill week (predicted == baseline == 261 for volume/all,
# since a 1-week trailing mean equals its only input), and the 2026-07-27
# batch's mean averaged the backfill week (261) with the first steady week
# (14), producing predicted=137.5 against baseline=14. Both batches are
# immutable rows (already logged, graded or not) and stay exactly as
# published -- this is a read-only annotation surfaced alongside them, not
# a correction. Forecasts logged after the fix are not in this set.
COLLECTION_REGIME_CONTAMINATED_WEEKS = frozenset({"2026-07-20", "2026-07-27"})


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
            "source_scope": f.get("source_scope"),
            "collection_regime_note": f["target_week_start"] in COLLECTION_REGIME_CONTAMINATED_WEEKS,
        }
        for f in pending
    ]

    return {"count": len(rows), "forecasts": rows}


def _forecast_outcome(abs_error: float, baseline_abs_error: float) -> str:
    """beat/tie/lost, derived here purely from the two error columns
    already on every graded forecast row -- no schema change. The stored
    beat_baseline is a boolean (abs_error < baseline_abs_error) that
    collapses a tie into "not beat": a short-history forecast that comes
    out EQUAL to the baseline (common early on, by construction, when
    there isn't yet enough signal to diverge from it) would read as a
    loss under that boolean alone. This is the honest three-way split."""
    if abs_error < baseline_abs_error:
        return "beat"
    if abs_error > baseline_abs_error:
        return "lost"
    return "tie"


@app.get("/forecasts/accuracy")
@cached()
def forecasts_accuracy():
    """Every graded forecast, plus a summary: overall MAE, beat-baseline
    rate overall and by target_type, the three-way beat/tie/lost outcome
    count, and the graded count. MAPE is deliberately not in the summary
    (a handful of low-count skill targets would make it wildly noisy) --
    per-row pct_error is still included for rows where actual > 0, so the
    detail is there without the misleading headline aggregate."""
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
            "outcome": _forecast_outcome(f["abs_error"], f["baseline_abs_error"]),
            "pct_error": pct_error,
            "graded_at": f["graded_at"],
            "source_scope": f.get("source_scope"),
            "collection_regime_note": f["target_week_start"] in COLLECTION_REGIME_CONTAMINATED_WEEKS,
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

    outcome_counts_overall = {
        "beat": sum(1 for r in rows if r["outcome"] == "beat"),
        "tie": sum(1 for r in rows if r["outcome"] == "tie"),
        "lost": sum(1 for r in rows if r["outcome"] == "lost"),
    }

    return {
        "forecasts": rows,
        "summary": {
            "count_graded": count_graded,
            "mae_overall": mae_overall,
            "beat_baseline_rate_overall": beat_overall,
            "beat_baseline_rate_by_type": beat_by_type,
            "outcome_counts_overall": outcome_counts_overall,
        },
    }


# --------------------------------------------------------------------------
# GET /backtest/summary, GET /backtest/detail
#
# backtests is a SEPARATE table from forecasts, read via its own
# queries.get_backtests() -- never mixed with /forecasts/pending or
# /forecasts/accuracy above. It is retrospective evidence only: computed
# after outcomes were already known, fully mutable and re-runnable (see
# backtest.py's module docstring). A backtest shows whether the model has
# any skill at all; only /forecasts/accuracy proves a prediction was made
# before its outcome existed. Never present these numbers as live forecast
# performance.
# --------------------------------------------------------------------------

def _backtest_outcome_counts(rows: list[dict]) -> dict:
    beat = sum(1 for r in rows if r["outcome"] == "beat")
    tie = sum(1 for r in rows if r["outcome"] == "tie")
    lost = sum(1 for r in rows if r["outcome"] == "lost")
    return {
        "n": len(rows),
        "beat": beat,
        "tie": tie,
        "lost": lost,
        "beat_rate": round(beat / len(rows), 4) if rows else None,
        "mae": round(statistics.fmean(r["abs_error"] for r in rows), 4) if rows else None,
    }


@app.get("/backtest/summary")
@cached()
def backtest_summary():
    taxonomy = queries.get_taxonomy()
    rows = queries.get_backtests()

    if not rows:
        return {
            "n_weeks": 0,
            "n_rows": 0,
            "source_scope": None,
            "overall": _backtest_outcome_counts([]),
            "by_target": [],
        }

    source_scope = rows[0].get("source_scope")
    n_weeks = len({r["target_week_start"] for r in rows})

    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_key[(r["target_type"], r["target_key"])].append(r)

    by_target = [
        {
            "target_type": target_type,
            "target_key": target_key,
            "display": _forecast_display(target_type, target_key, taxonomy),
            **_backtest_outcome_counts(subset),
        }
        for (target_type, target_key), subset in by_key.items()
    ]
    by_target.sort(key=lambda t: (t["target_type"], -t["n"], t["target_key"]))

    return {
        "n_weeks": n_weeks,
        "n_rows": len(rows),
        "source_scope": source_scope,
        "overall": _backtest_outcome_counts(rows),
        "by_target": by_target,
    }


@app.get("/backtest/detail")
@cached()
def backtest_detail():
    """Every backtests row, most recent target week first."""
    taxonomy = queries.get_taxonomy()
    rows = queries.get_backtests()
    rows = sorted(
        rows, key=lambda r: (r["target_week_start"], r["target_type"], r["target_key"]), reverse=True
    )

    return {
        "count": len(rows),
        "backtests": [
            {
                "target_type": r["target_type"],
                "target_key": r["target_key"],
                "display": _forecast_display(r["target_type"], r["target_key"], taxonomy),
                "target_week_start": r["target_week_start"],
                "model_version": r["model_version"],
                "predicted": r["predicted"],
                "baseline_predicted": r["baseline_predicted"],
                "actual": r["actual"],
                "abs_error": r["abs_error"],
                "baseline_abs_error": r["baseline_abs_error"],
                "outcome": r["outcome"],
                "source_scope": r["source_scope"],
                "computed_at": r["computed_at"],
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------
# Curriculum alignment -- compares HEC/NCEAC computing curricula (parsed by
# curriculum.py from data/curricula/ PDFs) against live market demand.
#
# SCOPE LIMITATION: both source documents cover COMPUTING disciplines only
# -- this index says nothing about trades, healthcare, education, or any
# other domain this project tracks. Market demand is deliberately scoped
# to postings in technology_it/engineering to match.
#
# queries.get_skill_mentions() is already scoped to queries.ACTIVE_TAXONOMY
# (see that constant's comment) and queries.get_taxonomy() already loads
# its display/category names, so nothing here needs its own taxonomy
# filtering anymore -- it used to, before that was centralized.
# --------------------------------------------------------------------------

CURRICULUM_SCOPE_NOTE = (
    "Compared against HEC's and NCEAC's official computing curricula. Computing "
    "disciplines only, so it says nothing about the other fields this site tracks."
)
CURRICULUM_TAUGHT_NOT_DEMANDED_NOTE = (
    "Foundational computing subjects (data structures, algorithms, operating systems) "
    "may never appear as a NAMED requirement in a posting even though the role depends "
    "on them, so a thin posting count here is not evidence a subject is unwanted. Rows "
    "reading zero are never published -- an absence this data can't distinguish from "
    "'the taxonomy never named it here' isn't a finding."
)
CURRICULUM_DEMANDED_NOT_TAUGHT_NOTE = (
    "All three lists count teachable technical and professional skills only. "
    "Workplace attributes (on-site, shift, full-time), credentials and languages are "
    "excluded because a curriculum can't teach them; soft and office/admin skills are "
    "excluded because curricula do cover them, through general-education requirements "
    "the course parser can't itemize skill-by-skill."
)
CURRICULUM_MATCHING_NOTE = (
    "Matching is taxonomy-based: a skill outside the active taxonomy is invisible "
    "on BOTH sides of this comparison, not just one. A curriculum topic and a "
    "market demand that are both real but both unnamed in the taxonomy will never "
    "appear here."
)
CURRICULUM_MARKET_DOMAINS = ("technology_it", "engineering")
CURRICULUM_GAP_MIN_COMPANIES = 5
CURRICULUM_NEAR_ZERO_POSTINGS = 2
# Floor for the "taught, barely visible" list: 1, not 0. A row asserting
# zero postings is never a useful published claim -- see the list's own
# comment in _build_curriculum_alignment.
CURRICULUM_NEAR_ZERO_MIN_POSTINGS = 1
# Below this many surviving rows the list is dropped entirely rather than
# published as a couple of stragglers.
CURRICULUM_NEAR_ZERO_MIN_ROWS = 3
# All three lists are restricted to SUBSTANTIVE skills by one input-level
# filter (_is_substantive_skill) inside _build_curriculum_alignment -- both
# the requirement_type == "skill" half and the soft/office_admin category
# half. See that call site's comment; there is deliberately no second,
# per-list copy of either check.


def _curriculum_market_stats() -> tuple[dict[str, set[str]], dict[str, set[str]], int]:
    """Returns (skill -> distinct posting ids, skill -> distinct company
    keys, total postings considered), restricted to postings in
    CURRICULUM_MARKET_DOMAINS."""
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()

    scoped_postings = [p for p in postings if p.get("domain") in CURRICULUM_MARKET_DOMAINS]
    scoped_ids = {p["id"] for p in scoped_postings}
    scoped_mentions = [m for m in mentions if m["posting_id"] in scoped_ids]

    skill_postings = _skill_postings_map(scoped_mentions)
    skill_companies = _skill_company_map(scoped_postings, scoped_mentions)
    return skill_postings, skill_companies, len(scoped_postings)


def _curriculum_skill_courses() -> dict[str, set[str]]:
    """skill -> set of distinct curriculum_courses ids that matched it,
    via either match_source (title or topics)."""
    skill_map = queries.get_curriculum_skill_map()
    out: dict[str, set[str]] = defaultdict(set)
    for row in skill_map:
        out[row["skill"]].add(row["course_id"])
    return out


def _build_curriculum_alignment() -> dict:
    taxonomy = queries.get_taxonomy()
    skill_postings, skill_companies, total_market_postings = _curriculum_market_stats()
    skill_courses = _curriculum_skill_courses()
    courses = queries.get_curriculum_courses()

    def _display(skill_key: str) -> dict:
        spec = taxonomy["skills"].get(skill_key, {})
        return {
            "skill": skill_key,
            "display": spec.get("display", skill_key),
            "category": spec.get("category", "unknown"),
        }

    # ONE filter, applied to both input sets, feeding all three derived
    # lists. _is_substantive_skill enforces both halves at once:
    # requirement_type == "skill" (a curriculum can't teach a workplace
    # attribute or a language requirement) AND category not in
    # {soft, office_admin} (curricula DO cover those via general-education
    # requirements the course parser can't itemize, so a soft/admin entry on
    # either side is a matching artifact, not a finding).
    #
    # This used to be _has_skill_requirement_type here plus a category check
    # inside demanded_not_taught only -- which let Communication and
    # Documentation headline "taught and demanded", and left "taught, barely
    # visible" almost entirely soft entries. Filtering at the input keeps
    # that from happening to any list, present or future.
    all_market_skills = {s for s in skill_postings.keys() if _is_substantive_skill(s, taxonomy)}
    all_curriculum_skills = {s for s in skill_courses.keys() if _is_substantive_skill(s, taxonomy)}

    # a) TAUGHT AND DEMANDED -- ranked by market posting count.
    taught_and_demanded = [
        {
            **_display(skill),
            "posting_count": len(skill_postings[skill]),
            "company_count": len(skill_companies.get(skill, ())),
            "course_count": len(skill_courses[skill]),
        }
        for skill in (all_market_skills & all_curriculum_skills)
    ]
    taught_and_demanded.sort(key=lambda r: -r["posting_count"])

    # b) DEMANDED NOT TAUGHT -- the headline list. >=5 distinct companies and
    # zero curriculum matches. Substantive-only is already guaranteed by the
    # input filter above; no local category check here. Ranked by company
    # count (the qualifying metric), posting count as tiebreak.
    demanded_not_taught = []
    for skill in (all_market_skills - all_curriculum_skills):
        company_count = len(skill_companies.get(skill, ()))
        if company_count >= CURRICULUM_GAP_MIN_COMPANIES:
            demanded_not_taught.append({
                **_display(skill),
                "posting_count": len(skill_postings[skill]),
                "company_count": company_count,
            })
    demanded_not_taught.sort(key=lambda r: (-r["company_count"], -r["posting_count"]))

    # c) TAUGHT NOT DEMANDED -- curriculum skills with low (not absent)
    # market presence, ranked by course_count so the most heavily taught
    # comes first.
    #
    # The floor is deliberate: a published row reading "0 postings" asserts
    # an absence this data can't support -- it means "no posting named this
    # skill", which is indistinguishable from "the taxonomy never had a
    # chance to name it here". Same principle as the insight sanity gate:
    # a claim that rounds to zero doesn't get published. And a list this
    # short stops being a pattern, so below _MIN_ROWS it is dropped whole
    # rather than shown as one or two lonely rows.
    taught_not_demanded = []
    for skill in all_curriculum_skills:
        posting_count = len(skill_postings.get(skill, ()))
        if CURRICULUM_NEAR_ZERO_MIN_POSTINGS <= posting_count <= CURRICULUM_NEAR_ZERO_POSTINGS:
            taught_not_demanded.append({
                **_display(skill),
                "posting_count": posting_count,
                "company_count": len(skill_companies.get(skill, ())),
                "course_count": len(skill_courses[skill]),
            })
    taught_not_demanded.sort(key=lambda r: -r["course_count"])
    if len(taught_not_demanded) < CURRICULUM_NEAR_ZERO_MIN_ROWS:
        taught_not_demanded = []

    matched_course_ids = {cid for ids in skill_courses.values() for cid in ids}

    return {
        "scope_note": CURRICULUM_SCOPE_NOTE,
        "matching_note": CURRICULUM_MATCHING_NOTE,
        "market_domains": list(CURRICULUM_MARKET_DOMAINS),
        "market_postings_considered": total_market_postings,
        "courses_total": len(courses),
        "courses_matched": len(matched_course_ids),
        "courses_unmatched": len(courses) - len(matched_course_ids),
        "taught_and_demanded": taught_and_demanded,
        "demanded_not_taught": demanded_not_taught,
        "demanded_not_taught_note": CURRICULUM_DEMANDED_NOT_TAUGHT_NOTE,
        "taught_not_demanded": taught_not_demanded,
        "taught_not_demanded_note": CURRICULUM_TAUGHT_NOT_DEMANDED_NOTE,
    }


@app.get("/curriculum/alignment")
@cached()
def curriculum_alignment():
    """The full index: taught+demanded, demanded-not-taught, and
    taught-not-demanded, plus course parse/match summary stats."""
    return _build_curriculum_alignment()


@app.get("/curriculum/gaps")
@cached()
def curriculum_gaps():
    """The headline list on its own: skills the market demands (>=5
    distinct companies, technology_it/engineering only) that zero
    curriculum course matches."""
    data = _build_curriculum_alignment()
    return {
        "scope_note": data["scope_note"],
        "matching_note": data["matching_note"],
        "demanded_not_taught_note": data["demanded_not_taught_note"],
        "market_domains": data["market_domains"],
        "min_companies_threshold": CURRICULUM_GAP_MIN_COMPANIES,
        "count": len(data["demanded_not_taught"]),
        "gaps": data["demanded_not_taught"],
    }


# --------------------------------------------------------------------------
# /paths/{family} -- skill adjacency by seniority, within a job_family
# (see job_family_classifier.py for how postings get that field).
#
# HONEST CONSTRAINT: job postings never show the same person twice, so
# career transitions cannot be observed. Everything below infers what
# employers ASK FOR at each experience level within a family, and the
# delta between levels -- it is NOT observed career movement. This text
# is repeated verbatim in every API response below and on the page.
#
# Depends on job_family (title normalization, job_family_classifier.py)
# and experience_level (structured_extraction.py's fresh/junior/mid/senior
# bucketing). mentions passed in here comes from queries.get_skill_mentions(),
# already scoped to queries.ACTIVE_TAXONOMY -- nothing extra to filter here.
# --------------------------------------------------------------------------

PATHS_HONEST_CONSTRAINT = (
    "Job postings never show the same person twice, so career transitions cannot be "
    "observed. This infers what employers ask for at each level within a family, and "
    "the delta between levels -- it is not observed career movement."
)
PATHS_LEVELS = ["fresh", "junior", "mid", "senior"]
PATHS_MIN_FAMILY_POSTINGS = 15
PATHS_MIN_LEVELS = 2
PATHS_MIN_SKILL_COMPANIES = 3  # floor for a skill to count in a delta OR a match signature
PATHS_MATCH_THRESHOLD = _STRONG_MATCH_THRESHOLD  # same "70% covered" bar as /coverage


def _build_family_path(family_key: str, family_display: str, fam_postings: list[dict], mentions: list[dict], taxonomy: dict) -> dict:
    """fam_postings: every posting with job_family == family_key (any
    experience_level, including None). mentions: queries.get_skill_mentions(),
    already scoped to the active taxonomy. Returns has_data=False with an
    honest reason naming the unmet threshold when the family doesn't
    qualify -- never a thin result presented as a finding."""
    base = {"family": family_key, "display": family_display}

    if len(fam_postings) < PATHS_MIN_FAMILY_POSTINGS:
        return {
            **base,
            "has_data": False,
            "n_postings": len(fam_postings),
            "levels_present": [],
            "reason": (
                f"Only {len(fam_postings)} posting(s) currently classified into this "
                f"family -- need at least {PATHS_MIN_FAMILY_POSTINGS}."
            ),
        }

    levels_present = [lvl for lvl in PATHS_LEVELS if any(p.get("experience_level") == lvl for p in fam_postings)]
    if len(levels_present) < PATHS_MIN_LEVELS:
        return {
            **base,
            "has_data": False,
            "n_postings": len(fam_postings),
            "levels_present": levels_present,
            "reason": (
                f"Only {len(levels_present)} experience level(s) represented "
                f"({', '.join(levels_present) if levels_present else 'none'}) -- need "
                f"at least {PATHS_MIN_LEVELS}."
            ),
        }

    fam_ids = {p["id"] for p in fam_postings}
    fam_mentions = [m for m in mentions if m["posting_id"] in fam_ids]

    level_objs: dict[str, dict] = {}
    for lvl in levels_present:
        lvl_postings = [p for p in fam_postings if p.get("experience_level") == lvl]
        lvl_ids = {p["id"] for p in lvl_postings}
        lvl_mentions = [m for m in fam_mentions if m["posting_id"] in lvl_ids]
        n_companies = len({_normalize_company_key(p["company"]) for p in lvl_postings if p.get("company")})
        skill_postings_map = _skill_postings_map(lvl_mentions)
        skill_companies_map = _skill_company_map(lvl_postings, lvl_mentions)

        skills = []
        for skill_key, company_set in skill_companies_map.items():
            spec = taxonomy["skills"].get(skill_key)
            if spec is None:
                continue  # stale mention row for a retired skill key, same guard as elsewhere
            skills.append({
                "skill": skill_key,
                "display": spec["display"],
                "category": spec["category"],
                "company_count": len(company_set),
                "posting_count": len(skill_postings_map.get(skill_key, ())),
            })
        skills.sort(key=lambda r: (-r["company_count"], -r["posting_count"]))

        level_objs[lvl] = {
            "level": lvl,
            "n_postings": len(lvl_postings),
            "n_companies": n_companies,
            "skills": skills,
        }

    # Delta: for each pair of CONSECUTIVELY PRESENT levels (a family missing
    # "mid" compares junior directly to senior -- honestly labeled with the
    # real level names, never silently interpolated), skills whose company
    # share is materially higher at the upper level. >=3 distinct companies
    # at the higher level is required for a skill to appear at all here.
    deltas = []
    for lower, higher in zip(levels_present, levels_present[1:]):
        lower_obj, higher_obj = level_objs[lower], level_objs[higher]
        lower_share = {
            s["skill"]: s["company_count"] / lower_obj["n_companies"]
            for s in lower_obj["skills"]
        } if lower_obj["n_companies"] else {}

        rows = []
        for s in higher_obj["skills"]:
            if s["company_count"] < PATHS_MIN_SKILL_COMPANIES:
                continue
            higher_share = s["company_count"] / higher_obj["n_companies"] if higher_obj["n_companies"] else 0.0
            lower_share_val = lower_share.get(s["skill"], 0.0)
            delta = higher_share - lower_share_val
            if delta <= 0:
                continue
            rows.append({
                "skill": s["skill"],
                "display": s["display"],
                "category": s["category"],
                "company_share_lower": round(lower_share_val, 3),
                "company_share_higher": round(higher_share, 3),
                "company_share_delta": round(delta, 3),
                "company_count_higher": s["company_count"],
            })
        rows.sort(key=lambda r: -r["company_share_delta"])

        deltas.append({
            "from_level": lower,
            "to_level": higher,
            "n_lower": {"postings": lower_obj["n_postings"], "companies": lower_obj["n_companies"]},
            "n_higher": {"postings": higher_obj["n_postings"], "companies": higher_obj["n_companies"]},
            "skills": rows,
        })

    return {
        **base,
        "has_data": True,
        "n_postings": len(fam_postings),
        "levels_present": levels_present,
        "levels": [level_objs[lvl] for lvl in levels_present],
        "deltas": deltas,
    }


@cached()
def _build_all_family_paths() -> dict[str, dict]:
    """Every family in job_families.yaml, has_data or not -- computed
    together in one pass so /paths/match doesn't refetch/refilter
    postings once per family."""
    families_meta = queries.get_job_families()
    taxonomy = queries.get_taxonomy()
    postings = queries.get_postings()
    mentions = queries.get_skill_mentions()

    postings_by_family: dict[str, list[dict]] = defaultdict(list)
    for p in postings:
        fam = p.get("job_family")
        if fam:
            postings_by_family[fam].append(p)

    return {
        meta["key"]: _build_family_path(meta["key"], meta["display"], postings_by_family.get(meta["key"], []), mentions, taxonomy)
        for meta in families_meta
    }


@app.get("/paths/{family}")
@cached()
def paths_for_family(family: str):
    """Levels, per-level skills (distinct-company counts), and deltas for
    one job_family. has_data=False + a `reason` naming the unmet
    threshold when the family doesn't qualify -- see PATHS_MIN_FAMILY_POSTINGS
    / PATHS_MIN_LEVELS."""
    families_meta = {f["key"]: f for f in queries.get_job_families()}
    if family not in families_meta:
        raise HTTPException(status_code=404, detail=f"Unknown job family '{family}'")

    result = dict(_build_all_family_paths()[family])
    result["honest_constraint"] = PATHS_HONEST_CONSTRAINT
    result["min_postings_threshold"] = PATHS_MIN_FAMILY_POSTINGS
    result["min_levels_threshold"] = PATHS_MIN_LEVELS
    return result


class PathsMatchRequest(BaseModel):
    skills: list[str]


def _better_family_match(a: dict, b: dict) -> bool:
    """True if candidate a should replace current-best b: higher match
    fraction wins, then a larger (more specific) signature, then family
    key for a stable tiebreak."""
    if a["match_fraction"] != b["match_fraction"]:
        return a["match_fraction"] > b["match_fraction"]
    if a["signature_size"] != b["signature_size"]:
        return a["signature_size"] > b["signature_size"]
    return a["family"] < b["family"]


def _best_family_match(user_skills: set[str], all_paths: dict[str, dict]) -> dict | None:
    """For every level of every qualifying family, a level's "signature" is
    the TECHNICAL (non-soft) skills a >=PATHS_MIN_SKILL_COMPANIES distinct
    companies actually ask for at that level -- soft skills are excluded
    from the signature for the same reason /coverage excludes them from
    user_skills: the picker never lets a user select one, so leaving them
    in would cap the achievable match fraction below 1.0 for no reason. If
    the user's skills cover >=PATHS_MATCH_THRESHOLD of some signature,
    that's a strong match -- same bar /coverage uses for a posting.
    Returns None (not a guess) when nothing clears the bar."""
    best = None
    for family_key, path in all_paths.items():
        if not path.get("has_data"):
            continue
        for level_obj in path["levels"]:
            signature = {
                s["skill"] for s in level_obj["skills"]
                if s["company_count"] >= PATHS_MIN_SKILL_COMPANIES and s["category"] != "soft"
            }
            if not signature:
                continue
            fraction = len(signature & user_skills) / len(signature)
            if fraction < PATHS_MATCH_THRESHOLD:
                continue
            candidate = {
                "family": family_key,
                "display": path["display"],
                "level": level_obj["level"],
                "match_fraction": round(fraction, 3),
                "signature_size": len(signature),
            }
            if best is None or _better_family_match(candidate, best):
                best = candidate
    return best


@app.post("/paths/match")
@cached()
def paths_match(payload: PathsMatchRequest):
    """Secondary panel for /analyzer: given the user's selected skills,
    finds the job_family + experience level they most strongly match into
    (see _best_family_match), and returns the skills that appear
    materially more at the NEXT level up that don't at theirs -- i.e. the
    delta this module already computes for that (level, next level) pair.
    matched=False (not a weak guess) when no family/level clears the
    match threshold; next_level=None when the matched level is already
    the top one this family has data for."""
    taxonomy = queries.get_taxonomy()
    # Same soft-skill exclusion as /coverage -- a family/level "signature"
    # is built from technical skill counts only, so a soft skill in the
    # input can never itself be matched into.
    user_skills = {s for s in payload.skills if taxonomy["skills"].get(s, {}).get("category") != "soft"}

    all_paths = _build_all_family_paths()
    best = _best_family_match(user_skills, all_paths)

    base_response = {
        "honest_constraint": PATHS_HONEST_CONSTRAINT,
        "match_threshold": PATHS_MATCH_THRESHOLD,
    }
    if best is None:
        return {
            **base_response,
            "matched": False,
            "family": None,
            "display": None,
            "your_level": None,
            "match_fraction": None,
            "next_level": None,
            "next_level_skills": [],
        }

    path = all_paths[best["family"]]
    levels_present = path["levels_present"]
    idx = levels_present.index(best["level"])
    if idx + 1 < len(levels_present):
        next_level = levels_present[idx + 1]
        delta = next(d for d in path["deltas"] if d["from_level"] == best["level"] and d["to_level"] == next_level)
        next_level_skills = delta["skills"]
    else:
        next_level = None
        next_level_skills = []

    return {
        **base_response,
        "matched": True,
        "family": best["family"],
        "display": best["display"],
        "your_level": best["level"],
        "match_fraction": best["match_fraction"],
        "next_level": next_level,
        "next_level_skills": next_level_skills,
    }


# --------------------------------------------------------------------------
# GET /briefings/latest, GET /briefings -- the fully-automated weekly
# briefing (briefing.py). Every row is either source='llm' (drafted by
# Groq from a facts dict, then verified by briefing.py's assertion layer
# before publication) or source='template' (that layer blocked the draft,
# or Groq wasn't reachable at all -- a plain briefing built directly from
# the same facts dict instead). This API never distinguishes the two in
# terms of trustworthiness -- both are published only because every
# number and name in them is already independently verifiable via
# facts_json -- but blocked_reason is exposed for anyone who wants to see
# exactly why a given week fell back.
#
# superseded_by: a correction to an already-published week is a NEW row
# (see briefing.py --regenerate / storage.supersede_briefing), never an
# edit of the original -- the original stays exactly as published,
# permanently, with superseded_by set to point at its replacement.
# /briefings/latest surfaces only the current (non-superseded) row per
# week, and -- when that row IS itself a correction -- also returns
# `supersedes`, the original it replaced, so the page can disclose the
# correction plainly instead of silently swapping the text.
# --------------------------------------------------------------------------

BRIEFING_SUMMARY_FIELDS = (
    "id", "week_start", "created_at", "body", "source", "model_version", "blocked_reason", "superseded_by",
)


def _briefing_summary(row: dict) -> dict:
    return {k: row.get(k) for k in BRIEFING_SUMMARY_FIELDS}


@app.get("/briefings/latest")
@cached()
def briefings_latest():
    """The current briefing for the most recent week that has one, or
    has_briefing=False if none have been published yet -- an honest empty
    state, not a 404, since "no briefing yet" is an expected, normal
    condition (e.g. before the first Monday run). "Current" excludes any
    row with superseded_by set -- a corrected week surfaces its
    correction, not the original. If the surfaced row is itself a
    correction, `supersedes` carries the original it replaced (full body
    included) so the page can disclose that plainly, not None otherwise."""
    briefings = queries.get_briefings()
    current = [b for b in briefings if not b.get("superseded_by")]
    if not current:
        return {"has_briefing": False}
    latest = max(current, key=lambda r: r["week_start"])

    original = next((b for b in briefings if b.get("superseded_by") == latest["id"]), None)
    return {
        "has_briefing": True,
        **_briefing_summary(latest),
        "supersedes": _briefing_summary(original) if original else None,
    }


@app.get("/briefings")
@cached()
def briefings_list(limit: int = Query(default=12, ge=1, le=52)):
    """Every published briefing, superseded ones included, most recent
    week first (ties broken by created_at so a week's correction sorts
    ahead of the original it replaced) -- the full audit trail, summary
    fields only (facts_json omitted here -- it can be sizeable; fetch
    /briefings/latest for the full record of the current one)."""
    briefings = sorted(queries.get_briefings(), key=lambda r: (r["week_start"], r["created_at"]), reverse=True)
    rows = [_briefing_summary(r) for r in briefings[:limit]]
    return {"count": len(rows), "briefings": rows}


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
            "/backtest/summary", "/backtest/detail",
            "/insights/live", "/system/health",
            "/curriculum/alignment", "/curriculum/gaps",
            "/paths/{family}", "/paths/match",
            "/briefings/latest", "/briefings",
        ],
    }
