"""Rukhwise collection entrypoint.

  python collect.py                          # Mustakbil, general feed, 5 pages, auto-enrich new postings
  python collect.py --source mustakbil --pages 10
  python collect.py --source mustakbil --category it --pages 10
      IT-category feed instead of the general Pakistan feed.

  python collect.py --enrich-all
      One-time backfill: full-detail enrichment (description + skills_raw)
      for every Mustakbil posting already in Supabase with a null/short
      description, regardless of when it was collected.

  python collect.py --source rozee --live --max-pages 5
      Batch-enrichment path for Rozee: connects to an already-running Chrome
      over CDP on localhost:9222. You clear Cloudflare's challenge manually
      once in that browser, then this script paginates (by clicking "Next"
      -- Rozee's pagination is JS-only, no real page URLs) and harvests many
      pages in one sitting.

Every Mustakbil collection run (general or --category) auto-enriches only
the postings it newly inserted -- already-known postings aren't re-fetched
for detail on every run. --enrich-all is the separate, explicit path for
backfilling postings collected before enrichment existed, or any that
failed enrichment previously.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

MUSTAKBIL_DEFAULT_URL = "https://www.mustakbil.com/jobs/pakistan"
ROZEE_LIVE_DELAY_RANGE = (3.0, 5.0)


def make_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


def _enrich_new_postings(new_postings: list[dict], run_id: str) -> dict:
    """Fetch full detail for newly-inserted postings and write it in.
    Returns {"enriched": int, "enrich_failed": int}."""
    from mustakbil import enrich_jobs
    from storage import enrich_postings

    if not new_postings:
        logger.info(f"[{run_id}] No new postings to enrich")
        return {"enriched": 0, "enrich_failed": 0}

    logger.info(f"[{run_id}] Enriching {len(new_postings)} new postings with full detail")
    fetched = enrich_jobs(new_postings)
    store_result = enrich_postings(fetched["enriched"])
    return {
        "enriched": store_result["enriched"],
        "enrich_failed": fetched["enrich_failed"] + store_result["enrich_failed"],
    }


def run_mustakbil(pages: int, start_url: str, category: str, run_id: str) -> None:
    from mustakbil import fetch_search_pages, fetch_search_pages_by_category
    from storage import upsert_postings

    if category == "it":
        logger.info(f"[{run_id}] Mustakbil IT-category collection starting, max {pages} pages")
        result = fetch_search_pages_by_category("it", max_pages=pages)
    else:
        logger.info(f"[{run_id}] Mustakbil general collection starting: {start_url}, max {pages} pages")
        result = fetch_search_pages(start_url, max_pages=pages)

    jobs = result["jobs"]
    pages_fetched = result["pages_fetched"]
    for job in jobs:
        job["category"] = category

    logger.info(f"[{run_id}] Fetched {pages_fetched} pages, parsed {len(jobs)} postings")

    counts = upsert_postings(jobs, run_id=run_id)
    enrich_result = _enrich_new_postings(counts["new_postings"], run_id)

    _log_summary(
        run_id=run_id,
        source="mustakbil",
        pages_fetched=pages_fetched,
        postings_parsed=len(jobs),
        counts=counts,
        enrich_result=enrich_result,
    )


def run_enrich_all(run_id: str) -> None:
    from mustakbil import enrich_jobs
    from storage import enrich_postings, get_postings_needing_enrichment

    targets = get_postings_needing_enrichment()
    logger.info(f"[{run_id}] --enrich-all: {len(targets)} Mustakbil postings with null/short description")

    if not targets:
        logger.info(f"[{run_id}] RUN SUMMARY source=mustakbil mode=enrich-all candidates=0 enriched=0 enrich_failed=0")
        return

    fetched = enrich_jobs(targets)
    store_result = enrich_postings(fetched["enriched"])
    enrich_failed = fetched["enrich_failed"] + store_result["enrich_failed"]

    logger.info(
        f"[{run_id}] RUN SUMMARY source=mustakbil mode=enrich-all candidates={len(targets)} "
        f"enriched={store_result['enriched']} enrich_failed={enrich_failed}"
    )


def run_rozee_live(max_pages: int, run_id: str) -> None:
    from playwright.sync_api import sync_playwright
    from rozee_parser import parse_listing_page
    from storage import upsert_postings

    logger.info(f"[{run_id}] Rozee live-CDP collection starting, max {max_pages} pages")

    all_jobs: list[dict] = []
    pages_fetched = 0
    seen_urls: set[str] = set()

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as exc:
            logger.error(
                f"[{run_id}] Could not connect to Chrome on localhost:9222: {exc}. "
                "Launch Chrome with --remote-debugging-port=9222, navigate to a Rozee "
                "search page, and clear the Cloudflare challenge manually first."
            )
            _log_summary(
                run_id=run_id, source="rozee",
                pages_fetched=0, postings_parsed=0,
                counts={"inserted": 0, "updated": 0, "failed": 0},
            )
            return

        context = browser.contexts[0]
        page = context.pages[0]
        logger.info(f"[{run_id}] Connected over CDP. Current URL: {page.url}")

        for page_num in range(1, max_pages + 1):
            html = page.content()
            page_jobs = parse_listing_page(html)

            new_jobs = [j for j in page_jobs if j.get("detail_url") not in seen_urls]
            for j in new_jobs:
                j["source"] = "rozee"
                seen_urls.add(j["detail_url"])

            if not new_jobs and page_num > 1:
                logger.info(f"[{run_id}] Page {page_num}: no new postings, stopping")
                break

            pages_fetched += 1
            all_jobs.extend(new_jobs)
            logger.info(f"[{run_id}] Page {page_num}/{max_pages}: parsed {len(new_jobs)} new postings")

            if page_num == max_pages:
                break

            next_link = page.locator("ul.pagination a.next")
            if next_link.count() == 0:
                logger.info(f"[{run_id}] No 'Next' control found, reached the end")
                break

            try:
                next_link.first.click()
                page.wait_for_timeout(2000)
            except Exception as exc:
                logger.warning(f"[{run_id}] Failed to click 'Next': {exc}")
                break

            time.sleep(random.uniform(*ROZEE_LIVE_DELAY_RANGE))

    logger.info(f"[{run_id}] Fetched {pages_fetched} pages, parsed {len(all_jobs)} postings")

    counts = upsert_postings(all_jobs, run_id=run_id)

    _log_summary(
        run_id=run_id,
        source="rozee",
        pages_fetched=pages_fetched,
        postings_parsed=len(all_jobs),
        counts=counts,
    )


def _log_summary(
    run_id: str,
    source: str,
    pages_fetched: int,
    postings_parsed: int,
    counts: dict,
    enrich_result: dict | None = None,
) -> None:
    msg = (
        f"[{run_id}] RUN SUMMARY source={source} pages_fetched={pages_fetched} "
        f"postings_parsed={postings_parsed} inserted={counts['inserted']} "
        f"updated={counts['updated']} failed={counts['failed']}"
    )
    if enrich_result is not None:
        msg += f" enriched={enrich_result['enriched']} enrich_failed={enrich_result['enrich_failed']}"
    logger.info(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise scraping + storage collector")
    parser.add_argument("--source", choices=["mustakbil", "rozee"], default="mustakbil")
    parser.add_argument("--pages", type=int, default=5, help="Pages for Mustakbil (default 5)")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pages for Rozee --live")
    parser.add_argument("--live", action="store_true", help="Rozee CDP batch-enrichment mode")
    parser.add_argument("--url", default=MUSTAKBIL_DEFAULT_URL, help="Mustakbil start URL override (general feed only)")
    parser.add_argument("--category", choices=["all", "it"], default="all", help="Mustakbil feed to collect")
    parser.add_argument("--enrich-all", action="store_true", help="Backfill full detail for existing null/short-description postings")
    args = parser.parse_args()

    run_id = make_run_id()

    if args.enrich_all:
        if args.source != "mustakbil":
            logger.error("--enrich-all only supports Mustakbil (its per-job detail endpoint)")
            sys.exit(1)
        run_enrich_all(run_id=run_id)
        return

    if args.source == "mustakbil":
        run_mustakbil(pages=args.pages, start_url=args.url, category=args.category, run_id=run_id)
    elif args.source == "rozee":
        if not args.live:
            logger.error("--source rozee requires --live (CDP-connected batch enrichment only)")
            sys.exit(1)
        run_rozee_live(max_pages=args.max_pages, run_id=run_id)


if __name__ == "__main__":
    main()
