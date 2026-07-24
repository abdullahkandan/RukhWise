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
    currency text,
    skills_raw jsonb,
    description text,
    scraped_at timestamptz,
    scrape_run_id text,
    category text,
    unique (source, detail_url)
);

-- Idempotent: safe to re-run against a database that already has the table
-- (adds columns added in later migrations) or a brand new one (already
-- created above, these are then no-ops).
alter table postings add column if not exists category text;
alter table postings add column if not exists currency text;

-- Taxonomy v2 structured extraction (see output/taxonomy_v2_spec.md
-- sections 2-3 and structured_extraction.py) -- credential/experience
-- facts, deliberately scalar columns here rather than skill_mentions
-- rows, since they're per-posting facts, not repeatable skill matches.
alter table postings add column if not exists degree_level text;
alter table postings add column if not exists degree_field text;
alter table postings add column if not exists has_certification boolean;
alter table postings add column if not exists experience_min_years integer;
alter table postings add column if not exists experience_max_years integer;
alter table postings add column if not exists experience_level text;

-- Three-stage domain classification (see domain_classifier.py /
-- domains.yaml). domain_method records which stage actually assigned the
-- value -- rule_title | rule_description | llm | unclassified -- so every
-- classification is auditable and re-runnable; domain_confidence is
-- 1.0 for stage-1 title matches, the actual count-based margin ratio for
-- stage-2 description matches, the LLM's own reported confidence for
-- stage 3, and null when a posting was never confidently classified.
alter table postings add column if not exists domain text;
alter table postings add column if not exists domain_method text;
alter table postings add column if not exists domain_confidence numeric;
"""

# The forecasts table's real schema/trigger/RLS lives in the dedicated
# migration SQL forecast.py's module docstring points to (output once,
# reviewed, and run by hand in the Supabase SQL editor -- forecasting
# needs an immutability trigger and a bigint PK, not the uuid/if-not-exists
# pattern the rest of this module uses for postings/skill_mentions).
# Nothing ever wrote through the old placeholder schema this replaced.

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

-- Idempotent: safe to re-run. Taxonomy v2 adds requirement_type
-- (skill | credential | experience | language | attribute) alongside
-- category on every mention -- default 'skill' is correct as-is for
-- every pre-v2 row, since every extraction_method='taxonomy_v1' mention
-- really was a plain skill match (v1 had no other requirement type).
alter table skill_mentions add column if not exists requirement_type text not null default 'skill';
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
            currency text,
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
            salary_min, salary_max, salary_raw, currency, skills_raw, description,
            scraped_at, scrape_run_id, category
        )
        select
            incoming.source, incoming.detail_url, incoming.source_job_id,
            incoming.first_seen_at, incoming.last_seen_at,
            incoming.title, incoming.company, incoming.city,
            incoming.posting_date, incoming.experience_raw,
            incoming.salary_min, incoming.salary_max, incoming.salary_raw,
            incoming.currency, incoming.skills_raw, incoming.description,
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
# it only replaces description/skills_raw/currency for postings that already
# exist. coalesce() so a row with a genuinely-empty detail-page field doesn't
# null out a perfectly good value the listing endpoint already gave us.
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
            skills_raw jsonb,
            currency text
        )
    ),
    upd as (
        update postings p
        set description = coalesce(incoming.description, p.description),
            skills_raw   = coalesce(incoming.skills_raw, p.skills_raw),
            currency     = coalesce(incoming.currency, p.currency)
        from incoming
        where p.source = incoming.source and p.detail_url = incoming.detail_url
        returning p.detail_url
    )
    select upd.detail_url from upd;
end;
$$;
"""

