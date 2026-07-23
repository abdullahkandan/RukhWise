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

  python collect.py --source indeed
      JobSpy (blank + 'data' keyword streams, pooled/deduped). Automated,
      in collect.yml's daily schedule -- wrapped so a failure there doesn't
      fail the whole job, since Indeed's tolerance of GitHub's datacenter
      IPs hasn't been proven yet at time of writing.

  python collect.py --source linkedin
      JobSpy, same two-stream shape, plus a mandatory geo filter (LinkedIn's
      own location search leaks non-Pakistani listings) and 30s between
      streams. Local/best-effort only, deliberately NOT in collect.yml --
      run this by hand, same as Rozee's --live path.

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


def _extract_skills_for_run(run_id: str) -> dict:
    """Skill extraction over every posting this run touched (inserted or
    updated -- get_postings_for_extraction(run_id) naturally covers both,
    since upsert_postings stamps scrape_run_id on both). Safe to call even
    if nothing new happened; re-extraction is a no-op via ON CONFLICT DO
    NOTHING in storage.store_skill_mentions()."""
    from extract import run_extraction

    return run_extraction(run_id=run_id)


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
    extract_result = _extract_skills_for_run(run_id)

    _log_summary(
        run_id=run_id,
        source="mustakbil",
        pages_fetched=pages_fetched,
        postings_parsed=len(jobs),
        counts=counts,
        enrich_result=enrich_result,
        extract_result=extract_result,
    )


def run_indeed(run_id: str) -> None:
    """Two JobSpy streams (blank + 'data'), pooled + deduped by job_url in
    jobspy_source.py. Trusted enough for the daily schedule (see
    collect.yml) but still wrapped in try/continue-on-error there until a
    workflow_dispatch run confirms Indeed survives GitHub's datacenter IPs."""
    from jobspy_source import fetch_indeed_jobs
    from storage import upsert_postings

    logger.info(f"[{run_id}] Indeed collection starting (JobSpy, 2 streams)")
    result = fetch_indeed_jobs()
    jobs = result["jobs"]
    for stream in result["stream_results"]:
        logger.info(
            f"[{run_id}] Indeed stream '{stream['category']}': "
            f"requested={stream['requested']} received={stream['received']}"
        )

    counts = upsert_postings(jobs, run_id=run_id)
    extract_result = _extract_skills_for_run(run_id)

    _log_summary(
        run_id=run_id,
        source="indeed",
        pages_fetched=len(result["stream_results"]),  # streams, not pages -- kept for RUN SUMMARY consistency
        postings_parsed=len(jobs),
        counts=counts,
        extract_result=extract_result,
    )


