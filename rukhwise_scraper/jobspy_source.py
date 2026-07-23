"""Indeed and LinkedIn collection via python-jobspy.

Both sources run two streams each -- a blank search and a "data" keyword
search -- pooled and deduplicated by job_url before being handed back as
plain job dicts in the same shape mustakbil.py/rozee_parser.py produce
(consumed by storage.upsert_postings via its own _row_to_record mapping).

Indeed is treated as trustworthy-by-default (country_indeed="Pakistan" is
a real structural parameter of Indeed's own search, not just a location
string) -- no geo filter here. LinkedIn's own search has been observed
(see the standalone JobSpy experiment) to leak non-Pakistani listings
despite location="Pakistan", so its geo filter below is mandatory, not a
nice-to-have.

STALENESS (LinkedIn only): a posting's date_posted can be weeks old --
LinkedIn resurfaces old listings in search results far more than Indeed
does. Old postings are still stored here, posting_date preserved exactly
as reported. What must NOT happen anywhere downstream: treating
first_seen_at (always "when we collected it", same as every other
source) as a proxy for "when this job was actually posted" for LinkedIn
rows -- that conflation is accurate for Mustakbil/Indeed (collected
same-day, so first_seen_at ~= posting recency) but not for LinkedIn. See
the comments at api/main.py's system_health and forecast.py's week
bucketing, where this specifically matters.
"""

from __future__ import annotations

import time
import unicodedata
from datetime import date

import pandas as pd

from config import setup_logging

logger = setup_logging()

STALE_DAYS_THRESHOLD = 14
LINKEDIN_INTER_STREAM_SLEEP_SECONDS = 30

INDEED_STREAMS = [
    ("general", dict(site_name=["indeed"], search_term="", location="Pakistan",
                      country_indeed="Pakistan", results_wanted=100)),
    ("data_search", dict(site_name=["indeed"], search_term="data", location="Pakistan",
                          country_indeed="Pakistan", results_wanted=50)),
]

LINKEDIN_STREAMS = [
    ("general", dict(site_name=["linkedin"], search_term="", location="Pakistan",
                      results_wanted=50, linkedin_fetch_description=True)),
    ("data_search", dict(site_name=["linkedin"], search_term="data", location="Pakistan",
                          results_wanted=50, linkedin_fetch_description=True)),
]

# Major/mid-size Pakistani cities and districts, normalized (diacritics
# stripped, lowercased). jobspy's location strings are Geonames-flavored
# and sometimes carry diacritics ("Karāchi", "Islāmābād") or district/
# division names ("Malir District") rather than a plain city name, so
# matching is substring-based against this list, not exact-equality.
PK_PLACE_NAMES = frozenset({
    "karachi", "lahore", "islamabad", "rawalpindi", "faisalabad", "multan",
    "peshawar", "quetta", "gujranwala", "sialkot", "bahawalpur", "sargodha",
    "sukkur", "larkana", "hyderabad", "mardan", "kasur", "rahim yar khan",
    "sahiwal", "okara", "wah cantonment", "dera ghazi khan", "mingora",
    "nawabshah", "mirpur khas", "chiniot", "kamoke", "burewala", "jhang",
    "sheikhupura", "gujrat", "kotri", "khanewal", "hafizabad", "kohat",
    "jacobabad", "shikarpur", "muzaffargarh", "khanpur", "gojra",
    "mandi bahauddin", "abbottabad", "turbat", "muridke", "chishtian",
    "daska", "mianwali", "jaranwala", "kamalia", "kot addu", "khairpur",
    "dadu", "nowshera", "charsadda", "swabi", "bannu", "dera ismail khan",
    "chakwal", "attock", "vehari", "toba tek singh", "jhelum", "gwadar",
    "zhob", "chaman", "sibi", "loralai", "muzaffarabad", "mirpur",
    "rawalakot", "bagh", "gilgit", "skardu", "chitral", "malir",
    "korangi", "nankana sahib", "ferozewala", "pattoki", "depalpur",
    "shorkot", "layyah", "bhakkar", "narowal", "hasilpur", "kabirwala",
    "pakpattan", "arifwala", "shujaabad", "alipur", "jatoi", "rajanpur",
    "chichawatni", "wazirabad", "sadiqabad", "timergara", "batkhela",
    "kalat", "khuzdar", "usta muhammad", "pishin", "panjgur", "awaran",
    "islamabad capital territory",
})