# Batched write path for structured_extraction.py's output -- degree_level/
# degree_field/has_certification/experience_min_years/experience_max_years/
# experience_level, matched to postings by id. Mirrors enrich_postings_batch's
# shape (batched jsonb_to_recordset UPDATE, one RPC call per batch) but sets
# every field unconditionally rather than coalescing -- a re-run of
# extract.py --all is a full recomputation, so a field that no longer
# matches should be cleared, not left stale from a prior run.
UPDATE_POSTINGS_STRUCTURED_FUNCTION_SQL = """\
create or replace function update_postings_structured_batch(payload jsonb)
returns table(id uuid)
language plpgsql
as $$
#variable_conflict use_column
begin
    return query
    with incoming as (
        select *
        from jsonb_to_recordset(payload) as x(
            id uuid,
            degree_level text,
            degree_field text,
            has_certification boolean,
            experience_min_years integer,
            experience_max_years integer,
            experience_level text
        )
    ),
    upd as (
        update postings p
        set degree_level = incoming.degree_level,
            degree_field = incoming.degree_field,
            has_certification = incoming.has_certification,
            experience_min_years = incoming.experience_min_years,
            experience_max_years = incoming.experience_max_years,
            experience_level = incoming.experience_level
        from incoming
        where p.id = incoming.id
        returning p.id
    )
    select upd.id from upd;
end;
$$;
"""

# Batched write path for domain_classifier.py's output -- domain,
# domain_method, domain_confidence. Same shape/convention as
# update_postings_structured_batch: unconditional SET, since a re-run of
# classify_domains.py is a full recomputation and a posting whose
# classification changed (or regressed to unclassified) should reflect
# that, not keep a stale prior value.
UPDATE_POSTINGS_DOMAIN_FUNCTION_SQL = """\
create or replace function update_postings_domain_batch(payload jsonb)
returns table(id uuid)
language plpgsql
as $$
#variable_conflict use_column
begin
    return query
    with incoming as (
        select *
        from jsonb_to_recordset(payload) as x(
            id uuid,
            domain text,
            domain_method text,
            domain_confidence numeric
        )
    ),
    upd as (
        update postings p
        set domain = incoming.domain,
            domain_method = incoming.domain_method,
            domain_confidence = incoming.domain_confidence
        from incoming
        where p.id = incoming.id
        returning p.id
    )
    select upd.id from upd;
end;
$$;
"""

