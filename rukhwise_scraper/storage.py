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
_QUERY_PAGE_SIZE = 1000

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
    category text,
    unique (source, detail_url)
);

-- Idempotent: safe to re-run against a database that already has the table
-- (adds the column added for multi-stream collection) or a brand new one
-- (already created above, this is then a no-op).
alter table postings add column if not exists category text;
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

SKILL_MENTIONS_TABLE_SQL = """\
create table if not exists skill_mentions (
    id uuid primary key default gen_random_uuid(),
    posting_id uuid references postings(id),
    skill text not null,
    category text not null,
    extraction_method text not null,
    extracted_at timestamptz default now(),
    unique(posting_id, skill, extraction_method)
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
#
# Returns one row per upserted record (not aggregated) so the caller can tell
# exactly which postings were newly inserted -- that list drives enrichment
# (only fetch full detail for postings we haven't already enriched before).
#
# category on conflict: prefer the more specific value. A posting first seen
# via the general feed (category='all') that later also turns up in the IT
# feed (category='it') should end up tagged 'it'; the reverse should not
# demote it back to 'all'. Null counts as least specific of all.
#
# Return type changed from the original (aggregated counts) version, so the
# old function must be dropped first -- `create or replace` can't change a
# function's return table shape in place.
DROP_OLD_UPSERT_FUNCTION_SQL = """\
drop function if exists upsert_postings_batch(jsonb);
"""

UPSERT_FUNCTION_SQL = """\
create function upsert_postings_batch(payload jsonb)
returns table(source text, detail_url text, source_job_id text, was_insert boolean)
language plpgsql
as $$
#variable_conflict use_column
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
            scrape_run_id text,
            category text
        )
    ),
    upserted as (
        insert into postings as p (
            source, detail_url, source_job_id, first_seen_at, last_seen_at,
            title, company, city, posting_date, experience_raw,
            salary_min, salary_max, salary_raw, skills_raw, description,
            scraped_at, scrape_run_id, category
        )
        select
            incoming.source, incoming.detail_url, incoming.source_job_id,
            incoming.first_seen_at, incoming.last_seen_at,
            incoming.title, incoming.company, incoming.city,
            incoming.posting_date, incoming.experience_raw,
            incoming.salary_min, incoming.salary_max, incoming.salary_raw,
            incoming.skills_raw, incoming.description,
            incoming.scraped_at, incoming.scrape_run_id, incoming.category
        from incoming
        on conflict (source, detail_url) do update set
            last_seen_at   = excluded.last_seen_at,
            scraped_at     = excluded.scraped_at,
            scrape_run_id  = excluded.scrape_run_id,
            category       = case
                when excluded.category is not null
                     and (p.category is null or p.category = 'all')
                    then excluded.category
                else p.category
            end
        returning p.source, p.detail_url, p.source_job_id, (xmax = 0) as was_insert
    )
    select upserted.source, upserted.detail_url, upserted.source_job_id, upserted.was_insert
    from upserted;
end;
$$;
"""

# Targeted content upgrade, separate from upsert_postings_batch on purpose --
# enrichment never touches first_seen_at/last_seen_at/category/scrape_run_id,
# it only replaces description/skills_raw for postings that already exist.
# coalesce() so a row with a genuinely-empty detail-page field doesn't null
# out a perfectly good value the listing endpoint already gave us.
ENRICH_FUNCTION_SQL = """\
create or replace function enrich_postings_batch(payload jsonb)
returns table(detail_url text)
language plpgsql
as $$
#variable_conflict use_column
begin
    return query
    with incoming as (
        select *
        from jsonb_to_recordset(payload) as x(
            source text,
            detail_url text,
            description text,
            skills_raw jsonb
        )
    ),
    upd as (
        update postings p
        set description = coalesce(incoming.description, p.description),
            skills_raw   = coalesce(incoming.skills_raw, p.skills_raw)
        from incoming
        where p.source = incoming.source and p.detail_url = incoming.detail_url
        returning p.detail_url
    )
    select upd.detail_url from upd;
end;
$$;
"""

ALL_SQL = "\n".join([
    POSTINGS_TABLE_SQL,
    FORECASTS_TABLE_SQL,
    SKILL_MENTIONS_TABLE_SQL,
    DROP_OLD_UPSERT_FUNCTION_SQL,
    UPSERT_FUNCTION_SQL,
    ENRICH_FUNCTION_SQL,
])


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
        "category": row.get("category"),
    }


def upsert_postings(rows: list[dict], run_id: str) -> dict:
    """Upsert parsed job rows into `postings`, batched via one RPC call per batch.

    On conflict (source, detail_url): only last_seen_at, scraped_at,
    scrape_run_id, and (conditionally) category are updated. first_seen_at
    and the originally scraped fields are never overwritten by a later
    crawl of the same posting.

    Returns {"inserted": int, "updated": int, "failed": int,
    "new_postings": list[{"source", "detail_url", "source_job_id"}]} --
    new_postings is exactly the rows that were newly inserted this call,
    for the caller to enrich.
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
    new_postings = []

    if records:
        client = _get_client()
        for i in range(0, len(records), _BATCH_SIZE):
            batch = records[i : i + _BATCH_SIZE]
            try:
                result = client.rpc("upsert_postings_batch", {"payload": batch}).execute()
                for row in result.data or []:
                    if row.get("was_insert"):
                        inserted += 1
                        new_postings.append({
                            "source": row["source"],
                            "detail_url": row["detail_url"],
                            "source_job_id": row["source_job_id"],
                        })
                    else:
                        updated += 1
            except Exception as exc:
                logger.error(f"Batch upsert failed ({len(batch)} rows): {exc}")
                failed += len(batch)

    logger.info(
        f"upsert_postings: inserted={inserted} updated={updated} failed={failed} (run_id={run_id})"
    )
    return {"inserted": inserted, "updated": updated, "failed": failed, "new_postings": new_postings}


