"""Rukhwise forecasting entrypoint.

  python forecast.py --grade
      Grades the most recent COMPLETE week (Mon 00:00 to Sun 23:59:59.999999
      PKT) against actuals computed from postings/skill_mentions. Only
      touches forecasts rows still ungraded for that specific week --
      already-graded rows are left alone. Idempotent: a second run finds
      nothing left to grade and does nothing (the forecasts table's
      immutability trigger also enforces grade-once at the database level,
      as a backstop, not the primary mechanism).

  python forecast.py --predict
      Logs forecasts for the NEXT week (next Monday PKT onward): one
      volume/all target (Mustakbil-only) and one skill/{name} target per
      skill in the current top 12 by distinct-posting count over the last
      4 complete weeks (all sources, bulk poster excluded). Upsert-safe:
      an existing forecast for the same (model_version, target_type,
      target_key, target_week_start) is left untouched, never overwritten.
      Prints a table of everything newly logged.

Requires the forecasts table + immutability trigger + RLS to already exist
(see the migration SQL this feature shipped with -- run once, by hand, in
the Supabase SQL editor; not applied by this script).

Methodology is fixed by design, not reconfigurable via flags:
  - PKT weeks run Monday 00:00 to Sunday 23:59:59.999999 (PKT = UTC+5, no
    DST -- a fixed offset is exact, not an approximation).
  - Volume actual/prediction: source='mustakbil' only.
  - Skill actual/prediction: all sources, with postings from Naseeb
    Enterprise Inc (the one consistently bulk poster) excluded.
  - model trailing_mean_3w_v1: predicted = mean of the last <=3 complete
    weeks' counts for that target (fewer if less history exists, minimum
    1 -- model_version gets a '_shorthist' suffix when so). baseline =
    the single most recent complete week's count. interval = predicted
    +/- 1.5 * stddev of the last <=4 complete weeks' counts (sample
    stdev; 0 spread with only 1 data point), floored at 0 on the low end.
  - "Complete weeks exist" only from the week containing that target
    universe's earliest postings.first_seen_at onward -- weeks entirely
    before collection began are never queried as zero-count history, since
    that would fabricate history the collector was never running to
    observe. Mustakbil-only volume and all-source skills therefore each
    get their own earliest-week floor.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

PKT = timezone(timedelta(hours=5))
BULK_COMPANY_KEY = "naseeb enterprise inc"  # normalized: whitespace-collapsed, casefolded
TOP_SKILLS_COUNT = 12
MEAN_WINDOW_WEEKS = 3
INTERVAL_WINDOW_WEEKS = 4
INTERVAL_MULTIPLIER = 1.5
MODEL_NAME = "trailing_mean_3w_v1"


# --------------------------------------------------------------------------
# PKT week math
# --------------------------------------------------------------------------

def _normalize_company(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _week_start(dt_utc: datetime) -> datetime:
    """Floor a UTC datetime to that PKT week's Monday 00:00, returned as
    the equivalent UTC instant."""
    pkt = dt_utc.astimezone(PKT)
    monday_pkt = (pkt - timedelta(days=pkt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday_pkt.astimezone(timezone.utc)


def _complete_week_start(now_utc: datetime) -> datetime:
    """The most recent COMPLETE week's Monday-00:00-PKT instant, as UTC.
    Always <= now_utc - 0s by construction (the current, in-progress week
    is never returned)."""
    return _week_start(now_utc) - timedelta(days=7)


def _next_week_start(now_utc: datetime) -> datetime:
    """Next Monday 00:00 PKT onward -- the week --predict forecasts."""
    return _week_start(now_utc) + timedelta(days=7)


def _week_label(week_start_utc: datetime) -> str:
    """ISO date (PKT) of that week's Monday -- what target_week_start stores."""
    return week_start_utc.astimezone(PKT).date().isoformat()