ALL_SQL = "\n".join([
    POSTINGS_TABLE_SQL,
    SKILL_MENTIONS_TABLE_SQL,
    DROP_OLD_UPSERT_FUNCTION_SQL,
    UPSERT_FUNCTION_SQL,
    ENRICH_FUNCTION_SQL,
    UPDATE_POSTINGS_STRUCTURED_FUNCTION_SQL,
    UPDATE_POSTINGS_DOMAIN_FUNCTION_SQL,
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
        "currency": row.get("currency"),
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
    """Write fetched description/skills_raw/currency into existing postings,
    matched on (source, detail_url). Does not touch first_seen_at/
    last_seen_at/category/scrape_run_id -- this is a content upgrade, not a
    re-crawl.

    `rows` are expected to already contain fetched 'description',
    'skills_raw', and 'currency' (e.g. from mustakbil.enrich_jobs()'s
    output), not raw listing rows.

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
            "currency": row.get("currency"),
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
    (<word_threshold words) description OR an empty skills_raw OR a null
    currency -- the one-time backfill target set for --enrich-all.

    120 words separates the two populations we actually have: listing-
    derived descriptions top out around 100 words, detail-page descriptions
    run 79-226, so 120 catches "never enriched" without much false-positive
    overlap. skills_raw is checked separately because a listing-derived
    description can already clear 120 words while still having never been
    enriched (empty skills_raw is the more reliable "hasn't been enriched
    yet" signal on its own). currency is checked the same way, for postings
    collected before the currency column existed.

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
            .select("source_job_id,detail_url,description,skills_raw,currency")
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
        if desc_short or _skills_raw_empty(r.get("skills_raw")) or not r.get("currency"):
            targets.append({"source_job_id": r["source_job_id"], "detail_url": r["detail_url"]})
    return targets


def get_postings_for_extraction(run_id: str | None = None) -> list[dict]:
    """Postings to run skill extraction over: either every posting in
    Supabase (run_id=None, the --all backfill path) or just the ones from
    one scrape run (run_id='...', the normal per-collection path).
    experience_raw is included alongside description/skills_raw --
    structured_extraction.py (taxonomy v2's credential/experience pass)
    needs it, and pulling it here keeps skill + structured extraction on
    one shared read instead of two."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        query = client.table("postings").select("id,title,description,skills_raw,experience_raw")
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

    Each mention dict needs: posting_id, skill, category. requirement_type
    is optional and defaults to 'skill' (matching the column's own default)
    for callers that predate taxonomy v2.
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
            "requirement_type": m.get("requirement_type") or "skill",
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


def update_postings_structured(rows: list[dict]) -> dict:
    """Batch-write structured_extraction.py's output -- degree_level,
    degree_field, has_certification, experience_min_years,
    experience_max_years, experience_level -- via
    update_postings_structured_batch. Each row needs 'id' plus all six
    fields (None for anything not found); the RPC sets every field
    unconditionally, so a re-run correctly clears a stale value rather
    than leaving one from a prior extraction pass.

    Returns {"updated": int, "failed": int}.
    """
    if not rows:
        return {"updated": 0, "failed": 0}

    client = _get_client()
    updated = 0
    failed = 0

    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        try:
            result = client.rpc("update_postings_structured_batch", {"payload": batch}).execute()
            updated += len(result.data or [])
        except Exception as exc:
            logger.error(f"Batch structured-fields update failed ({len(batch)} rows): {exc}")
            failed += len(batch)

    logger.info(f"update_postings_structured: updated={updated} failed={failed}")
    return {"updated": updated, "failed": failed}


def get_postings_for_domain_classification() -> list[dict]:
    """id, source, title, description for every posting -- what
    domain_classifier.py needs: title for stage 1, description for stages
    2 and 3. Read-only; classify_domains.py's only write path is
    update_postings_domain() below."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,source,title,description")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def update_postings_domain(rows: list[dict]) -> dict:
    """Batch-write domain_classifier.py's output -- domain, domain_method,
    domain_confidence -- via update_postings_domain_batch. Each row needs
    'id' plus all three fields; the RPC sets every field unconditionally,
    so a re-run of classify_domains.py (e.g. after domains.yaml changes)
    correctly overwrites a prior classification rather than leaving it
    stale.

    Returns {"updated": int, "failed": int}.
    """
    if not rows:
        return {"updated": 0, "failed": 0}

    client = _get_client()
    updated = 0
    failed = 0

    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        try:
            result = client.rpc("update_postings_domain_batch", {"payload": batch}).execute()
            updated += len(result.data or [])
        except Exception as exc:
            logger.error(f"Batch domain update failed ({len(batch)} rows): {exc}")
            failed += len(batch)

    logger.info(f"update_postings_domain: updated={updated} failed={failed}")
    return {"updated": updated, "failed": failed}


def get_postings_for_blank_description_audit() -> list[dict]:
    """id, source, domain, domain_method, domain_confidence, description,
    skills_raw for every posting -- what investigate_blank_descriptions.py
    (quantify the blank-description population, check whether skills_raw
    is doing the work description can't) and backfill_llm_title_only.py
    (identify domain_method='llm' rows actually classified from title
    alone) both need."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,source,domain,domain_method,domain_confidence,description,skills_raw")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def get_postings_for_domain_validation() -> list[dict]:
    """id, title, description, domain, domain_method, domain_confidence
    for every posting -- what validate_domain_classifier.py needs to
    hand-audit the LLM-classified tier specifically (the tier the prior
    validation round never sampled -- only the 'other' bucket was
    checked)."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,title,description,domain,domain_method,domain_confidence")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def get_postings_for_skill_gap_analysis() -> list[dict]:
    """id, source, company, title, description, domain, skills_raw for
    every posting -- what skill_gap_discovery.py needs: the CORRECTED
    domain (from domain_classifier.py's three-stage classification, not
    drift.py's title-only inference) for grouping, company for the
    distinct-company aggregation bar, and full description text for the
    LLM extraction pass (unlike drift.py's n-gram mining, this reads the
    whole description, not a snippet window). source/skills_raw are also
    what skill_gap_source_diagnostic.py needs to tell whether a domain's
    low candidate yield is genuine or an artifact of extracting from
    postings with no description text (skills_raw is the alternative,
    already-structured source for those)."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,source,company,title,description,domain,skills_raw")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


# --------------------------------------------------------------------------
# Forecasting (forecast.py) -- reads needed to compute weekly actuals/
# history, plus the forecasts table's own writes. Uses the same service-role
# client as everything else in this module (forecasts RLS is public-read,
# service-role-write only -- see the migration SQL forecast.py points to).
# --------------------------------------------------------------------------

def get_postings_for_forecast() -> list[dict]:
    """id, source, company, first_seen_at for every posting -- what
    forecast.py needs to bucket into PKT weeks and count, per source, with
    the bulk poster excluded where its methodology calls for that."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,source,company,first_seen_at")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def get_postings_for_drift() -> list[dict]:
    """id, source, company, title, description, skills_raw for every
    posting -- what drift.py needs: description text for n-gram mining,
    plus title/skills_raw so it can run the existing taxonomy matcher
    (extract_skills.extract_skills) for its depth metric (total vs.
    substantive taxonomy matches per posting), the same three fields that
    function already expects. Read-only; drift.py never writes anywhere."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,source,company,title,description,skills_raw")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def get_skill_mentions_for_forecast() -> list[dict]:
    """posting_id, skill for every skill_mentions row."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("skill_mentions")
            .select("posting_id,skill")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def get_postings_for_depth_comparison() -> list[dict]:
    """id, source, title, degree_level, has_certification, experience_level
    for every posting -- what compare_taxonomy_depth.py needs: domain
    inference from title, source grouping, and the taxonomy-v2 structured
    credential/experience signal (which never appears in skill_mentions,
    since those two are deliberately not taxonomy entries -- see
    structured_extraction.py)."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,source,title,degree_level,has_certification,experience_level")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def get_postings_for_v3_depth_comparison() -> list[dict]:
    """id, source, domain, degree_level, has_certification,
    experience_level for every posting -- what compare_taxonomy_v3_depth.py
    needs: the CORRECTED domain (domain_classifier.py's three-stage
    classification, not drift.py's title-only inference used by the older
    compare_taxonomy_depth.py) for grouping, source for the other grouping,
    and the taxonomy-v2-introduced structured credential/experience signal
    for requirement_substantive (unchanged by v3 -- those two are still
    deliberately not taxonomy entries, see structured_extraction.py)."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("postings")
            .select("id,source,domain,degree_level,has_certification,experience_level")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def get_skill_mentions_for_analysis() -> list[dict]:
    """posting_id, skill, category, requirement_type, extraction_method
    for every skill_mentions row -- what compare_taxonomy_depth.py needs
    to distinguish the preserved taxonomy_v1 pass from the new taxonomy_v2
    pass (see taxonomy v2 spec section 1: "v1 extractions stay
    auditable")."""
    client = _get_client()
    rows = []
    offset = 0
    while True:
        res = (
            client.table("skill_mentions")
            .select("posting_id,skill,category,requirement_type,extraction_method")
            .range(offset, offset + _QUERY_PAGE_SIZE - 1)
            .execute()
        )
        batch = res.data
        rows.extend(batch)
        if len(batch) < _QUERY_PAGE_SIZE:
            break
        offset += _QUERY_PAGE_SIZE
    return rows


def insert_forecasts(rows: list[dict]) -> dict:
    """Insert new forecast rows (the --predict step). Each row needs:
    run_id, model_version, target_type, target_key, target_week_start,
    predicted, interval_low, interval_high, baseline_predicted.

    Upsert-safe via the table's own unique constraint on (model_version,
    target_type, target_key, target_week_start): an existing row is left
    alone entirely (ignore_duplicates), never overwritten -- the
    immutability trigger would reject an overwrite attempt anyway, but this
    avoids even trying. Supabase's upsert only returns the rows it actually
    inserted, which is how the caller finds out exactly which ones were new
    (to print only those, not ones silently skipped as already-logged).

    Returns {"inserted": int, "skipped": int, "failed": int,
    "inserted_rows": list[dict]}.
    """
    if not rows:
        return {"inserted": 0, "skipped": 0, "failed": 0, "inserted_rows": []}

    client = _get_client()
    inserted = 0
    skipped = 0
    failed = 0
    inserted_rows: list[dict] = []

    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        try:
            result = (
                client.table("forecasts")
                .upsert(
                    batch,
                    on_conflict="model_version,target_type,target_key,target_week_start",
                    ignore_duplicates=True,
                )
                .execute()
            )
            returned = result.data or []
            inserted += len(returned)
            skipped += len(batch) - len(returned)
            inserted_rows.extend(returned)
        except Exception as exc:
            logger.error(f"Batch forecasts insert failed ({len(batch)} rows): {exc}")
            failed += len(batch)

    logger.info(f"insert_forecasts: inserted={inserted} skipped={skipped} failed={failed}")
    return {"inserted": inserted, "skipped": skipped, "failed": failed, "inserted_rows": inserted_rows}


def get_ungraded_forecasts(target_week_start: str) -> list[dict]:
    """Every forecasts row for one target week (ISO date string) still
    awaiting grading (graded_at is null) -- the --grade step's input.
    Scoping to one specific week here, rather than "all ungraded rows", is
    what makes --grade only ever touch the most recent complete week its
    caller computed; it never reaches back into an older backlog."""
    client = _get_client()
    res = (
        client.table("forecasts")
        .select("id,target_type,target_key,target_week_start,predicted,baseline_predicted,model_version,source_scope")
        .eq("target_week_start", target_week_start)
        .is_("graded_at", "null")
        .execute()
    )
    return res.data or []


def grade_forecast_row(
    row_id: int,
    actual: float,
    graded_at: str,
    abs_error: float,
    baseline_abs_error: float,
    beat_baseline: bool,
) -> bool:
    """Fill the five grading columns on one forecasts row. Only ever
    touches those five columns, so the immutability trigger's "locked
    fields changed" check always passes here by construction; the trigger
    still independently enforces grade-once (old.graded_at is not null)."""
    client = _get_client()
    try:
        client.table("forecasts").update({
            "actual": actual,
            "graded_at": graded_at,
            "abs_error": abs_error,
            "baseline_abs_error": baseline_abs_error,
            "beat_baseline": beat_baseline,
        }).eq("id", row_id).execute()
        return True
    except Exception as exc:
        logger.error(f"Failed to grade forecasts row id={row_id}: {exc}")
        return False


# --------------------------------------------------------------------------
# Backtesting (backtest.py) -- a separate table from forecasts on purpose.
# backtests is a fully mutable, re-computed-from-scratch derived artifact
# (retrospective evidence of whether the model has any skill at all);
# forecasts is an append-only, immutable record of predictions made before
# outcomes existed. The two must never be mixed -- see backtest.py's module
# docstring. The backtests table's own migration SQL (schema only, no
# immutability trigger) is printed by hand, the same one-time pattern the
# forecasts table and its source_scope column followed.
# --------------------------------------------------------------------------

def truncate_backtests() -> int:
    """Delete every row in backtests. Called at the start of every
    backtest.py run -- backtests are always fully recomputed, never
    incrementally updated, so stale rows from a prior run (e.g. a since-
    retired skill that's no longer in the top 12) must not linger.
    Returns the number of rows deleted."""
    client = _get_client()
    result = client.table("backtests").delete().gte("id", 0).execute()
    return len(result.data or [])


def insert_backtests(rows: list[dict]) -> dict:
    """Batch insert backtests rows (call after truncate_backtests()).
    Upsert rather than plain insert, keyed on the same
    (model_version, target_type, target_key, target_week_start) shape
    forecasts uses -- a defensive idempotency backstop only, since the
    table should already be empty; on conflict, overwrite (this is a
    derived artifact, not a historical record, so a repeated row losing
    to a fresher computation is correct behavior, unlike forecasts'
    ignore_duplicates).

    Returns {"inserted": int, "failed": int}.
    """
    if not rows:
        return {"inserted": 0, "failed": 0}

    client = _get_client()
    inserted = 0
    failed = 0

    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        try:
            result = (
                client.table("backtests")
                .upsert(batch, on_conflict="model_version,target_type,target_key,target_week_start")
                .execute()
            )
            inserted += len(result.data or [])
        except Exception as exc:
            logger.error(f"Batch backtests insert failed ({len(batch)} rows): {exc}")
            failed += len(batch)

    logger.info(f"insert_backtests: inserted={inserted} failed={failed}")
    return {"inserted": inserted, "failed": failed}


if __name__ == "__main__":
    print_schema_sql()
