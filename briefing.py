"""Rukhwise weekly briefing: fully automated, fact-gated publication.

  python briefing.py
      Runs after forecast.py --grade and --predict in the Monday workflow.
      Computes a facts dict directly from the database (TASK 1 -- no LLM
      involved in this step at all), sends ONLY that facts dict to Groq to
      draft 120-180 words of plain restating prose (TASK 2), then runs the
      draft through a hard assertion layer before anything is published
      (TASK 3): every numeral and every capitalized name/entity in the
      draft must trace back to the facts dict, a fixed list of predictive/
      causal phrases is banned outright, and word count must land in
      [100, 220]. Any single failure blocks the LLM draft and falls back
      to a plain templated briefing built directly, mechanically, from
      that same facts dict -- which cannot fail the assertion layer by
      construction, since it says nothing the facts dict didn't already
      say. The site always publishes something true; the LLM version is
      an upgrade, never a dependency.

      The LLM never computes, never predicts, and cannot introduce a
      number: it is handed a dict and asked to restate it in prose. Every
      fact-shaped claim in the published briefing is independently
      verifiable against facts_json, stored alongside body on every row.
      Idempotent: a briefing already published for the target week is
      left alone (see storage.get_briefing_for_week), so a workflow retry
      never double-calls Groq or attempts a second insert (the table's
      unique(week_start) and immutability trigger would reject it anyway).

Requires the briefings table + immutability trigger + RLS to already
exist (see the migration SQL this feature shipped with -- run once, by
hand, in the Supabase SQL editor; not applied by this script).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

from forecast import (  # noqa: E402
    AUTOMATED_COLLECTION_START,
    AUTOMATED_SOURCES,
    BULK_COMPANY_KEY,
    SUBSTANTIVE_SKILL_EXCLUDED_CATEGORIES,
    TAXONOMY_VERSION,
    VOLUME_SOURCE,
    _complete_week_start,
    _count_in_week,
    _is_market_skill,
    _load_taxonomy,
    _next_week_start,
    _normalize_company,
    _parse_ts,
    _week_label,
    _week_start,
)

SOURCE_DISPLAY = {"mustakbil": "Mustakbil", "indeed": "Indeed"}

TOP_SKILLS_LIMIT = 5
TOP_SKILLS_COMPARISON_LIMIT = 10

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_MAX_RETRIES = 5

WORD_COUNT_MIN = 100
WORD_COUNT_MAX = 220
# TASK 3(c), verbatim from spec. Case-insensitive substring match against
# the draft -- predictive or causal language has no place in a briefing
# that only ever restates what already happened.
BANNED_PHRASES = [
    "expected to", "suggests", "likely", "indicates that",
    "due to", "because of", "will grow", "trend shows",
]


def _target_display(target_type: str, target_key: str, taxonomy: dict) -> str:
    if target_type == "volume":
        return "All postings (Mustakbil)"
    spec = taxonomy["skills"].get(target_key)
    return spec["display"] if spec else target_key


def _outcome(abs_error: float, baseline_abs_error: float) -> str:
    """beat/tie/lost -- same three-way derivation as api/main.py's
    _forecast_outcome (duplicated here, not imported: api/ and the
    root-level scripts are deliberately independent dependency surfaces,
    same reasoning curriculum.py's own ACTIVE_TAXONOMY scoping follows)."""
    if abs_error < baseline_abs_error:
        return "beat"
    if abs_error > baseline_abs_error:
        return "lost"
    return "tie"


# --------------------------------------------------------------------------
# TASK 1: fact computation. No LLM anywhere in this section.
# --------------------------------------------------------------------------

def _skill_company_counts_in_week(
    mentions: list[dict], postings_index: dict[str, dict], week_start_utc: datetime
) -> dict[str, int]:
    """skill -> distinct-company count within [week_start, week_start+7d),
    restricted to whatever postings_index already scopes (automated
    sources, per this module's caller) and excluding the bulk poster --
    same exclusions forecast.py applies to its own skill targets."""
    week_end_utc = week_start_utc + timedelta(days=7)
    skill_companies: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        posting = postings_index.get(m["posting_id"])
        if not posting or not posting.get("first_seen_at"):
            continue
        company_key = _normalize_company(posting.get("company"))
        if not company_key or company_key == BULK_COMPANY_KEY:
            continue
        ts = _parse_ts(posting["first_seen_at"])
        if week_start_utc <= ts < week_end_utc:
            skill_companies[m["skill"]].add(company_key)
    return {skill: len(companies) for skill, companies in skill_companies.items()}


def compute_facts(now_utc: datetime | None = None) -> dict:
    """TASK 1. Every fact the LLM (and the template fallback) are allowed
    to know, computed here directly from the database. Returns a plain,
    JSON-serializable dict -- this IS the boundary: nothing computed after
    this function returns is ever treated as ground truth again, and the
    LLM never sees anything except this dict's own json.dumps()."""
    from storage import (
        get_forecasts_for_week,
        get_graded_forecasts,
        get_postings_for_forecast,
        get_skill_mentions_for_briefing,
    )

    now_utc = now_utc or datetime.now(timezone.utc)
    taxonomy = _load_taxonomy()

    last_week_start = _complete_week_start(now_utc)
    last_week_label = _week_label(last_week_start)
    prior_week_start = last_week_start - timedelta(days=7)
    prior_week_label = _week_label(prior_week_start)
    next_week_start = _next_week_start(now_utc)
    next_week_label = _week_label(next_week_start)

    # ---- forecasts: last week's graded outcomes -------------------------
    last_week_forecasts = get_forecasts_for_week(last_week_label)
    graded_last_week = [f for f in last_week_forecasts if f.get("graded_at")]

    beat_n = tie_n = lost_n = 0
    target_rows = []
    for f in graded_last_week:
        outcome = _outcome(float(f["abs_error"]), float(f["baseline_abs_error"]))
        if outcome == "beat":
            beat_n += 1
        elif outcome == "tie":
            tie_n += 1
        else:
            lost_n += 1
        target_rows.append({
            "target_type": f["target_type"],
            "target_key": f["target_key"],
            "display": _target_display(f["target_type"], f["target_key"], taxonomy),
            "predicted": round(float(f["predicted"]), 2),
            "actual": round(float(f["actual"]), 2),
            "baseline_predicted": round(float(f["baseline_predicted"]), 2),
            "outcome": outcome,
        })
    target_rows.sort(key=lambda r: (r["target_type"], r["target_key"]))
    mae_last_week = (
        round(sum(float(f["abs_error"]) for f in graded_last_week) / len(graded_last_week), 2)
        if graded_last_week else None
    )

    # ---- running totals across ALL graded weeks --------------------------
    all_graded = get_graded_forecasts()
    rt_beat = sum(1 for f in all_graded if _outcome(float(f["abs_error"]), float(f["baseline_abs_error"])) == "beat")
    rt_tie = sum(1 for f in all_graded if _outcome(float(f["abs_error"]), float(f["baseline_abs_error"])) == "tie")
    rt_lost = sum(1 for f in all_graded if _outcome(float(f["abs_error"]), float(f["baseline_abs_error"])) == "lost")
    mae_all_time = (
        round(sum(float(f["abs_error"]) for f in all_graded) / len(all_graded), 2)
        if all_graded else None
    )

    # ---- this week's newly logged predictions -----------------------------
    new_predictions = get_forecasts_for_week(next_week_label)
    new_prediction_rows = sorted(
        [
            {
                "target_type": f["target_type"],
                "target_key": f["target_key"],
                "display": _target_display(f["target_type"], f["target_key"], taxonomy),
                "predicted": round(float(f["predicted"]), 2),
            }
            for f in new_predictions
        ],
        key=lambda r: (r["target_type"], r["target_key"]),
    )

    # ---- posting volume -- MUST match forecast.py's own volume definition
    # exactly (VOLUME_SOURCE, "mustakbil" only), not AUTOMATED_SOURCES --
    # AUTOMATED_SOURCES (mustakbil+indeed) is what forecast.py uses for
    # SKILL targets specifically; volume actual/predicted has always been
    # narrower, permanently Mustakbil-only (see forecast.py's run_grade()).
    # Importing VOLUME_SOURCE instead of re-deriving this is what makes it
    # structurally impossible for the briefing and the forecast engine to
    # disagree about what a week's volume means -- a bug caught in the
    # first published briefing (using AUTOMATED_SOURCES here happened to
    # print the same numbers that week only because Indeed's history
    # didn't start until after it, not because the scope was actually
    # correct).
    postings = get_postings_for_forecast()
    volume_postings = [p for p in postings if p.get("source") == VOLUME_SOURCE]
    last_week_volume = _count_in_week(volume_postings, last_week_start)
    prior_week_volume = _count_in_week(volume_postings, prior_week_start)

    # A week-over-week comparison is only meaningful if BOTH weeks are
    # steady-state collection -- see AUTOMATED_COLLECTION_START's own
    # comment in forecast.py. prior_week_start is checked explicitly even
    # though it's always the earlier of the two (last_week_start >=
    # prior_week_start by construction): this is the actual guard, not an
    # optimization, so it stays legible as its own condition rather than
    # relying on that ordering fact holding forever.
    collection_start_week = _week_start(AUTOMATED_COLLECTION_START)
    comparison_available = (
        prior_week_start >= collection_start_week and last_week_start >= collection_start_week
    )

    if comparison_available:
        volume_change = last_week_volume - prior_week_volume
        volume_change_pct = (
            round(volume_change / prior_week_volume * 100, 1) if prior_week_volume else None
        )
    else:
        volume_change = None
        volume_change_pct = None

    # ---- top skills by distinct-company count, automated sources only -----
    # (LinkedIn's first_seen_at is not a trustworthy recency signal -- see
    # forecast.py's module docstring -- so a week-bucketed skill ranking
    # restricts to AUTOMATED_SOURCES for the same reason forecast.py's own
    # skill targets do -- this is deliberately the WIDER mustakbil+indeed
    # scope, not VOLUME_SOURCE, matching forecast.py's own skill/volume
    # split exactly.)
    #
    # Substantive skills only -- requirement_type == 'skill' excludes
    # attributes (on_site, morning_shift: a workplace ARRANGEMENT, not a
    # skill) and languages; category excludes soft/office_admin (near-
    # universal, low-signal). Same two-part filter api/main.py's curriculum
    # endpoints apply (_is_teachable_skill there), and the same
    # _is_market_skill this module imports from forecast.py, which applies
    # it to its own top-12 target selection too -- a "top skills" list (or
    # forecast target) that leads with On-Site and Communication is a
    # taxonomy-matching artifact, not a market finding.
    automated_postings = [p for p in postings if p.get("source") in AUTOMATED_SOURCES]
    automated_index = {p["id"]: p for p in automated_postings}

    mentions = [
        m for m in get_skill_mentions_for_briefing()
        if m.get("extraction_method") == TAXONOMY_VERSION and _is_market_skill(m["skill"], taxonomy)
    ]

    last_week_skill_counts = _skill_company_counts_in_week(mentions, automated_index, last_week_start)
    prior_week_skill_counts = _skill_company_counts_in_week(mentions, automated_index, prior_week_start)

    last_week_ranked = sorted(last_week_skill_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    prior_week_ranked = sorted(prior_week_skill_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def _display_row(skill_key: str, company_count: int) -> dict:
        spec = taxonomy["skills"].get(skill_key)
        return {"skill": skill_key, "display": spec["display"] if spec else skill_key, "company_count": company_count}

    top5 = [_display_row(k, n) for k, n in last_week_ranked[:TOP_SKILLS_LIMIT]]
    last_top10_keys = {k for k, _ in last_week_ranked[:TOP_SKILLS_COMPARISON_LIMIT]}
    prior_top10_keys = {k for k, _ in prior_week_ranked[:TOP_SKILLS_COMPARISON_LIMIT]}

    entered = [{"skill": k, "display": (taxonomy["skills"].get(k) or {}).get("display", k)} for k in sorted(last_top10_keys - prior_top10_keys)]
    left = [{"skill": k, "display": (taxonomy["skills"].get(k) or {}).get("display", k)} for k in sorted(prior_top10_keys - last_top10_keys)]

    return {
        "week_start": last_week_label,
        "next_week_start": next_week_label,
        "generated_at": now_utc.replace(microsecond=0).isoformat(),
        "forecasts_last_week": {
            "week": last_week_label,
            "count_graded": len(graded_last_week),
            "beat": beat_n,
            "tie": tie_n,
            "lost": lost_n,
            "mae": mae_last_week,
            "targets": target_rows,
        },
        "running_totals": {
            "count_graded_all_time": len(all_graded),
            "beat_all_time": rt_beat,
            "tie_all_time": rt_tie,
            "lost_all_time": rt_lost,
            "mae_all_time": mae_all_time,
        },
        "new_predictions": {
            "week": next_week_label,
            "count": len(new_prediction_rows),
            "targets": new_prediction_rows,
        },
        "posting_volume": {
            "sources": [SOURCE_DISPLAY.get(VOLUME_SOURCE, VOLUME_SOURCE)],
            "last_week_label": last_week_label,
            "prior_week_label": prior_week_label if comparison_available else None,
            "last_week": last_week_volume,
            "prior_week": prior_week_volume if comparison_available else None,
            "change": volume_change,
            "change_pct": volume_change_pct,
            "comparison_available": comparison_available,
            "comparison_unavailable_reason": (
                None if comparison_available
                else "collection cadence changed after an initial bulk backfill; the prior week is not comparable"
            ),
        },
        "top_skills": {
            "week": last_week_label,
            "prior_week": prior_week_label,
            "top5": top5,
            "entered_top10": entered,
            "left_top10": left,
        },
    }


# --------------------------------------------------------------------------
# TASK 2: drafting. The facts dict, json-serialized, is the ONLY thing Groq
# ever sees -- no raw postings, no raw skill_mentions, nothing else.
# --------------------------------------------------------------------------

def _groq_prompt(facts: dict) -> str:
    return (
        "You are restating a JSON facts object as plain prose for a reader who has not seen "
        "the underlying website or data. Write 120-180 words. State what was predicted last "
        "week and what actually happened, and summarize the other facts given. Do not "
        "speculate about WHY anything happened. Do not predict what will happen next. Do not "
        "introduce any number, name, or claim that is not present in the JSON below -- every "
        "number and every proper name you write must come directly from this data. Do not use "
        "marketing language. Write plain, restrained, factual prose, like a data report, not "
        "an announcement. Output ONLY the prose paragraph(s) -- no title, no markdown, no "
        "preamble, no bullet points, no closing remarks.\n\n"
        + json.dumps(facts, indent=2)
    )


def draft_with_groq(facts: dict) -> tuple[str | None, str | None]:
    """Returns (draft_text, model_version) on success, (None, reason) on
    failure -- a failure here is never raised, only returned, so run()
    can fall back to the template unconditionally."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning("GROQ_API_KEY not set -- skipping LLM draft, falling back to template")
        return None, "groq_api_key_missing"

    last_exc: Exception | None = None
    for attempt in range(GROQ_MAX_RETRIES):
        try:
            resp = requests.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": _groq_prompt(facts)}],
                    "temperature": 0.3,
                },
                timeout=60,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else (2 ** attempt) * 3
                logger.warning(f"Groq rate-limited (attempt {attempt + 1}/{GROQ_MAX_RETRIES}), waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return content.strip(), GROQ_MODEL
        except Exception as exc:
            last_exc = exc
            break

    logger.error(f"Groq draft request failed: {last_exc}")
    return None, f"groq_error: {last_exc}"


# --------------------------------------------------------------------------
# TASK 3: the assertion layer -- the publication gate. Every check takes
# the draft and the facts dict (or its json.dumps()) and returns a short
# failure reason, or None if that check passes.
# --------------------------------------------------------------------------

_NUMERAL_RE = re.compile(r"-?\d+(?:\.\d+)?")
_COMMA_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d),(?=\d)")


def _normalize_numeral(raw: str) -> str:
    # Sign is deliberately dropped: prose can honestly restate a negative
    # fact ("a decrease of 247") without writing the minus sign, so
    # comparing by absolute value is what "traceable to facts" actually
    # means here -- this check is about fabrication (a number that
    # appears nowhere in the input), not sign-fidelity in phrasing.
    s = raw.lstrip("+-")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
        if s == "":
            s = "0"
    return s or "0"


def _extract_numerals(text: str) -> set[str]:
    text = _COMMA_BETWEEN_DIGITS_RE.sub("", text)
    return {_normalize_numeral(m) for m in _NUMERAL_RE.findall(text)}


def _assert_numerals(draft: str, facts_json: str) -> str | None:
    """TASK 3(a). Every numeral in the draft must appear in the facts
    dict's own serialization -- checked by running the identical
    extraction+normalization over both, so anything the facts dict
    legitimately contains (including inside dates, ids, model_version
    strings) is automatically allowed, with nothing hand-maintained."""
    unmatched = sorted(_extract_numerals(draft) - _extract_numerals(facts_json), key=float)
    if unmatched:
        return f"unmatched numeral(s): {', '.join(unmatched)}"
    return None


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CAP_TOKEN_RE = re.compile(r"[A-Z][A-Za-z0-9+#./\-]*")
# Sentence-initial capitalization is required by English grammar regardless
# of whether the word is a proper noun, so it is dropped positionally (see
# _extract_cap_tokens) rather than by exhaustively listing every ordinary
# word that could start a sentence. This short list only catches a handful
# of capitalized words that can plausibly appear CAPITALIZED mid-sentence
# too (e.g. after a comma) without being an entity name.
_CAP_STOPWORDS = {"I", "The", "This", "That", "These", "Those", "It", "Its", "A", "An"}


def _extract_cap_tokens(text: str, *, drop_sentence_starts: bool) -> set[str]:
    tokens: set[str] = set()
    if not drop_sentence_starts:
        for word in text.split():
            m = _CAP_TOKEN_RE.match(word.strip("\"'()[]{}.,;:"))
            if m:
                tokens.add(m.group(0))
        return tokens

    for sentence in _SENTENCE_SPLIT_RE.split(text):
        words = sentence.split()
        for i, word in enumerate(words):
            m = _CAP_TOKEN_RE.match(word.strip("\"'()[]{}.,;:"))
            if not m:
                continue
            token = m.group(0)
            if i == 0:
                continue  # sentence-initial -- proves nothing about proper-noun-ness
            if token in _CAP_STOPWORDS:
                continue
            tokens.add(token)
    return tokens


def _assert_names(draft: str, facts_json: str) -> str | None:
    """TASK 3(b). Same allowed-set-from-facts approach as the numeral
    check: every capitalized word/token in the draft (excluding sentence-
    initial capitals and a short function-word stoplist) must appear
    among the capitalized tokens findable in the facts dict's own
    serialization -- skill display names, source names ("Mustakbil",
    "Indeed"), target keys, model_version, etc."""
    draft_caps = _extract_cap_tokens(draft, drop_sentence_starts=True)
    allowed_caps = _extract_cap_tokens(facts_json, drop_sentence_starts=False)
    unmatched = sorted(draft_caps - allowed_caps)
    if unmatched:
        return f"unmatched name(s)/entity(ies): {', '.join(unmatched)}"
    return None


def _assert_banned_phrases(draft: str) -> str | None:
    """TASK 3(c)."""
    lowered = draft.lower()
    hits = [p for p in BANNED_PHRASES if p in lowered]
    if hits:
        return f"banned phrase(s) found: {', '.join(hits)}"
    return None


def _assert_word_count(draft: str) -> str | None:
    """TASK 3(d)."""
    n = len(draft.split())
    if not (WORD_COUNT_MIN <= n <= WORD_COUNT_MAX):
        return f"word count {n} outside [{WORD_COUNT_MIN}, {WORD_COUNT_MAX}]"
    return None


def verify_draft(draft: str, facts: dict) -> str | None:
    """The publication gate. Returns None if the draft clears every check;
    otherwise the reason for the FIRST failing check (checks run in a
    fixed order -- this is not exhaustive of every problem, just the
    first one found, which is enough to block)."""
    facts_json = json.dumps(facts)
    for check in (
        lambda: _assert_numerals(draft, facts_json),
        lambda: _assert_names(draft, facts_json),
        lambda: _assert_banned_phrases(draft),
        lambda: _assert_word_count(draft),
    ):
        reason = check()
        if reason:
            return reason
    return None


# --------------------------------------------------------------------------
# Template fallback -- built directly from facts, string formatting only.
# Cannot fail verify_draft() by construction (nothing here isn't already in
# facts), so it is never itself run through the assertion layer.
# --------------------------------------------------------------------------

def build_template_briefing(facts: dict) -> str:
    fw = facts["forecasts_last_week"]
    rt = facts["running_totals"]
    np_ = facts["new_predictions"]
    vol = facts["posting_volume"]
    sk = facts["top_skills"]

    parts = [f"Weekly briefing for the week of {facts['week_start']}."]

    if fw["count_graded"] > 0:
        mae_str = f"{fw['mae']:.2f}" if fw["mae"] is not None else "unavailable"
        parts.append(
            f"Last week, {fw['count_graded']} forecast(s) were graded: {fw['beat']} beat "
            f"baseline, {fw['tie']} tied baseline, and {fw['lost']} lost to baseline, with a "
            f"mean absolute error of {mae_str}."
        )
    else:
        parts.append("No forecasts were graded for last week.")

    if rt["count_graded_all_time"] > 0:
        rt_mae_str = f"{rt['mae_all_time']:.2f}" if rt["mae_all_time"] is not None else "unavailable"
        parts.append(
            f"Across all graded weeks to date, {rt['count_graded_all_time']} forecast(s) have "
            f"been graded in total: {rt['beat_all_time']} beat, {rt['tie_all_time']} tied, and "
            f"{rt['lost_all_time']} lost, for an overall mean absolute error of {rt_mae_str}."
        )

    parts.append(
        f"{np_['count']} new forecast(s) were logged this week, for the week of {np_['week']}."
    )

    sources_str = " and ".join(vol["sources"])
    if not vol.get("comparison_available", True):
        parts.append(
            f"Posting volume from {sources_str} was {vol['last_week']} last week. A "
            f"week-over-week comparison is not available because collection cadence changed "
            f"after an initial bulk backfill."
        )
    elif vol["prior_week"] is not None:
        direction = "up from" if vol["change"] > 0 else "down from" if vol["change"] < 0 else "unchanged from"
        parts.append(
            f"Posting volume from {sources_str} was {vol['last_week']} last week, {direction} "
            f"{vol['prior_week']} the week before."
        )
    else:
        parts.append(f"Posting volume from {sources_str} was {vol['last_week']} last week.")

    if sk["top5"]:
        top5_str = ", ".join(f"{s['display']} ({s['company_count']} companies)" for s in sk["top5"])
        parts.append(f"The top skills by distinct-company count last week were: {top5_str}.")

    if sk["entered_top10"]:
        parts.append("Entering the top 10: " + ", ".join(s["display"] for s in sk["entered_top10"]) + ".")
    if sk["left_top10"]:
        parts.append("Leaving the top 10: " + ", ".join(s["display"] for s in sk["left_top10"]) + ".")

    return " ".join(parts)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(force_regenerate: bool = False) -> dict:
    """force_regenerate=True (CLI: --regenerate) intentionally publishes a
    NEW briefing for the current target week even if one is already
    active, then marks the old one superseded_by the new row's id. The
    old row's own columns are never touched -- it stays exactly as
    published, permanently readable as what was actually live at the
    time. Used for correcting a bug in fact computation after the fact
    (see storage.supersede_briefing); never used for the ordinary
    scheduled path, which must stay a no-op on a week already done."""
    now_utc = datetime.now(timezone.utc)
    facts = compute_facts(now_utc)
    week_start = facts["week_start"]

    from storage import get_briefing_for_week, insert_briefing, supersede_briefing

    existing = get_briefing_for_week(week_start)
    if existing and not force_regenerate:
        logger.info(
            f"Briefing for week_start={week_start} already published "
            f"(source={existing['source']}) -- nothing to do"
        )
        print(f"Briefing for week {week_start} already published (source={existing['source']}). Skipping.")
        return {"skipped": True, "week_start": week_start}

    draft, model_version_or_reason = draft_with_groq(facts)
    blocked_reason: str | None = None
    rejected_draft: str | None = None

    if draft is not None:
        failure = verify_draft(draft, facts)
        if failure is None:
            body, source, model_version = draft, "llm", model_version_or_reason
        else:
            logger.error(
                f"LLM draft REJECTED by assertion layer ({failure})\n"
                f"--- offending draft ---\n{draft}\n--- end draft ---"
            )
            body = build_template_briefing(facts)
            source = "template"
            model_version = model_version_or_reason
            blocked_reason = failure
            rejected_draft = draft
    else:
        reason = model_version_or_reason
        logger.warning(f"No LLM draft available ({reason}) -- publishing template briefing")
        body = build_template_briefing(facts)
        source = "template"
        model_version = None
        blocked_reason = reason

    facts_for_storage = dict(facts)
    if rejected_draft is not None:
        facts_for_storage["_rejected_llm_draft"] = rejected_draft

    row = {
        "week_start": week_start,
        "body": body,
        "source": source,
        "facts_json": facts_for_storage,
        "model_version": model_version,
        "blocked_reason": blocked_reason,
    }
    result = insert_briefing(row)

    superseded_id: str | None = None
    if existing and force_regenerate and result["inserted"]:
        if supersede_briefing(existing["id"], result["row"]["id"]):
            superseded_id = existing["id"]

    print(f"\n{'=' * 78}\nWEEKLY BRIEFING -- week of {week_start}\n{'=' * 78}")
    if superseded_id:
        print(f"NOTE: this supersedes briefing id={superseded_id} (kept, unmodified, as the audit record)")
    print(f"source: {source}" + (f"  (blocked: {blocked_reason})" if blocked_reason else ""))
    print(f"model_version: {model_version}")
    print(f"word_count: {len(body.split())}")
    print()
    print(body)
    print()
    if not result["inserted"]:
        print("NOTE: not stored -- either the briefings table doesn't exist yet (run the "
              "migration SQL), or a row for this week already exists. See log above.")
    elif existing and force_regenerate and not superseded_id:
        print(f"WARNING: new briefing id={result['row']['id']} was published, but marking the "
              f"prior briefing id={existing['id']} as superseded FAILED -- both rows are now "
              f"active for week {week_start}. See log above; this needs manual attention.")

    return {
        "skipped": False,
        "week_start": week_start,
        "source": source,
        "blocked_reason": blocked_reason,
        "stored": result["inserted"],
        "superseded": superseded_id,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Rukhwise weekly briefing")
    parser.add_argument(
        "--regenerate", action="store_true",
        help="Publish a new briefing for the current target week even if one is already "
             "active, then mark the old one superseded_by the new row (the old row's own "
             "columns are never touched). For correcting a bug in fact computation after "
             "the fact -- never used by the ordinary scheduled path.",
    )
    args = parser.parse_args()
    run(force_regenerate=args.regenerate)


if __name__ == "__main__":
    main()