def _strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_place(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return _strip_diacritics(s).strip().lower()


def _country_segment(location: str) -> str:
    parts = [p.strip() for p in location.split(",") if p.strip()]
    return parts[-1] if parts else ""


def location_resolves_to_pakistan(location) -> tuple[bool, str]:
    """(accepted, reason) -- reason is for the drop log, not branching."""
    if not isinstance(location, str) or not location.strip():
        return False, "blank location"
    country = normalize_place(_country_segment(location))
    if country in ("pakistan", "pk"):
        return True, "country segment"
    norm_full = normalize_place(location)
    if any(city in norm_full for city in PK_PLACE_NAMES):
        return True, "known city match"
    return False, "no PK signal in location"


def extract_city(location) -> str | None:
    if not isinstance(location, str) or not location.strip():
        return None
    first = location.split(",")[0].strip()
    return first or None


def _extract_posting_date(date_posted) -> str | None:
    if date_posted is None:
        return None
    try:
        if pd.isna(date_posted):
            return None
    except (TypeError, ValueError):
        pass
    d = date_posted.date() if hasattr(date_posted, "date") else date_posted
    return d.isoformat() if isinstance(d, date) else None


def _clean_optional(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _row_to_job(row, source: str, category: str, include_salary: bool) -> dict:
    # pandas represents missing values as float('nan') even in otherwise-
    # string columns (title/company/description included) -- a raw NaN is
    # not valid JSON and silently kills the whole upsert batch downstream
    # (Supabase's client rejects it wholesale, not just the bad row), so
    # every field pulled off the row goes through _clean_optional, not
    # just the ones that look numeric.
    job = {
        "source": source,
        "detail_url": _clean_optional(row.get("job_url")),
        "source_job_id": _clean_optional(row.get("id")),
        "title": _clean_optional(row.get("title")),
        "company": _clean_optional(row.get("company")),
        "city": extract_city(row.get("location")),
        "posting_date": _extract_posting_date(row.get("date_posted")),
        "experience": _clean_optional(row.get("experience_range")),
        "salary_min": None,
        "salary_max": None,
        "salary_raw": None,
        "currency": None,
        "skills": None,  # extraction runs from description via extract.py, not jobspy's own 'skills' column
        "description": _clean_optional(row.get("description")),
        "category": category,
    }
    if include_salary:
        # postings.salary_min/max are integer columns; jobspy hands back
        # numpy floats (600.0), and the upsert RPC's jsonb_to_recordset
        # rejects "600.0" as integer input syntax -- must be a real int
        # before it's ever serialized.
        salary_min = _clean_optional(row.get("min_amount"))
        salary_max = _clean_optional(row.get("max_amount"))
        job["salary_min"] = int(salary_min) if salary_min is not None else None
        job["salary_max"] = int(salary_max) if salary_max is not None else None
        job["currency"] = _clean_optional(row.get("currency"))
    return job


def _run_stream(site_label: str, category: str, kwargs: dict) -> tuple[pd.DataFrame | None, dict]:
    from jobspy import scrape_jobs

    requested = kwargs.get("results_wanted", 0)
    logger.info(f"{site_label} stream '{category}': requesting {requested} (search_term={kwargs.get('search_term')!r})")
    try:
        df = scrape_jobs(**kwargs)
        received = len(df)
    except Exception as exc:  # noqa: BLE001 -- one stream failing must not sink the other
        df = None
        received = 0
        logger.error(f"{site_label} stream '{category}' failed: {type(exc).__name__}: {exc}")
    logger.info(f"{site_label} stream '{category}': received {received}/{requested}")
    return df, {"category": category, "requested": requested, "received": received}


def _pool_and_dedupe_jobs(jobs: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for j in jobs:
        if j["detail_url"]:
            deduped[j["detail_url"]] = j  # last occurrence wins, same convention as storage.upsert_postings
    return list(deduped.values())


def fetch_indeed_jobs() -> dict:
    """Both Indeed streams, pooled + deduped by job_url. Salary fields are
    always null (0% coverage observed in the standalone experiment; Indeed
    simply doesn't expose it for this market)."""
    all_jobs: list[dict] = []
    stream_results = []
    for category, kwargs in INDEED_STREAMS:
        df, result = _run_stream("Indeed", category, kwargs)
        stream_results.append(result)
        if df is not None and len(df) > 0:
            all_jobs.extend(_row_to_job(row, "indeed", category, include_salary=False) for _, row in df.iterrows())

    jobs = _pool_and_dedupe_jobs(all_jobs)
    logger.info(f"Indeed: pooled_raw={len(all_jobs)} deduped={len(jobs)}")
    return {"jobs": jobs, "stream_results": stream_results}


def fetch_linkedin_jobs() -> dict:
    """Both LinkedIn streams, pooled + deduped by job_url, then the
    mandatory geo filter. Rows whose location doesn't resolve to Pakistan
    are dropped entirely and logged (count + the actual dropped location
    strings), not silently discarded."""
    all_jobs_with_location: list[tuple[dict, object]] = []
    stream_results = []

    for i, (category, kwargs) in enumerate(LINKEDIN_STREAMS):
        if i > 0:
            logger.info(f"Sleeping {LINKEDIN_INTER_STREAM_SLEEP_SECONDS}s between LinkedIn streams...")
            time.sleep(LINKEDIN_INTER_STREAM_SLEEP_SECONDS)

        df, result = _run_stream("LinkedIn", category, kwargs)
        stream_results.append(result)
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                job = _row_to_job(row, "linkedin", category, include_salary=True)
                all_jobs_with_location.append((job, row.get("location")))

    # Dedup by job_url, keeping the paired raw location alongside.
    deduped: dict[str, tuple[dict, object]] = {}
    for job, location in all_jobs_with_location:
        if job["detail_url"]:
            deduped[job["detail_url"]] = (job, location)
    pooled = list(deduped.values())

    kept: list[dict] = []
    dropped_locations: list[str] = []
    for job, location in pooled:
        ok, _reason = location_resolves_to_pakistan(location)
        if ok:
            kept.append(job)
        else:
            dropped_locations.append(str(location) if location else "(blank)")

    if dropped_locations:
        logger.warning(
            f"LinkedIn geo-filter: dropped {len(dropped_locations)} of {len(pooled)} row(s) "
            f"not resolving to Pakistan"
        )
        for loc in dropped_locations:
            logger.warning(f"  dropped location: {loc!r}")
    else:
        logger.info(f"LinkedIn geo-filter: all {len(pooled)} row(s) resolved to Pakistan")

    stale_count = 0
    for job in kept:
        if job["posting_date"]:
            age_days = (date.today() - date.fromisoformat(job["posting_date"])).days
            if age_days > STALE_DAYS_THRESHOLD:
                stale_count += 1
    if stale_count:
        logger.info(
            f"LinkedIn: {stale_count} of {len(kept)} kept row(s) have posting_date older than "
            f"{STALE_DAYS_THRESHOLD} days -- stored as-is (posting_date preserved, not dropped). "
            f"first_seen_at for these rows will be today, NOT the posting's real age -- see module docstring."
        )

    logger.info(f"LinkedIn: pooled_raw={len(pooled)} kept_after_geo_filter={len(kept)}")
    return {
        "jobs": kept,
        "stream_results": stream_results,
        "dropped_count": len(dropped_locations),
        "dropped_locations": dropped_locations,
        "stale_count": stale_count,
    }