def enrich_postings(rows: list[dict]) -> dict:
    """Write fetched description/skills_raw into existing postings, matched
    on (source, detail_url). Does not touch first_seen_at/last_seen_at/
    category/scrape_run_id -- this is a content upgrade, not a re-crawl.

    `rows` are expected to already contain fetched 'description' and
    'skills_raw' (e.g. from mustakbil.enrich_jobs()'s output), not raw
    listing rows.

    Returns {"enriched": int, "enrich_failed": int}.
    """
    records = []
    for row in rows:
        source = row.get("source")
        detail_url = row.get("detail_url")
        if not source or not detail_url:
            continue
        records.append({
            "source": source,
            "detail_url": detail_url,
            "description": row.get("description"),
            "skills_raw": row.get("skills_raw"),
        })

    enriched = 0
    enrich_failed = len(rows) - len(records)

    if records:
        client = _get_client()
        for i in range(0, len(records), _BATCH_SIZE):
            batch = records[i : i + _BATCH_SIZE]
            try:
                result = client.rpc("enrich_postings_batch", {"payload": batch}).execute()
                matched = len(result.data or [])
                enriched += matched
                enrich_failed += len(batch) - matched
            except Exception as exc:
                logger.error(f"Batch enrichment failed ({len(batch)} rows): {exc}")
                enrich_failed += len(batch)

    logger.info(f"enrich_postings: enriched={enriched} enrich_failed={enrich_failed}")
    return {"enriched": enriched, "enrich_failed": enrich_failed}


def _skills_raw_empty(skills_raw) -> bool:
    if not skills_raw:
        return True
    if isinstance(skills_raw, dict):
        return not skills_raw.get("required_skills_text")
    return False


def get_postings_needing_enrichment(word_threshold: int = 120) -> list[dict]:
    """Every Mustakbil posting currently in Supabase with a null/short
    (<word_threshold words) description OR an empty skills_raw -- the
    one-time backfill target set for --enrich-all.

    120 words separates the two populations we actually have: listing-
    derived descriptions top out around 100 words, detail-page descriptions
    run 79-226, so 120 catches "never enriched" without much false-positive
    overlap. skills_raw is checked separately because a listing-derived
    description can already clear 120 words while still having never been
    enriched (empty skills_raw is the more reliable "hasn't been enriched
    yet" signal on its own).

    Scoped to source='mustakbil' only -- detail enrichment is Mustakbil-
    API-specific, and Rozee rows legitimately have null descriptions until
    Rozee gets its own detail-enrichment path; they shouldn't be swept into
    a Mustakbil-only backfill.
    """
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("source_job_id,detail_url,description,skills_raw")
            .eq("source", "mustakbil")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE

    targets = []
    for r in rows:
        desc = r.get("description")
        desc_short = not desc or len(desc.split()) < word_threshold
        if desc_short or _skills_raw_empty(r.get("skills_raw")):
            targets.append({"source_job_id": r["source_job_id"], "detail_url": r["detail_url"]})
    return targets


def get_postings_for_extraction(run_id: str | None = None) -> list[dict]:
    """Postings to run skill extraction over: either every posting in
    Supabase (run_id=None, the --all backfill path) or just the ones from
    one scrape run (run_id='...', the normal per-collection path)."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        query = client.table("postings").select("id,title,description,skills_raw")
        if run_id is not None:
            query = query.eq("scrape_run_id", run_id)
        res = query.range(offset, offset + _QUERY_PAGE_SIZE - 1).execute()
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def store_skill_mentions(mentions: list[dict], extraction_method: str) -> dict:
    """Batch-insert skill mentions, ON CONFLICT (posting_id, skill,
    extraction_method) DO NOTHING -- re-running extraction over
    already-processed postings is always safe and cheap; it just no-ops on
    rows already recorded for that method.

    Each mention dict needs: posting_id, skill, category.
    Returns {"inserted": int, "skipped": int, "failed": int}.
    """
    records = []
    for m in mentions:
        posting_id = m.get("posting_id")
        skill = m.get("skill")
        category = m.get("category")
        if not posting_id or not skill or not category:
            continue
        records.append({
            "posting_id": posting_id,
            "skill": skill,
            "category": category,
            "extraction_method": extraction_method,
        })

    failed = len(mentions) - len(records)
    inserted = 0
    skipped = 0

    if records:
        client = _get_client()
        for i in range(0, len(records), _BATCH_SIZE):
            batch = records[i : i + _BATCH_SIZE]
            try:
                result = (
                    client.table("skill_mentions")
                    .upsert(
                        batch,
                        on_conflict="posting_id,skill,extraction_method",
                        ignore_duplicates=True,
                    )
                    .execute()
                )
                matched = len(result.data or [])
                inserted += matched
                skipped += len(batch) - matched
            except Exception as exc:
                logger.error(f"Batch skill_mentions insert failed ({len(batch)} rows): {exc}")
                failed += len(batch)

    logger.info(
        f"store_skill_mentions: inserted={inserted} skipped={skipped} failed={failed} "
        f"(extraction_method={extraction_method})"
    )
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    print_schema_sql()