def _weeks_history(
    anchor_complete_week_start: datetime, count: int, earliest_week_start: datetime
) -> list[datetime]:
    """Up to `count` complete week-start instants ending at
    anchor_complete_week_start, oldest first, clipped so no week before
    earliest_week_start is included -- never fabricate pre-collection
    history as a zero week."""
    weeks: list[datetime] = []
    w = anchor_complete_week_start
    for _ in range(count):
        if w < earliest_week_start:
            break
        weeks.append(w)
        w -= timedelta(days=7)
    weeks.reverse()
    return weeks


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------

def _count_in_week(postings: list[dict], week_start_utc: datetime) -> int:
    week_end_utc = week_start_utc + timedelta(days=7)
    return sum(
        1 for p in postings
        if p.get("first_seen_at") and week_start_utc <= _parse_ts(p["first_seen_at"]) < week_end_utc
    )


def _skill_posting_ids(
    mentions: list[dict], postings_index: dict[str, dict], exclude_bulk: bool
) -> dict[str, set[str]]:
    """skill -> set of posting_ids mentioning it, optionally dropping any
    posting from the bulk poster."""
    out: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        posting = postings_index.get(m["posting_id"])
        if not posting:
            continue
        if exclude_bulk and _normalize_company(posting.get("company")) == BULK_COMPANY_KEY:
            continue
        out[m["skill"]].add(m["posting_id"])
    return out


def _count_distinct_in_week(
    posting_ids: set[str], postings_index: dict[str, dict], week_start_utc: datetime
) -> int:
    week_end_utc = week_start_utc + timedelta(days=7)
    count = 0
    for pid in posting_ids:
        posting = postings_index.get(pid)
        if not posting or not posting.get("first_seen_at"):
            continue
        ts = _parse_ts(posting["first_seen_at"])
        if week_start_utc <= ts < week_end_utc:
            count += 1
    return count


def _top_skills(
    skill_ids: dict[str, set[str]],
    postings_index: dict[str, dict],
    window_start_utc: datetime,
    window_end_utc: datetime,
    limit: int,
) -> list[str]:
    """Skills ranked by distinct-posting count within [window_start_utc,
    window_end_utc), ties broken alphabetically for determinism."""
    counts: dict[str, int] = {}
    for skill, ids in skill_ids.items():
        n = 0
        for pid in ids:
            posting = postings_index.get(pid)
            if not posting or not posting.get("first_seen_at"):
                continue
            ts = _parse_ts(posting["first_seen_at"])
            if window_start_utc <= ts < window_end_utc:
                n += 1
        if n > 0:
            counts[skill] = n
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [skill for skill, _ in ranked[:limit]]


# --------------------------------------------------------------------------
# Forecast row construction (--predict)
# --------------------------------------------------------------------------

def _build_forecast_row(
    run_id: str,
    target_type: str,
    target_key: str,
    target_week_start_utc: datetime,
    mean_counts: list[int],
    interval_counts: list[int],
) -> dict:
    shorthist = len(mean_counts) < MEAN_WINDOW_WEEKS
    model_version = MODEL_NAME + ("_shorthist" if shorthist else "")

    predicted = statistics.fmean(mean_counts)
    baseline_predicted = mean_counts[-1]  # mean_counts is oldest->newest

    if len(interval_counts) >= 2:
        spread = statistics.stdev(interval_counts) * INTERVAL_MULTIPLIER
    else:
        spread = 0.0
    interval_low = max(0.0, predicted - spread)
    interval_high = predicted + spread

    return {
        "run_id": run_id,
        "model_version": model_version,
        "target_type": target_type,
        "target_key": target_key,
        "target_week_start": _week_label(target_week_start_utc),
        "predicted": round(predicted, 4),
        "interval_low": round(interval_low, 4),
        "interval_high": round(interval_high, 4),
        "baseline_predicted": round(float(baseline_predicted), 4),
    }


