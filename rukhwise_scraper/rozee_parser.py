"""Parser for Rozee.pk job search listing pages.

Kept separate from fetcher.py -- when Rozee changes their markup,
only this file should need to change. Never raises on a malformed
card; missing fields come back as None (or [] for skills).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from config import setup_logging

logger = setup_logging()

_SALARY_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([kKmM])?")


def parse_listing_page(html: str) -> list[dict]:
    """Parse one Rozee.pk job-search results page into a list of job dicts."""
    soup = BeautifulSoup(html, "lxml")
    # Scoped to the real results list -- Rozee also renders a handful of
    # skeleton-loader `div.job` placeholders inside `div.jlist.loading`
    # (no title, used for the pre-AJAX loading animation) that must be excluded.
    cards = soup.select("div.jlist#jobs > div.job")

    jobs = []
    skipped = 0
    for card in cards:
        try:
            job = _parse_card(card)
        except Exception as exc:
            logger.warning(f"Skipping malformed job card: {exc}")
            skipped += 1
            continue
        if job is None:
            skipped += 1
            continue
        jobs.append(job)

    logger.info(f"Parsed {len(jobs)} job cards ({skipped} skipped) out of {len(cards)} found")
    return jobs


def _parse_card(card) -> dict | None:
    title, detail_url = _extract_title_and_url(card)
    if title is None and detail_url is None:
        # Not a recognizable job card -- skip rather than emit an all-None row.
        return None

    company, city = _extract_company_and_city(card)
    salary_min, salary_max = _extract_salary(card)

    return {
        "title": title,
        "company": company,
        "city": city,
        "posting_date": _extract_text(card, 'span[data-original-title="Posted On"]'),
        "experience": _extract_text(card, "span.func-area-drn"),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "skills": _extract_skills(card),
        "detail_url": detail_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def _extract_title_and_url(card):
    try:
        h3 = card.select_one("h3.s-18")
        if h3 is None:
            return None, None
        title = h3.get("title") or h3.get_text(strip=True) or None
        a = h3.select_one("a[href]")
        url = _normalize_url(a["href"]) if a and a.get("href") else None
        return title, url
    except Exception as exc:
        logger.warning(f"Failed to extract title/url: {exc}")
        return None, None


def _normalize_url(href: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    return href


def _extract_company_and_city(card):
    try:
        cname = card.select_one("div.cname bdi")
        if cname is None:
            return None, None

        links = cname.select("a.display-inline")
        company = None
        if links:
            company = links[0].get_text(strip=True).rstrip(",").strip() or None

        city = None
        if len(links) >= 3:
            # Real city names render as their own <a>: [company, city, ", Pakistan"]
            city = links[1].get_text(strip=True) or None
        else:
            # Placeholder locations ("All Cities", "Multiple Cities") are a bare
            # text node between the company <a> and the ", Pakistan" <a>.
            direct_text = " ".join(
                s.strip() for s in cname.find_all(string=True, recursive=False) if s.strip()
            )
            city = direct_text or None

        return company, city
    except Exception as exc:
        logger.warning(f"Failed to extract company/city: {exc}")
        return None, None


def _extract_text(card, selector: str) -> str | None:
    try:
        el = card.select_one(selector)
        if el is None:
            return None
        return el.get_text(strip=True) or None
    except Exception as exc:
        logger.warning(f"Failed to extract '{selector}': {exc}")
        return None


def _extract_salary(card):
    try:
        el = card.select_one('span[data-original-title="Offer Salary - PKR"] span')
        if el is None:
            return None, None
        text = el.get_text(strip=True)
        values = [
            v for v in (_to_number(num, unit) for num, unit in _SALARY_TOKEN_RE.findall(text))
            if v is not None
        ]
        if not values:
            return None, None
        if len(values) == 1:
            return values[0], values[0]
        return min(values), max(values)
    except Exception as exc:
        logger.warning(f"Failed to extract salary: {exc}")
        return None, None


def _to_number(num_str: str, unit: str | None) -> int | None:
    try:
        value = float(num_str)
    except ValueError:
        return None
    if unit and unit.lower() == "k":
        value *= 1_000
    elif unit and unit.lower() == "m":
        value *= 1_000_000
    return int(value)


def _extract_skills(card) -> list[str]:
    try:
        tags = card.select("div.job-dtl span.label")
        return [s for s in (t.get_text(strip=True) for t in tags) if s]
    except Exception as exc:
        logger.warning(f"Failed to extract skills: {exc}")
        return []
