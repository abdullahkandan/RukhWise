"""All Supabase reads live here, and only here -- main.py never touches the
Supabase client directly. This is a read-only service: it authenticates
with SUPABASE_ANON_KEY, never the service-role key, because it's meant to
be deployed publicly. If Row Level Security isn't already enabled on these
tables, the anon key can read everything with no policy at all (RLS off =
unrestricted) -- which is *worse* than what we want (it also means anon
could write). See the RLS SQL printed by this module's __main__ block.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Same reasoning as main.py: makes `from cache import cached` resolve
# whether this module is loaded as top-level `queries` or as `api.queries`.
# A no-op in the common case (main.py already put this directory on
# sys.path before importing queries), but keeps this module independently
# runnable too -- e.g. `python -m api.queries` for the RLS SQL printout.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml
from dotenv import load_dotenv
from supabase import Client, create_client

from cache import cached

_PROJECT_ROOT = Path(__file__).parent.parent
_TAXONOMY_PATH = _PROJECT_ROOT / "rukhwise_scraper" / "taxonomy_v1.yaml"

load_dotenv(_PROJECT_ROOT / ".env")

_QUERY_PAGE_SIZE = 1000

POSTING_COLUMNS = (
    "id,source,category,title,company,city,posting_date,experience_raw,"
    "salary_min,salary_max,salary_raw,currency,detail_url,skills_raw,"
    "first_seen_at,last_seen_at,scrape_run_id"
)

# --- RLS policy SQL -- print via `python queries.py` -----------------------

RLS_SQL = """\
alter table postings enable row level security;
alter table skill_mentions enable row level security;
alter table forecasts enable row level security;

drop policy if exists "public read" on postings;
create policy "public read" on postings for select using (true);

drop policy if exists "public read" on skill_mentions;
create policy "public read" on skill_mentions for select using (true);

drop policy if exists "public read" on forecasts;
create policy "public read" on forecasts for select using (true);
"""


def _get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_ANON_KEY"]
    return create_client(url, key)


def _fetch_all(table: str, columns: str, filters: dict | None = None) -> list[dict]:
    client = _get_client()
    rows: list[dict] = []
    offset = 0
    while True:
        query = client.table(table).select(columns)
        for col, val in (filters or {}).items():
            query = query.eq(col, val)
        res = query.range(offset, offset + _QUERY_PAGE_SIZE - 1).execute()
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


@cached()
def get_postings() -> list[dict]:
    """Every posting, all columns any endpoint needs. Cached -- this is the
    hot path nearly every endpoint builds on."""
    return _fetch_all("postings", POSTING_COLUMNS)


@cached()
def get_skill_mentions() -> list[dict]:
    """Every skill_mentions row (posting_id, skill, category, extraction_method)."""
    return _fetch_all("skill_mentions", "posting_id,skill,category,extraction_method")


@cached()
def get_taxonomy() -> dict:
    """Not a Supabase read, but still centralized here -- both queries.py
    and main.py need skill display names/categories, and this is the one
    place that touches the filesystem for it."""
    with open(_TAXONOMY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


FORECAST_COLUMNS = (
    "id,run_id,created_at,model_version,target_type,target_key,target_week_start,"
    "predicted,interval_low,interval_high,baseline_predicted,actual,graded_at,"
    "abs_error,baseline_abs_error,beat_baseline,source_scope"
)


@cached()
def get_forecasts() -> list[dict]:
    """Every forecasts row, pending and graded alike -- main.py splits by
    graded_at itself. Written only by forecast.py (service-role key,
    outside this read-only API); this is a plain public-read select, same
    as every other table here."""
    return _fetch_all("forecasts", FORECAST_COLUMNS)


if __name__ == "__main__":
    print(RLS_SQL)
