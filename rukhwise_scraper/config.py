"""Shared configuration for the Rozee.pk feasibility scraper."""

import logging
import random

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MIN_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 4.0

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 3.0  # backoff = BACKOFF_BASE_SECONDS * 2**attempt

PAGE_LOAD_TIMEOUT_MS = 30_000

OUTPUT_CSV_PATH = "output/rozee_postings.csv"
LOG_PATH = "output/scrape.log"


def random_delay() -> float:
    return random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("rukhwise_scraper")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    import os
    os.makedirs("output", exist_ok=True)
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
