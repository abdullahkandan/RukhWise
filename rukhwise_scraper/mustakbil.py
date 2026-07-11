"""Mustakbil.com job listing fetcher + parser.

Mustakbil loads without bot protection (even a bare request with no headers
gets 200) and is an Angular SPA that embeds its search results as a JSON
blob (`<script id="mustakbil-state">`) in the server-rendered HTML, rather
than as scrapable per-field DOM tags. The frontend listing route has no
working pagination -- query strings return 410, path-segment guesses 404 --
but the embedded blob reveals the real, independently-callable API behind
it (api.mustakbil.com/ws/jobs/search), which paginates cleanly and returns
an empty list past the last page.

Page 1 comes from the frontend URL the caller supplies (this is what
resolves whatever slug -- city, category, or both -- into concrete
categoryId/city/countryId params). Page 2 onward is fetched directly from
the API using those resolved params, so this works for any category/city
combination without hardcoding a slug-to-id mapping.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

import requests

from config import MAX_RETRIES, BACKOFF_BASE_SECONDS, USER_AGENT, random_delay, setup_logging

logger = setup_logging()

API_SEARCH_URL = "https://api.mustakbil.com/ws/jobs/search"
DETAIL_URL_TEMPLATE = "https://www.mustakbil.com/jobs/job/{id}"

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
        "skills": [],  # Mustakbil has no skills taxonomy -- always empty.
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


def fetch_search_pages(start_url: str, max_pages: int = 5) -> dict:
    """Iterate through result pages for a Mustakbil search, starting from a
    frontend listing URL, e.g.:
        https://www.mustakbil.com/jobs/pakistan/karachi/information-technology
        https://www.mustakbil.com/jobs/pakistan/lahore
        https://www.mustakbil.com/jobs/pakistan/sales
        https://www.mustakbil.com/jobs/pakistan

    Page 1 is parsed from that URL directly. Page 2+ is fetched from the
    resolved JSON API (api.mustakbil.com) using the same filters the
    frontend page resolved to, since the frontend route itself doesn't
    paginate. Stops early if a page comes back empty (reached the end) or
    a fetch fails after retries. 2-4s randomized delay between requests.

    Returns {"jobs": list[dict], "pages_fetched": int}.
    """
    all_jobs: list[dict] = []

    resp = _fetch(start_url)
    if resp is None:
        return {"jobs": all_jobs, "pages_fetched": 0}

    state = _extract_state(resp.text)
    if state is None:
        logger.warning(f"No mustakbil-state on {start_url} -- aborting")
        return {"jobs": all_jobs, "pages_fetched": 0}

    found = _find_search_payload(state)
    if found is None:
        logger.warning(f"No jobs/search payload resolved from {start_url} -- aborting")
        return {"jobs": all_jobs, "pages_fetched": 0}

    resolved_params, body = found
    page1_jobs = _parse_raw_jobs(body.get("list", []))
    logger.info(f"Page 1/{max_pages}: parsed {len(page1_jobs)} jobs from {start_url}")
    all_jobs.extend(page1_jobs)
    pages_fetched = 1

    if max_pages <= 1 or not page1_jobs:
        return {"jobs": all_jobs, "pages_fetched": pages_fetched}

    for page_num in range(2, max_pages + 1):
        time.sleep(random_delay())

        params = dict(resolved_params)
        params["page"] = page_num
        resp = _fetch(API_SEARCH_URL, params=params)
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

    logger.info(f"fetch_search_pages: {len(all_jobs)} total jobs from {pages_fetched} pages")
    return {"jobs": all_jobs, "pages_fetched": pages_fetched}