def run_predict() -> list[dict]:
    from storage import get_postings_for_forecast, get_skill_mentions_for_forecast, insert_forecasts

    now_utc = datetime.now(timezone.utc)
    complete_week_start = _complete_week_start(now_utc)
    target_week_start = _next_week_start(now_utc)

    postings = get_postings_for_forecast()
    mentions = get_skill_mentions_for_forecast()

    if not postings:
        logger.error("No postings found -- refusing to predict with zero history")
        return []

    postings_index = {p["id"]: p for p in postings}

    mustakbil_postings = [p for p in postings if p.get("source") == "mustakbil"]
    if not mustakbil_postings:
        logger.error("No Mustakbil postings found -- cannot build the volume/all target")
        volume_mean_weeks: list[datetime] = []
    else:
        mustakbil_earliest = min(_parse_ts(p["first_seen_at"]) for p in mustakbil_postings)
        mustakbil_earliest_week = _week_start(mustakbil_earliest)
        volume_mean_weeks = _weeks_history(complete_week_start, MEAN_WINDOW_WEEKS, mustakbil_earliest_week)
        volume_interval_weeks = _weeks_history(complete_week_start, INTERVAL_WINDOW_WEEKS, mustakbil_earliest_week)

    all_earliest = min(_parse_ts(p["first_seen_at"]) for p in postings)
    all_earliest_week = _week_start(all_earliest)
    skill_mean_weeks = _weeks_history(complete_week_start, MEAN_WINDOW_WEEKS, all_earliest_week)
    skill_interval_weeks = _weeks_history(complete_week_start, INTERVAL_WINDOW_WEEKS, all_earliest_week)

    run_id = f"forecast-{now_utc:%Y%m%dT%H%M%S}"
    candidates: list[dict] = []

    # ---- volume/all (Mustakbil only) --------------------------------
    if volume_mean_weeks:
        mean_counts = [_count_in_week(mustakbil_postings, w) for w in volume_mean_weeks]
        interval_counts = [_count_in_week(mustakbil_postings, w) for w in volume_interval_weeks]
        candidates.append(
            _build_forecast_row(run_id, "volume", "all", target_week_start, mean_counts, interval_counts)
        )
    else:
        logger.warning("No complete week of Mustakbil history yet -- skipping volume/all this run")

    # ---- skill/{name}, top 12 by distinct postings, all sources, bulk excluded ----
    if skill_mean_weeks:
        skill_ids = _skill_posting_ids(mentions, postings_index, exclude_bulk=True)
        top_skills = _top_skills(
            skill_ids, postings_index,
            window_start_utc=skill_interval_weeks[0],
            window_end_utc=complete_week_start + timedelta(days=7),
            limit=TOP_SKILLS_COUNT,
        )
        for skill in top_skills:
            ids = skill_ids.get(skill, set())
            mean_counts = [_count_distinct_in_week(ids, postings_index, w) for w in skill_mean_weeks]
            interval_counts = [_count_distinct_in_week(ids, postings_index, w) for w in skill_interval_weeks]
            candidates.append(
                _build_forecast_row(run_id, "skill", skill, target_week_start, mean_counts, interval_counts)
            )
    else:
        logger.warning("No complete week of history yet -- skipping all skill targets this run")

    result = insert_forecasts(candidates)
    logger.info(
        f"[{run_id}] PREDICT SUMMARY candidates={len(candidates)} "
        f"inserted={result['inserted']} skipped_existing={result['skipped']} failed={result['failed']}"
    )

    inserted_rows = result["inserted_rows"]
    _print_predict_table(inserted_rows)
    return inserted_rows


