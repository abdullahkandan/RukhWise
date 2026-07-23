"""Rukhwise backtest: retrospective evaluation of trailing_mean_3w_v1.

  python backtest.py
      Truncates and recomputes the ENTIRE backtests table from scratch,
      over every complete historical week that has at least one prior
      complete week of Mustakbil history. Prints a beat/tie/lost summary
      per target type and overall when done.

Structurally separate from forecast.py at every layer, on purpose:
  - Different table (backtests, not forecasts) -- see the migration SQL
    printed alongside this feature, run once by hand in the Supabase SQL
    editor, not applied by this script. backtests carries no immutability
    trigger: it is a derived artifact, fully mutable and re-runnable, never
    a historical record. Every run truncates and recomputes it whole.
  - A backtest answers "does this model have any skill at all against
    history it already knows the answer to." That is real but WEAKER
    evidence than the live forecast log: forecast.py logs a prediction
    before the target week's outcome exists and never lets it be edited
    afterward. This script computes "predictions" for weeks that have
    already happened, with the benefit of hindsight about which weeks even
    have enough history to bother with. Never present backtest numbers as
    if they were live forecast performance, and never write to or read
    from the forecasts table here.

Source scope: source_scope is fixed to 'mustakbil' for the ENTIRE backtest
(volume AND skill targets alike) -- every row this script writes carries
that literal string. Unlike forecast.py's live skill methodology (which
pools AUTOMATED_SOURCES = mustakbil + indeed), Mustakbil is the only source
with enough continuous history to backtest meaningfully: Indeed only has a
few weeks so far, Rozee is session-based/semi-manual, and LinkedIn's
first_seen_at can't be trusted as posting recency at all (see
jobspy_source.py). Pooling any of those in would just be a different way
of leaking today's data-collection footprint into a supposedly historical
evaluation.

No lookahead: for the week being "predicted," every count that feeds the
trailing mean / interval / top-12-skill selection is built ONLY from
postings with first_seen_at strictly before that week's Monday 00:00 PKT
(the as-of view). This is asserted in code (_weeks_strictly_before), not
just documented -- a silent regression here would make the backtest
numbers meaningless by letting the model "see the future." The target
week's actual outcome is, correctly, computed from the full corpus (that's
the whole point of grading in hindsight).

Reuses forecast.py's week-bucketing/counting helpers directly rather than
reimplementing them, so the backtest's math is guaranteed identical to
what --predict/--grade actually do, not a parallel implementation that
could quietly drift from it.
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

from forecast import (  # noqa: E402
    INTERVAL_MULTIPLIER,
    INTERVAL_WINDOW_WEEKS,
    MEAN_WINDOW_WEEKS,
    MODEL_NAME,
    TOP_SKILLS_COUNT,
    _complete_week_start,
    _count_distinct_in_week,
    _count_in_week,
    _parse_ts,
    _skill_posting_ids,
    _top_skills,
    _week_label,
    _week_start,
    _weeks_history,
)

BACKTEST_SOURCE_SCOPE = "mustakbil"
BACKTEST_RUN_PREFIX = "backtest"


# --------------------------------------------------------------------------
# Week iteration
# --------------------------------------------------------------------------

def _iter_target_weeks(earliest_week_start: datetime, last_complete_week_start: datetime):
    """Every complete week strictly after earliest_week_start (so it always
    has >=1 prior complete week of history) up to and including
    last_complete_week_start."""
    w = earliest_week_start + timedelta(days=7)
    while w <= last_complete_week_start:
        yield w
        w += timedelta(days=7)


def _weeks_strictly_before(weeks: list[datetime], boundary_utc: datetime) -> bool:
    """No-lookahead invariant: every history week used to build a
    "prediction" for `boundary_utc` must have fully elapsed before
    `boundary_utc` starts."""
    return all(w + timedelta(days=7) <= boundary_utc for w in weeks)


# --------------------------------------------------------------------------
# Row construction
# --------------------------------------------------------------------------

def _build_backtest_row(
    run_id: str,
    target_type: str,
    target_key: str,
    target_week_start_utc: datetime,
    mean_counts: list[int],
    interval_counts: list[int],
    actual: int,
) -> dict:
    shorthist = len(mean_counts) < MEAN_WINDOW_WEEKS
    model_version = MODEL_NAME + ("_shorthist" if shorthist else "")

    predicted = statistics.fmean(mean_counts)
    baseline_predicted = float(mean_counts[-1])  # mean_counts is oldest->newest

    if len(interval_counts) >= 2:
        spread = statistics.stdev(interval_counts) * INTERVAL_MULTIPLIER
    else:
        spread = 0.0
    interval_low = max(0.0, predicted - spread)
    interval_high = predicted + spread

    abs_error = round(abs(actual - predicted), 4)
    baseline_abs_error = round(abs(actual - baseline_predicted), 4)
    if abs_error < baseline_abs_error:
        outcome = "beat"
    elif abs_error > baseline_abs_error:
        outcome = "lost"
    else:
        outcome = "tie"

    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": run_id,
        "computed_at": now_iso,
        "is_retrospective": True,
        "model_version": model_version,
        "target_type": target_type,
        "target_key": target_key,
        "target_week_start": _week_label(target_week_start_utc),
        "predicted": round(predicted, 4),
        "interval_low": round(interval_low, 4),
        "interval_high": round(interval_high, 4),
        "baseline_predicted": round(baseline_predicted, 4),
        "actual": actual,
        "abs_error": abs_error,
        "baseline_abs_error": baseline_abs_error,
        "outcome": outcome,
        "beat_baseline": outcome == "beat",
        "source_scope": BACKTEST_SOURCE_SCOPE,
    }


# --------------------------------------------------------------------------
# Main computation
# --------------------------------------------------------------------------

def run_backtest() -> list[dict]:
    from storage import (
        get_postings_for_forecast,
        get_skill_mentions_for_forecast,
        insert_backtests,
        truncate_backtests,
    )

    now_utc = datetime.now(timezone.utc)
    last_complete_week = _complete_week_start(now_utc)

    postings = get_postings_for_forecast()
    mentions = get_skill_mentions_for_forecast()

    mustakbil_postings = [p for p in postings if p.get("source") == "mustakbil"]
    if not mustakbil_postings:
        logger.error("No Mustakbil postings found -- nothing to backtest")
        return []

    full_index = {p["id"]: p for p in mustakbil_postings}
    full_skill_ids = _skill_posting_ids(mentions, full_index, exclude_bulk=True)

    earliest = min(_parse_ts(p["first_seen_at"]) for p in mustakbil_postings)
    earliest_week = _week_start(earliest)

    target_weeks = list(_iter_target_weeks(earliest_week, last_complete_week))
    if not target_weeks:
        logger.warning(
            "Not enough Mustakbil history yet for even one backtest week "
            "(need >=1 complete week before the earliest collected week)"
        )
        return []

    run_id = f"{BACKTEST_RUN_PREFIX}-{now_utc:%Y%m%dT%H%M%S}"
    rows: list[dict] = []

    for target_week in target_weeks:
        anchor = target_week - timedelta(days=7)  # last complete week strictly before target_week

        # As-of view: only postings collected before this historical week
        # started are visible to the "prediction" for it -- the entire
        # no-lookahead guarantee starts here.
        asof_postings = [p for p in mustakbil_postings if _parse_ts(p["first_seen_at"]) < target_week]
        assert all(_parse_ts(p["first_seen_at"]) < target_week for p in asof_postings), (
            f"Lookahead detected: as-of filter let a postings row from the target week or "
            f"later leak into history for {_week_label(target_week)}"
        )
        asof_index = {p["id"]: p for p in asof_postings}

        mean_weeks = _weeks_history(anchor, MEAN_WINDOW_WEEKS, earliest_week)
        interval_weeks = _weeks_history(anchor, INTERVAL_WINDOW_WEEKS, earliest_week)
        if not mean_weeks:
            continue  # shouldn't happen given _iter_target_weeks' own floor, but never fabricate a row without history

        assert _weeks_strictly_before(mean_weeks, target_week), (
            f"Lookahead detected: mean-window history reaches into or past {_week_label(target_week)}"
        )
        assert _weeks_strictly_before(interval_weeks, target_week), (
            f"Lookahead detected: interval-window history reaches into or past {_week_label(target_week)}"
        )

        # ---- volume/all (Mustakbil only) ----------------------------
        mean_counts = [_count_in_week(asof_postings, w) for w in mean_weeks]
        interval_counts = [_count_in_week(asof_postings, w) for w in interval_weeks]
        actual = _count_in_week(mustakbil_postings, target_week)
        rows.append(
            _build_backtest_row(run_id, "volume", "all", target_week, mean_counts, interval_counts, actual)
        )

        # ---- skill/{name}, top 12 by distinct postings AS OF this week ----
        skill_ids_asof = _skill_posting_ids(mentions, asof_index, exclude_bulk=True)
        top_skills = _top_skills(
            skill_ids_asof, asof_index,
            window_start_utc=interval_weeks[0],
            window_end_utc=target_week,
            limit=TOP_SKILLS_COUNT,
        )
        for skill in top_skills:
            ids_asof = skill_ids_asof.get(skill, set())
            mean_counts = [_count_distinct_in_week(ids_asof, asof_index, w) for w in mean_weeks]
            interval_counts = [_count_distinct_in_week(ids_asof, asof_index, w) for w in interval_weeks]
            # Actual outcome uses the FULL corpus (hindsight is the point of
            # grading) -- never the as-of-clipped view used to build history.
            ids_full = full_skill_ids.get(skill, set())
            actual = _count_distinct_in_week(ids_full, full_index, target_week)
            rows.append(
                _build_backtest_row(run_id, "skill", skill, target_week, mean_counts, interval_counts, actual)
            )

    truncated = truncate_backtests()
    result = insert_backtests(rows)
    logger.info(
        f"[{run_id}] BACKTEST SUMMARY weeks_evaluated={len(target_weeks)} rows_computed={len(rows)} "
        f"truncated_prior_rows={truncated} inserted={result['inserted']} failed={result['failed']} "
        f"source_scope={BACKTEST_SOURCE_SCOPE}"
    )
    _print_summary(rows)
    return rows


def _print_summary(rows: list[dict]) -> None:
    if not rows:
        print("No backtest rows computed (not enough Mustakbil history for even one backtest week yet).")
        return

    n_weeks = len({r["target_week_start"] for r in rows})
    print(
        f"BACKTEST -- source_scope={BACKTEST_SOURCE_SCOPE} (Mustakbil only; the only source with enough "
        f"continuous history to backtest -- see backtest.py's module docstring)"
    )
    print(
        "RETROSPECTIVE: computed after outcomes were already known. This shows whether the model has "
        "any skill at all, not that a prediction was made before the outcome existed -- that stronger "
        "claim belongs to the live forecast log (forecast.py / the forecasts table) only.\n"
    )
    print(f"{len(rows)} rows across {n_weeks} week(s)\n")

    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["target_type"]].append(r)

    header = f"{'target_type':<12} {'n':<6} {'beat':<6} {'tie':<6} {'lost':<6} {'mae':<8} {'beat_rate':<10}"
    print(header)
    print("-" * len(header))
    for target_type, rs in sorted(by_type.items()):
        beat = sum(1 for r in rs if r["outcome"] == "beat")
        tie = sum(1 for r in rs if r["outcome"] == "tie")
        lost = sum(1 for r in rs if r["outcome"] == "lost")
        mae = statistics.fmean(r["abs_error"] for r in rs)
        print(f"{target_type:<12} {len(rs):<6} {beat:<6} {tie:<6} {lost:<6} {mae:<8.3f} {beat / len(rs):<10.4f}")

    beat = sum(1 for r in rows if r["outcome"] == "beat")
    tie = sum(1 for r in rows if r["outcome"] == "tie")
    lost = sum(1 for r in rows if r["outcome"] == "lost")
    mae = statistics.fmean(r["abs_error"] for r in rows)
    print("-" * len(header))
    print(f"{'overall':<12} {len(rows):<6} {beat:<6} {tie:<6} {lost:<6} {mae:<8.3f} {beat / len(rows):<10.4f}")


def main() -> None:
    run_backtest()


if __name__ == "__main__":
    main()
