"""Supabase storage layer for scraped postings.

Kept separate from the scrapers -- fetcher/parser modules only ever hand
back plain dicts; this module is the only place that talks to Supabase.
Credentials come from the environment (.env via python-dotenv) and are
never logged or printed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client, Client

from config import setup_logging

logger = setup_logging()

load_dotenv()

_BATCH_SIZE = 500

POSTINGS_TABLE_SQL = """\
create extension if not exists pgcrypto;

create table if not exists postings (
    id uuid primary key default gen_random_uuid(),
    source text not null,
    detail_url text not null,
    source_job_id text,
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null,
    title text,
    company text,
    city text,
    posting_date date,
    experience_raw text,
    salary_min integer,
    salary_max integer,
    salary_raw text,
    skills_raw jsonb,
    description text,
    scraped_at timestamptz,
    scrape_run_id text,
    unique (source, detail_url)
);
"""

FORECASTS_TABLE_SQL = """\
create table if not exists forecasts (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz default now(),
    target_metric text not null,
    target_period_start date,
    target_period_end date,
    predicted_value numeric,
    model_version text,
    actual_value numeric,
    graded_at timestamptz,
    error numeric
);
"""

# PostgREST's built-in .upsert() replaces every column it's given on conflict --
# there's no way to tell it "only touch these three columns." A plpgsql function
# is the only way to get selective-column-on-conflict AND single-round-trip
# batching (one RPC call inserts/updates the whole batch in one statement).
#
# `returning (xmax = 0)` is the standard Postgres idiom for telling inserted
# rows (xmax = 0, no prior transaction touched them) apart from rows that hit
# the ON CONFLICT DO UPDATE branch (xmax gets set to the updating transaction).
UPSERT_FUNCTION_SQL = """\
create or replace function upsert_postings_batch(payload jsonb)
returns table(inserted_count integer, updated_count integer)
language plpgsql
as $$
begin
    return query
    with incoming as (
        select *
        from jsonb_to_recordset(payload) as x(
            source text,
            detail_url text,
            source_job_id text,
            first_seen_at timestamptz,
            last_seen_at timestamptz,
            title text,
            company text,
            city text,
            posting_date date,
            experience_raw text,
            salary_min integer,
            salary_max integer,
            salary_raw text,
            skills_raw jsonb,
            description text,
            scraped_at timestamptz,
            scrape_run_id text
        )
    ),
    upserted as (
        insert into postings as p (
            source, detail_url, source_job_id, first_seen_at, last_seen_at,
            title, company, city, posting_date, experience_raw,
            salary_min, salary_max, salary_raw, skills_raw, description,
            scraped_at, scrape_run_id
        )
        select
            source, detail_url, source_job_id, first_seen_at, last_seen_at,
            title, company, city, posting_date, experience_raw,
            salary_min, salary_max, salary_raw, skills_raw, description,
            scraped_at, scrape_run_id
        from incoming
        on conflict (source, detail_url) do update set
            last_seen_at   = excluded.last_seen_at,
            scraped_at     = excluded.scraped_at,
            scrape_run_id  = excluded.scrape_run_id
        returning (xmax = 0) as was_insert
    )
    select
        count(*) filter (where was_insert)::integer,
        count(*) filter (where not was_insert)::integer
    from upserted;
end;
$$;
"""

ALL_SQL = "\n".join([POSTINGS_TABLE_SQL, FORECASTS_TABLE_SQL, UPSERT_FUNCTION_SQL])


def print_schema_sql() -> None:
    print(ALL_SQL)


def _get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def _normalize_posting_date(value):
    """Best-effort convert to ISO date; bad/unparseable input becomes None
    rather than risking the whole batch statement on one malformed row."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    try:
        # Handles full ISO datetimes too, e.g. Mustakbil's "2026-07-07T08:22:39"
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    logger.warning(f"Could not parse posting_date '{value}', storing as null")
    return None


def _row_to_record(row: dict, run_id: str, now_iso: str) -> dict | None:
    source = row.get("source")
    detail_url = row.get("detail_url")
    if not source or not detail_url:
        return None
    return {
        "source": source,
        "detail_url": detail_url,
        "source_job_id": row.get("source_job_id"),
        "first_seen_at": now_iso,
        "last_seen_at": now_iso,
        "title": row.get("title"),
        "company": row.get("company"),
        "city": row.get("city"),
        "posting_date": _normalize_posting_date(row.get("posting_date")),
        "experience_raw": row.get("experience"),
        "salary_min": row.get("salary_min"),
        "salary_max": row.get("salary_max"),
        "salary_raw": row.get("salary_raw"),
        "skills_raw": row.get("skills") or [],
        "description": row.get("description"),
        "scraped_at": row.get("scraped_at") or now_iso,
        "scrape_run_id": run_id,
    }


def upsert_postings(rows: list[dict], run_id: str) -> dict:
    """Upsert parsed job rows into `postings`, batched via one RPC call per batch.

    On conflict (source, detail_url): only last_seen_at, scraped_at, and
    scrape_run_id are updated. first_seen_at and the originally scraped
    fields are never overwritten by a later crawl of the same posting.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    records_by_key: dict[tuple[str, str], dict] = {}
    failed = 0
    duplicates = 0
    for row in rows:
        record = _row_to_record(row, run_id, now_iso)
        if record is None:
            failed += 1
            logger.warning(f"Skipping row with missing source/detail_url: {row.get('title')!r}")
            continue
        key = (record["source"], record["detail_url"])
        if key in records_by_key:
            duplicates += 1
        # Last occurrence wins -- a single INSERT...ON CONFLICT statement can't
        # touch the same (source, detail_url) row twice, and pagination can
        # legitimately return the same posting on two pages if the underlying
        # listing shifted between requests.
        records_by_key[key] = record

    if duplicates:
        logger.warning(f"Deduplicated {duplicates} repeated (source, detail_url) rows within this batch")

    records = list(records_by_key.values())

    inserted = 0
    updated = 0

    if records:
        client = _get_client()
        for i in range(0, len(records), _BATCH_SIZE):
            batch = records[i : i + _BATCH_SIZE]
            try:
                result = client.rpc("upsert_postings_batch", {"payload": batch}).execute()
                data = result.data or []
                if data:
                    inserted += data[0].get("inserted_count", 0) or 0
                    updated += data[0].get("updated_count", 0) or 0
            except Exception as exc:
                logger.error(f"Batch upsert failed ({len(batch)} rows): {exc}")
                failed += len(batch)

    logger.info(
        f"upsert_postings: inserted={inserted} updated={updated} failed={failed} (run_id={run_id})"
    )
    return {"inserted": inserted, "updated": updated, "failed": failed}


if __name__ == "__main__":
    print_schema_sql()