def _print_predict_table(rows: list[dict]) -> None:
    if not rows:
        print("No forecasts newly logged (all targets already had a forecast for this week, or none could be built).")
        return

    rows = sorted(rows, key=lambda r: (r["target_type"], r["target_key"]))
    headers = ["target_type", "target_key", "target_week_start", "model_version",
               "predicted", "interval_low", "interval_high", "baseline_predicted"]
    widths = [max(len(h), max(len(str(r.get(h, ""))) for r in rows)) for h in headers]

    def fmt_row(values: list[str]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(values, widths))

    print(fmt_row(headers))
    print(fmt_row(["-" * w for w in widths]))
    for r in rows:
        print(fmt_row([str(r.get(h, "")) for h in headers]))


# --------------------------------------------------------------------------
# Grading (--grade)
# --------------------------------------------------------------------------

def run_grade() -> list[dict]:
    from storage import (
        get_postings_for_forecast,
        get_skill_mentions_for_forecast,
        get_ungraded_forecasts,
        grade_forecast_row,
    )

    now_utc = datetime.now(timezone.utc)
    complete_week_start = _complete_week_start(now_utc)
    week_label = _week_label(complete_week_start)

    # Refuse to grade any week not fully complete in PKT: by construction,
    # complete_week_start's week always ends (complete_week_start + 7d)
    # at or before now_utc. This assertion just makes that guarantee
    # explicit rather than implicit in the arithmetic above.
    assert complete_week_start + timedelta(days=7) <= now_utc, (
        f"Refusing to grade {week_label}: that week has not fully elapsed in PKT yet"
    )

    ungraded = get_ungraded_forecasts(week_label)
    if not ungraded:
        logger.info(f"No ungraded forecasts for complete week {week_label} -- nothing to grade")
        return []

    logger.info(f"Grading {len(ungraded)} ungraded forecast(s) for complete week {week_label}")

    postings = get_postings_for_forecast()
    mentions = get_skill_mentions_for_forecast()
    postings_index = {p["id"]: p for p in postings}

    mustakbil_postings = [p for p in postings if p.get("source") == "mustakbil"]
    volume_actual = _count_in_week(mustakbil_postings, complete_week_start)

    skill_ids = _skill_posting_ids(mentions, postings_index, exclude_bulk=True)

    now_iso = now_utc.isoformat()
    graded_rows: list[dict] = []

    for row in ungraded:
        if row["target_type"] == "volume":
            actual = volume_actual
        else:
            ids = skill_ids.get(row["target_key"], set())
            actual = _count_distinct_in_week(ids, postings_index, complete_week_start)

        predicted = float(row["predicted"])
        baseline_predicted = float(row["baseline_predicted"])
        abs_error = round(abs(actual - predicted), 4)
        baseline_abs_error = round(abs(actual - baseline_predicted), 4)
        beat_baseline = abs_error < baseline_abs_error

        ok = grade_forecast_row(
            row["id"],
            actual=actual,
            graded_at=now_iso,
            abs_error=abs_error,
            baseline_abs_error=baseline_abs_error,
            beat_baseline=beat_baseline,
        )
        if ok:
            graded_rows.append({
                **row,
                "actual": actual,
                "abs_error": abs_error,
                "baseline_abs_error": baseline_abs_error,
                "beat_baseline": beat_baseline,
            })

    logger.info(
        f"GRADE SUMMARY week={week_label} graded={len(graded_rows)} "
        f"failed={len(ungraded) - len(graded_rows)}"
    )

    for r in graded_rows:
        mark = "beat baseline" if r["beat_baseline"] else "did not beat baseline"
        print(
            f"{r['target_type']:<7} {r['target_key']:<20} actual={r['actual']:<6} "
            f"predicted={r['predicted']:<8} baseline={r['baseline_predicted']:<8} "
            f"abs_error={r['abs_error']:<8} ({mark})"
        )

    return graded_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Rukhwise forecasting")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--grade", action="store_true", help="Grade the most recent complete week")
    group.add_argument("--predict", action="store_true", help="Log forecasts for next week")
    args = parser.parse_args()

    if args.grade:
        run_grade()
    elif args.predict:
        run_predict()


if __name__ == "__main__":
    main()
