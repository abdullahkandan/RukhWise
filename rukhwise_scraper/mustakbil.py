"""Mustakbil.com job listing fetcher + parser.

Mustakbil loads without bot protection (even a bare request with no headers
gets 200) and is an Angular SPA that embeds its search results as a JSON
blob (`<script id="mustakbil-state">`) in the server-rendered HTML, rather
than as scrapable per-field DOM tags. The frontend listing route has no
working pagination -- query strings return 410, path-segment guesses 404 --
but the embedded blob reveals the real, independently-callable API behind
it (api.mustakbil.com/ws/jobs/search), which paginates cleanly and returns
an empty list past the last page.

Page 1 of the *general* feed comes from the frontend URL the caller
supplies (this is what resolves whatever slug -- city, category, or both --
into concrete categoryId/city/countryId params). Page 2 onward is fetched
directly from the API using those resolved params. Category-only feeds
(e.g. IT) skip the frontend step entirely and hit the API directly for
every page, since some bare-category frontend routes 404 (e.g.
/jobs/pakistan/information-technology does, even though the same category
combined with a city works) while the API itself has no such restriction.

There's also a per-job detail endpoint (api.mustakbil.com/ws/jobs/job/{id})
that the search/listing endpoint doesn't expose: it has a fuller HTML
`description` and a `requiredSkills` field the listing endpoint lacks
entirely. fetch_job_detail()/enrich_jobs() pull that in as a deliberate
second pass, not the default listing fetch, since it's one request per job.
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

import requests
from bs4 import BeautifulSoup

from config import MAX_RETRIES, BACKOFF_BASE_SECONDS, USER_AGENT, random_delay, setup_logging

logger = setup_logging()

API_SEARCH_URL = "https://api.mustakbil.com/ws/jobs/search"
API_DETAIL_URL_TEMPLATE = "https://api.mustakbil.com/ws/jobs/job/{id}"
DETAIL_URL_TEMPLATE = "https://www.mustakbil.com/jobs/job/{id}"

COUNTRY_ID_PAKISTAN = 162
# Discovered via the SSR cache key for /jobs/pakistan/karachi/information-technology
# (httpCacheKey/ws/jobs/search/?categoryId=47&city=Karachi&countryId=162&page=1) and
# verified directly: categoryId=47 with no city filter returns only category=='IT'
# postings, spread across multiple cities.
CATEGORY_IDS = {"it": 47}

DETAIL_DELAY_RANGE = (1.5, 3.0)

_STATE_SCRIPT_RE = re.compile(r'<script[^>]*id="mustakbil-state"[^>]*>(.*?)</script>', re.S)
_CACHE_KEY_RE = re.compile(r"httpCacheKey/ws/jobs/search/\?(.*)")


def _fetch(url: str, params: dict | None = None) -> requests.Response | None:
    """GET with retry+backoff. Only a real browser User-Agent is sent --
    Mustakbil doesn't require anything more (verified: bare requests with
    zero headers also get 200)."""
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            if resp.status_code == 200:
                logger.info(f"Fetched OK: {resp.url}")
                return resp
            logger.warning(
                f"Attempt {attempt}/{MAX_RETRIES} got HTTP {resp.status_code} for {resp.url}"
            )
        except requests.RequestException as exc:
            logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed for {url}: {exc}")

        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

    logger.error(f"Giving up on {url} after {MAX_RETRIES} attempts")
    return None


def _extract_state(html: str) -> dict | None:
    m = _STATE_SCRIPT_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to decode mustakbil-state JSON: {exc}")
        return None


def _find_search_payload(state: dict) -> tuple[dict, dict] | None:
    """Returns (resolved_query_params, response_body) for the jobs/search
    entry in the state blob -- resolved_query_params is what the frontend
    page actually asked the API for (categoryId, city, countryId, ...)."""
    for key, value in state.items():
        m = _CACHE_KEY_RE.search(key)
        if m:
            params = {k: v[0] for k, v in parse_qs(m.group(1)).items()}
            return params, value.get("body", {})
    return None


def _parse_raw_jobs(raw_jobs: list[dict]) -> list[dict]:
    jobs = []
    for raw in raw_jobs:
        try:
            job = _parse_job(raw)
        except Exception as exc:
            logger.warning(f"Skipping malformed job record: {exc}")
            continue
        if job is not None:
            jobs.append(job)
    return jobs


def _parse_job(raw: dict) -> dict | None:
    job_id = raw.get("id")
    if job_id is None:
        return None
    return {
        "title": raw.get("title") or None,
        "company": raw.get("company") or None,
        "city": raw.get("city") or None,
        "posting_date": raw.get("postedOn") or None,
        "experience": raw.get("experienceLevel") or None,
        "salary_min": raw.get("salaryMin"),
        "salary_max": raw.get("salaryMax"),
        "skills": [],  # listing endpoint has no skills field -- see fetch_job_detail
        "detail_url": DETAIL_URL_TEMPLATE.format(id=job_id),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": "mustakbil",
        "description": raw.get("description") or None,
        "source_job_id": str(job_id),
    }


def parse_listing_page(html: str) -> list[dict]:
    """Parse one Mustakbil search-results page (frontend HTML) into job dicts.

    Same dict shape as rozee_parser.parse_listing_page, plus 'source',
    'description', and 'source_job_id' which are free on this site.
    """
    state = _extract_state(html)
    if state is None:
        logger.warning("No mustakbil-state blob found in page -- returning empty list")
        return []

    found = _find_search_payload(state)
    if found is None:
        logger.warning("No jobs/search entry found in mustakbil-state -- returning empty list")
        return []

    _params, body = found
    raw_jobs = body.get("list", [])
    jobs = _parse_raw_jobs(raw_jobs)
    logger.info(f"Parsed {len(jobs)} of {len(raw_jobs)} job records")
    return jobs


def _paginate_api(params: dict, max_pages: int, first_page_jobs: list[dict] | None = None) -> dict:
    """Shared pagination loop against api.mustakbil.com given a fixed/resolved
    set of query params. If first_page_jobs is given, page 1 is assumed
    already fetched elsewhere (the frontend-resolve path) and this only
    fetches page 2+; otherwise it fetches every page including page 1.
    2-4s randomized delay between requests. Stops on an empty page or a
    failed fetch. Returns {"jobs": list[dict], "pages_fetched": int}.
    """
    all_jobs = list(first_page_jobs) if first_page_jobs is not None else []
    pages_fetched = 1 if first_page_jobs is not None else 0
    start_page = 2 if first_page_jobs is not None else 1

    for page_num in range(start_page, max_pages + 1):
        if pages_fetched > 0:
            time.sleep(random_delay())

        p = dict(params)
        p["page"] = page_num
        resp = _fetch(API_SEARCH_URL, params=p)
        if resp is None:
            logger.warning(f"Page {page_num}: fetch failed after retries, stopping pagination")
            break

        try:
            payload = resp.json()
        except ValueError:
            logger.warning(f"Page {page_num}: response was not valid JSON, stopping pagination")
            break

        raw_jobs = payload.get("list", [])
        if not raw_jobs:
            logger.info(f"Page {page_num}: empty result list, reached the end")
            break

        pages_fetched += 1
        page_jobs = _parse_raw_jobs(raw_jobs)
        logger.info(f"Page {page_num}/{max_pages}: parsed {len(page_jobs)} jobs")
        all_jobs.extend(page_jobs)

    return {"jobs": all_jobs, "pages_fetched": pages_fetched}


def fetch_search_pages(start_url: str, max_pages: int = 5) -> dict:
    """Iterate through result pages for a Mustakbil search, starting from a
    frontend listing URL, e.g.:
        https://www.mustakbil.com/jobs/pakistan/karachi/information-technology
        https://www.mustakbil.com/jobs/pakistan/lahore
        https://www.mustakbil.com/jobs/pakistan/sales
        https://www.mustakbil.com/jobs/pakistan

    Page 1 is parsed from that URL directly. Page 2+ is fetched from the
    resolved JSON API using the same filters the frontend page resolved to.
    Returns {"jobs": list[dict], "pages_fetched": int}.
    """
    resp = _fetch(start_url)
    if resp is None:
        return {"jobs": [], "pages_fetched": 0}

    state = _extract_state(resp.text)
    if state is None:
        logger.warning(f"No mustakbil-state on {start_url} -- aborting")
        return {"jobs": [], "pages_fetched": 0}

    found = _find_search_payload(state)
    if found is None:
        logger.warning(f"No jobs/search payload resolved from {start_url} -- aborting")
        return {"jobs": [], "pages_fetched": 0}

    resolved_params, body = found
    page1_jobs = _parse_raw_jobs(body.get("list", []))
    logger.info(f"Page 1/{max_pages}: parsed {len(page1_jobs)} jobs from {start_url}")

    if max_pages <= 1 or not page1_jobs:
        return {"jobs": page1_jobs, "pages_fetched": 1 if page1_jobs else 0}

    result = _paginate_api(resolved_params, max_pages, first_page_jobs=page1_jobs)
    logger.info(f"fetch_search_pages: {len(result['jobs'])} total jobs from {result['pages_fetched']} pages")
    return result


def fetch_search_pages_by_category(category: str, max_pages: int = 5) -> dict:
    """Fetch a category feed directly from the API, all of Pakistan, no city
    filter. Bypasses the frontend-URL resolution step entirely -- some bare
    category slugs 404 on their own frontend route (IT does), so this calls
    the API directly for every page instead, using a known categoryId.
    """
    if category not in CATEGORY_IDS:
        raise ValueError(f"Unknown category {category!r}, known: {list(CATEGORY_IDS)}")

    params = {"categoryId": CATEGORY_IDS[category], "countryId": COUNTRY_ID_PAKISTAN}
    result = _paginate_api(params, max_pages)
    logger.info(
        f"fetch_search_pages_by_category({category!r}): {len(result['jobs'])} total jobs "
        f"from {result['pages_fetched']} pages"
    )
    return result


def _html_to_text(html: str | None) -> str | None:
    """Strip HTML to clean text, keeping paragraph/bullet structure as
    newlines instead of collapsing everything to one run-on line."""
    if not html or not html.strip():
        return None
    soup = BeautifulSoup(html, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "- ")
        li.append("\n")
    text = soup.get_text()
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines) or None


def fetch_job_detail(job_id: str) -> dict | None:
    """Fetch and parse one job's full detail from the per-job API endpoint.
    Returns {"description": str|None, "skills_raw": dict|None}, or None if
    the fetch/parse failed outright.
    """
    resp = _fetch(API_DETAIL_URL_TEMPLATE.format(id=job_id))
    if resp is None:
        return None

    try:
        payload = resp.json()
    except ValueError:
        logger.warning(f"Job {job_id}: detail response was not valid JSON")
        return None

    description = _html_to_text(payload.get("description"))
    required_skills_text = _html_to_text(payload.get("requiredSkills"))
    # Distinguishable from Rozee's tag-list skills_raw shape (a plain list of strings).
    skills_raw = {"required_skills_text": required_skills_text} if required_skills_text else None

    return {"description": description, "skills_raw": skills_raw}


def enrich_jobs(targets: list[dict]) -> dict:
    """Fetch full detail for a list of {"source_job_id", "detail_url", ...}
    dicts and merge in description/skills_raw. 1.5-3s delay between calls
    (separate, tighter range than listing-page pagination since these are
    much smaller individual requests).

    Returns {"enriched": list[dict], "enrich_failed": int}. Entries with no
    source_job_id, or whose detail fetch fails, count toward enrich_failed
    and are skipped rather than raising.
    """
    enriched = []
    failed = 0

    for i, target in enumerate(targets):
        job_id = target.get("source_job_id")
        if not job_id:
            logger.warning(f"Skipping enrichment target with no source_job_id: {target}")
            failed += 1
            continue

        if i > 0:
            time.sleep(random.uniform(*DETAIL_DELAY_RANGE))

        detail = fetch_job_detail(job_id)
        if detail is None:
            logger.warning(f"Enrichment failed for job {job_id}")
            failed += 1
            continue

        enriched.append({
            "source": "mustakbil",
            "detail_url": target.get("detail_url") or DETAIL_URL_TEMPLATE.format(id=job_id),
            "description": detail["description"],
            "skills_raw": detail["skills_raw"],
        })

    logger.info(f"enrich_jobs: {len(enriched)} enriched, {failed} failed, of {len(targets)} targets")
    return {"enriched": enriched, "enrich_failed": failed}
