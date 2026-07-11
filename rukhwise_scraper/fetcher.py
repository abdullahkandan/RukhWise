"""Page-fetching layer: owns the browser, retries, delays, and stealth.

Kept separate from parser.py on purpose -- when Rozee changes their HTML,
only parser.py should need to change. This module only ever hands back
raw HTML strings.
"""

import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

from config import (
    USER_AGENT,
    MAX_RETRIES,
    BACKOFF_BASE_SECONDS,
    PAGE_LOAD_TIMEOUT_MS,
    random_delay,
    setup_logging,
)

logger = setup_logging()


class Fetcher:
    """Wraps a single stealth-patched Playwright browser/context/page."""

    def __init__(self, headless: bool = True):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._headless = headless
        self._stealth = Stealth()

    def __enter__(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        self._stealth.apply_stealth_sync(self._context)
        self._page = self._context.new_page()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def fetch(self, url: str) -> str | None:
        """Fetch a URL and return its rendered HTML, or None after exhausting retries."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
                # Give Cloudflare's managed challenge a moment to resolve/redirect.
                self._page.wait_for_timeout(2500)
                html = self._page.content()

                if _looks_like_challenge_page(html):
                    logger.warning(f"Challenge page detected on attempt {attempt} for {url}")
                    raise RuntimeError("cloudflare challenge page")

                logger.info(f"Fetched OK: {url}")
                delay = random_delay()
                time.sleep(delay)
                return html

            except (PlaywrightTimeoutError, RuntimeError) as exc:
                logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed for {url}: {exc}")
                if attempt == MAX_RETRIES:
                    logger.error(f"Giving up on {url} after {MAX_RETRIES} attempts")
                    return None
                backoff = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                time.sleep(backoff)

        return None


def _looks_like_challenge_page(html: str) -> bool:
    return "<title>Just a moment" in html or "id=\"challenge-error-text\"" in html
