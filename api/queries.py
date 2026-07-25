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

# --- Single source of truth for which taxonomy pass is live -----------------
# P0 fix: this used to be implicit and inconsistent -- get_taxonomy() read
# taxonomy_v1.yaml while ~14 call sites across main.py fetched
# skill_mentions with no extraction_method filter at all, silently blending
# taxonomy_v1/v2/v3 mention rows together on every existing endpoint (v2/v3-
# only skill keys were invisible or mis-labeled everywhere except the
# curriculum endpoints, which had already been scoped to v3 by hand). Fixed
# by making this the ONE place that names the active pass: get_skill_mentions()
# below filters to it at the query layer (every caller gets a clean single-
# taxonomy view automatically, nothing left to remember), and get_taxonomy()
# loads its display/category names. Bump this one constant to move the whole
# API to a future taxonomy_v4 in one place.
ACTIVE_TAXONOMY = "taxonomy_v3"
_TAXONOMY_PATH = _PROJECT_ROOT / "rukhwise_scraper" / f"{ACTIVE_TAXONOMY}.yaml"
_JOB_FAMILIES_PATH = _PROJECT_ROOT / "rukhwise_scraper" / "job_families.yaml"

load_dotenv(_PROJECT_ROOT / ".env")

_QUERY_PAGE_SIZE = 1000

POSTING_COLUMNS = (
    "id,source,category,title,company,city,posting_date,experience_raw,"
    "salary_min,salary_max,salary_raw,currency,detail_url,skills_raw,"
    "first_seen_at,last_seen_at,scrape_run_id,domain,job_family,experience_level"
)

# --- RLS policy SQL -- print via `python queries.py` -----------------------

RLS_SQL = """\
alter table postings enable row level security;
alter table skill_mentions enable row level security;
alter table forecasts enable row level security;
alter table backtests enable row level security;
alter table curriculum_courses enable row level security;
alter table curriculum_skill_map enable row level security;
alter table briefings enable row level security;

drop policy if exists "public read" on postings;
create policy "public read" on postings for select using (true);

drop policy if exists "public read" on skill_mentions;
create policy "public read" on skill_mentions for select using (true);

drop policy if exists "public read" on forecasts;
create policy "public read" on forecasts for select using (true);

drop policy if exists "public read" on backtests;
create policy "public read" on backtests for select using (true);

drop policy if exists "public read" on curriculum_courses;
create policy "public read" on curriculum_courses for select using (true);

drop policy if exists "public read" on curriculum_skill_map;
create policy "public read" on curriculum_skill_map for select using (true);

drop policy if exists "public read" on briefings;
create policy "public read" on briefings for select using (true);
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
    """Every skill_mentions row for ACTIVE_TAXONOMY only (posting_id, skill,
    category, extraction_method) -- filtered here, at the query layer, so
    every one of main.py's callers gets a clean single-taxonomy view with
    nothing to remember. This is the fix for the taxonomy-blending bug: see
    ACTIVE_TAXONOMY's comment above for what was wrong before."""
    return _fetch_all(
        "skill_mentions", "posting_id,skill,category,extraction_method",
        filters={"extraction_method": ACTIVE_TAXONOMY},
    )


@cached()
def get_taxonomy() -> dict:
    """Display names/categories for ACTIVE_TAXONOMY. Not a Supabase read,
    but still centralized here -- both queries.py and main.py need this,
    and this is the one place that touches the filesystem for it."""
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


BACKTEST_COLUMNS = (
    "id,run_id,created_at,computed_at,is_retrospective,model_version,target_type,"
    "target_key,target_week_start,predicted,interval_low,interval_high,"
    "baseline_predicted,actual,abs_error,baseline_abs_error,outcome,beat_baseline,"
    "source_scope"
)


@cached()
def get_backtests() -> list[dict]:
    """Every backtests row -- a separate table from forecasts, written only
    by backtest.py (service-role key). Fully mutable/re-computed-from-
    scratch on every run, unlike forecasts' append-only history; see
    backtest.py's module docstring for why the two must never be mixed."""
    return _fetch_all("backtests", BACKTEST_COLUMNS)


CURRICULUM_COURSE_COLUMNS = "id,source_document,degree_program,course_code,course_title,credit_hours,topics_raw"


@cached()
def get_curriculum_courses() -> list[dict]:
    """Every curriculum_courses row -- see curriculum.py for how these
    were parsed from the HEC/NCEAC PDFs in data/curricula/."""
    return _fetch_all("curriculum_courses", CURRICULUM_COURSE_COLUMNS)


@cached()
def get_curriculum_skill_map() -> list[dict]:
    """Every curriculum_skill_map row (course_id, skill, match_source)."""
    return _fetch_all("curriculum_skill_map", "course_id,skill,match_source")


BRIEFING_COLUMNS = "id,week_start,created_at,body,source,facts_json,model_version,blocked_reason"


@cached()
def get_briefings() -> list[dict]:
    """Every briefings row -- written only by briefing.py (service-role
    key), immutable once inserted (see that table's trigger). Small,
    append-only, one row per week -- no pagination concerns in practice,
    but _fetch_all is used anyway for consistency with every other
    fetcher here."""
    return _fetch_all("briefings", BRIEFING_COLUMNS)


@cached()
def get_job_families() -> list[dict]:
    """The controlled job-family vocabulary (key, display, domain,
    keywords) -- see job_family_classifier.py for the module that
    actually classifies postings against it. Read directly from the
    YAML rather than importing that module, same reasoning as
    get_taxonomy(): this API package stays self-contained."""
    with open(_JOB_FAMILIES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["families"]


if __name__ == "__main__":
    print(RLS_SQL)