def run_linkedin(run_id: str) -> None:
    """Two JobSpy streams, 30s slept between them (jobspy_source.py), then
    a MANDATORY geo filter -- LinkedIn's own location search has been
    observed to leak non-Pakistani listings even with location="Pakistan".
    Local/best-effort only, deliberately not in collect.yml -- run this
    by hand, same as Rozee's --live path."""
    from jobspy_source import STALE_DAYS_THRESHOLD, fetch_linkedin_jobs
    from storage import upsert_postings

    logger.info(f"[{run_id}] LinkedIn collection starting (JobSpy, 2 streams, local/best-effort)")
    result = fetch_linkedin_jobs()
    jobs = result["jobs"]
    for stream in result["stream_results"]:
        logger.info(
            f"[{run_id}] LinkedIn stream '{stream['category']}': "
            f"requested={stream['requested']} received={stream['received']}"
        )
    logger.info(
        f"[{run_id}] LinkedIn geo-filter: dropped={result['dropped_count']} of "
        f"{len(jobs) + result['dropped_count']} pooled row(s); "
        f"stale (>{STALE_DAYS_THRESHOLD}d old, still stored)={result['stale_count']}"
    )

    counts = upsert_postings(jobs, run_id=run_id)
    extract_result = _extract_skills_for_run(run_id)

    _log_summary(
        run_id=run_id,
        source="linkedin",
        pages_fetched=len(result["stream_results"]),  # streams, not pages -- kept for RUN SUMMARY consistency
        postings_parsed=len(jobs),
        counts=counts,
        extract_result=extract_result,
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


# Same card selector rozee_parser scopes to (real cards only, skeleton-loader
# placeholders excluded). NOTE: this must index into the NodeList rather than
# use a `:first-child`-style pseudo-class -- `:first-child` requires the card
# to literally be its parent's first DOM child, which fails (silently, no
# error, just an empty match) whenever anything else precedes it in
# div.jlist#jobs. That's what produced the earlier "href=None" sentinel: the
# selector matched nothing at all, not that the card's href was truly absent.
_FIRST_CARD_HREF_JS = """
    (() => {
        const cards = document.querySelectorAll('div.jlist#jobs > div.job');
        if (!cards.length) return null;
        const a = cards[0].querySelector('h3.s-18 a[href]');
        return a ? a.getAttribute('href') : null;
    })()
"""

ROZEE_SEARCH_URL_TEMPLATE = "https://www.rozee.pk/job/jsearch/q/data/fpn/{offset}"
ROZEE_CARDS_PER_PAGE = 20  # verified: fpn/0 and fpn/20 return distinct, non-overlapping card sets


def _get_first_card_href(page) -> str | None:
    try:
        return page.evaluate(_FIRST_CARD_HREF_JS)
    except Exception:
        return None


def _wait_for_new_first_card(page, previous_href: str | None, timeout_ms: int = 8000) -> bool:
    """Block until the first job card's href differs from previous_href, or
    timeout_ms elapses. Used as the settle condition after each goto() --
    Rozee's search results are server-rendered, but this still guards
    against reading the DOM mid-navigation. A timeout here is NOT
    automatically treated as failure by the caller: for direct fpn/N
    navigation (unlike the old click-Next approach) a same/unchanged first
    card can also mean the offset is genuinely past the last page, since
    Rozee may re-serve the same content rather than erroring. The caller
    inspects actual page state afterward to tell those apart.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        page.wait_for_function(
            """(prev) => {
                const cards = document.querySelectorAll('div.jlist#jobs > div.job');
                if (!cards.length) return false;
                const a = cards[0].querySelector('h3.s-18 a[href]');
                const href = a ? a.getAttribute('href') : null;
                return href !== null && href !== prev;
            }""",
            arg=previous_href,
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeoutError:
        return False


def run_rozee_live(max_pages: int, run_id: str, category: str | None = None) -> None:
    from playwright.sync_api import sync_playwright
    from rozee_parser import parse_listing_page
    from storage import upsert_postings

    logger.info(f"[{run_id}] Rozee live-CDP collection starting, max {max_pages} pages, category={category!r}")

    all_jobs: list[dict] = []
    pages_fetched = 0
    seen_urls: set[str] = set()
    stop_reason = "reached max_pages"
    previous_first_href: str | None = None

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
            offset = (page_num - 1) * ROZEE_CARDS_PER_PAGE
            url = ROZEE_SEARCH_URL_TEMPLATE.format(offset=offset)

            if page_num > 1:
                time.sleep(random.uniform(*ROZEE_LIVE_DELAY_RANGE))

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                stop_reason = f"navigation to {url} raised an exception: {exc}"
                break

            # Settle condition, not a hard pass/fail gate -- see docstring.
            # A timeout here just means we proceed to inspect whatever state
            # actually loaded, rather than assuming failure outright.
            _wait_for_new_first_card(page, previous_first_href, timeout_ms=8000)

            html = page.content()
            page_jobs = parse_listing_page(html)
            first_href = _get_first_card_href(page)
            first_title = page_jobs[0]["title"] if page_jobs else None
            logger.info(
                f"[{run_id}] Page {page_num} (fpn/{offset}): first card = {first_title!r} "
                f"href={first_href!r} ({len(page_jobs)} cards)"
            )

            if not page_jobs:
                stop_reason = f"fpn/{offset} returned 0 cards -- genuinely reached the end of results"
                break

            if page_num > 1 and first_href is not None and first_href == previous_first_href:
                stop_reason = (
                    f"fpn/{offset} returned the same first card as the previous offset "
                    f"-- genuinely reached the end of results (offset beyond available postings)"
                )
                break

            previous_first_href = first_href

            new_jobs = [j for j in page_jobs if j.get("detail_url") not in seen_urls]
            for j in new_jobs:
                j["source"] = "rozee"
                j["category"] = category
                seen_urls.add(j["detail_url"])

            pages_fetched += 1
            all_jobs.extend(new_jobs)
            logger.info(f"[{run_id}] Page {page_num}/{max_pages}: parsed {len(page_jobs)} cards, {len(new_jobs)} new")

            if page_num == max_pages:
                stop_reason = "reached max_pages"
                break

    logger.info(f"[{run_id}] Pagination stopped: {stop_reason}")
    logger.info(f"[{run_id}] Fetched {pages_fetched} pages, parsed {len(all_jobs)} postings")

    counts = upsert_postings(all_jobs, run_id=run_id)
    extract_result = _extract_skills_for_run(run_id)

    _log_summary(
        run_id=run_id,
        source="rozee",
        pages_fetched=pages_fetched,
        postings_parsed=len(all_jobs),
        counts=counts,
        extract_result=extract_result,
    )


def _log_summary(
    run_id: str,
    source: str,
    pages_fetched: int,
    postings_parsed: int,
    counts: dict,
    enrich_result: dict | None = None,
    extract_result: dict | None = None,
) -> None:
    msg = (
        f"[{run_id}] RUN SUMMARY source={source} pages_fetched={pages_fetched} "
        f"postings_parsed={postings_parsed} inserted={counts['inserted']} "
        f"updated={counts['updated']} failed={counts['failed']}"
    )
    if enrich_result is not None:
        msg += f" enriched={enrich_result['enriched']} enrich_failed={enrich_result['enrich_failed']}"
    if extract_result is not None:
        msg += (
            f" skill_mentions_inserted={extract_result['inserted']} "
            f"skill_mentions_skipped={extract_result['skipped']}"
        )
    logger.info(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise scraping + storage collector")
    parser.add_argument("--source", choices=["mustakbil", "rozee", "indeed", "linkedin"], default="mustakbil")
    parser.add_argument("--pages", type=int, default=5, help="Pages for Mustakbil (default 5)")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pages for Rozee --live")
    parser.add_argument("--live", action="store_true", help="Rozee CDP batch-enrichment mode")
    parser.add_argument("--url", default=MUSTAKBIL_DEFAULT_URL, help="Mustakbil start URL override (general feed only)")
    parser.add_argument(
        "--category", default=None,
        help="Category label stored into postings.category. For Mustakbil, 'it' "
             "additionally switches the feed itself (default: 'all'); any other "
             "value is just a label. For Rozee, purely a label (e.g. 'data_search' "
             "for a q/data-style search session) -- doesn't change what's fetched.",
    )
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
        category = args.category or "all"
        run_mustakbil(pages=args.pages, start_url=args.url, category=category, run_id=run_id)
    elif args.source == "rozee":
        if not args.live:
            logger.error("--source rozee requires --live (CDP-connected batch enrichment only)")
            sys.exit(1)
        run_rozee_live(max_pages=args.max_pages, run_id=run_id, category=args.category)
    elif args.source == "indeed":
        run_indeed(run_id=run_id)
    elif args.source == "linkedin":
        run_linkedin(run_id=run_id)


if __name__ == "__main__":
    main()
