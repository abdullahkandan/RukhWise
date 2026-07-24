"""Two-stage job-title normalization against a controlled family
vocabulary (job_families.yaml). This module only implements the
matching/scoring logic; the vocabulary itself is a fixed, hand-approved
list -- see that file's own header. Never invents a family.

Stage 1 (rule, title): seniority prefixes are stripped from the title
first (see _strip_seniority_prefix), then keyword-matched against the
posting's OWN domain's families first (a posting's domain massively
narrows the candidate family list -- see domains.yaml/domain_classifier.py
for where that field comes from), plus the always-included cross_domain
families. If nothing matches there, a second, UNSCOPED pass checks every
family regardless of domain -- a posting's domain classification can be
imperfect, or a title can genuinely belong to a family outside its own
domain. First match wins; a tie between two families (equal keyword-hit
count) is left unresolved rather than guessed, same principle as
domain_classifier.py's classify_title().

Stage 2 (LLM): whatever stage 1 misses is batched to Groq and given ONLY
the controlled family list plus 'unmatched' as valid answers -- the
prompt is explicit that inventing a family outside that list is not
allowed, and any response naming something else is treated as unmatched
regardless of what the model intended. Below LLM_CONFIDENCE_THRESHOLD
also stays unmatched; the confidence is never overridden upward.

Every posting gets exactly one family_method:
  rule       -- stage 1 (domain-scoped or unscoped) assigned a real family
  llm        -- stage 2 assigned a real family from the closed vocabulary
  unmatched  -- neither stage produced a confident match; job_family is
                NULL (there is no residual 'unmatched' family value --
                unlike domains.yaml's 'other', this vocabulary is a
                curated approved list, not a taxonomy with a catch-all
                member, so an unmatched posting has no family, not a
                fake one)
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
import yaml

from config import setup_logging

logger = setup_logging()

JOB_FAMILIES_PATH = Path(__file__).parent / "job_families.yaml"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BATCH_SIZE = 20
GROQ_MAX_RETRIES = 5
GROQ_BATCH_DELAY_SECONDS = 2.5
LLM_CONFIDENCE_THRESHOLD = 0.6

# Stripped only from the START of a title (a true prefix), never mid-title
# or end-of-title -- this is what correctly leaves "Executive Assistant"
# untouched (the role IS "assistant") while still reducing "Assistant
# Accountant" to "Accountant" (a genuine seniority modifier there). "head
# of" is a two-word prefix; the rest are single words. Applied repeatedly
# so stacked prefixes ("Senior Lead Engineer") are fully stripped.
_SENIORITY_PREFIXES = ["senior", "junior", "lead", "assistant", "head of", "chief"]
_SENIORITY_PREFIX_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(p) for p in sorted(_SENIORITY_PREFIXES, key=len, reverse=True)) + r")\b[\s:.-]*",
    re.IGNORECASE,
)


def _strip_seniority_prefix(title: str) -> str:
    prev = None
    stripped = title
    while stripped != prev:
        prev = stripped
        stripped = _SENIORITY_PREFIX_RE.sub("", stripped)
    return stripped.strip()


def _load_families() -> list[dict]:
    with open(JOB_FAMILIES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["families"]


_FAMILIES = _load_families()
FAMILY_KEYS = [f["key"] for f in _FAMILIES]
CROSS_DOMAIN_KEYS = frozenset(f["key"] for f in _FAMILIES if f["domain"] == "cross_domain")


def _phrase_pattern(keyword: str) -> "re.Pattern[str]":
    tokens = keyword.strip().split()
    fragment = r"\s+".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<!\w)" + fragment + r"(?!\w)", re.IGNORECASE)


_PATTERNS_BY_FAMILY: dict[str, list["re.Pattern[str]"]] = {
    f["key"]: [_phrase_pattern(kw) for kw in f["keywords"]] for f in _FAMILIES
}
_FAMILIES_BY_DOMAIN: dict[str, list[str]] = {}
for _f in _FAMILIES:
    _FAMILIES_BY_DOMAIN.setdefault(_f["domain"], []).append(_f["key"])


def _count_family_keywords(text: str, family_keys: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in family_keys:
        n = sum(1 for p in _PATTERNS_BY_FAMILY[key] if p.search(text))
        if n > 0:
            counts[key] = n
    return counts


def _best_match(counts: dict[str, int]) -> tuple[str, float] | None:
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    top_key, top_count = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top_count:
        return None  # tie -- ambiguous, don't guess
    return top_key, 1.0


def classify_title_rule(title: str | None, domain: str | None) -> tuple[str, float] | None:
    """(family_key, confidence) via stage 1: domain-scoped search first
    (the posting's own domain's families + cross_domain), then an
    unscoped search across every family if that finds nothing."""
    if not title:
        return None
    stripped = _strip_seniority_prefix(title)
    if not stripped:
        return None

    domain_keys = list(_FAMILIES_BY_DOMAIN.get(domain, [])) if domain else []
    scoped_keys = list(dict.fromkeys(domain_keys + list(CROSS_DOMAIN_KEYS)))
    if scoped_keys:
        scoped_match = _best_match(_count_family_keywords(stripped, scoped_keys))
        if scoped_match:
            return scoped_match

    unscoped_match = _best_match(_count_family_keywords(stripped, FAMILY_KEYS))
    return unscoped_match


# --------------------------------------------------------------------------
# Stage 2: LLM (Groq), batched, closed vocabulary only.
# --------------------------------------------------------------------------

def _extract_json_array(content: str) -> str:
    """Same defensive extraction as skill_gap_discovery.py's fixed
    version -- prefers the LAST fenced ```json block if the model
    "thinks out loud" through multiple revisions, falling back to a
    bracket-depth scan (respecting string literals) for the first
    complete array when there are no fences at all."""
    text = content.strip()

    fence_matches = re.findall(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence_matches:
        return fence_matches[-1]

    start = text.find("[")
    if start == -1:
        return text

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _groq_prompt(payload: list[dict]) -> str:
    return (
        "Classify each job title below into exactly ONE job family from this FIXED, CLOSED list -- "
        "you may ONLY choose a family from this list, or 'unmatched'. Do not invent a family name "
        "that is not in this list under any circumstances: " + ", ".join(FAMILY_KEYS) + ". "
        "Seniority words (senior, junior, lead, assistant, head of, chief) do not change the family -- "
        "ignore them when deciding. Use 'unmatched' when the title genuinely does not fit any family "
        "in the list -- do not force a weak match. For each title, return its id, the assigned family "
        "(must be exactly one of the listed keys, or 'unmatched'), and a confidence between 0 and 1. "
        "Respond as a JSON array of objects with keys: id, family, confidence.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def classify_via_llm(postings: list[dict]) -> dict[str, tuple[str | None, float | None]]:
    """postings: [{"id", "title"}, ...]. Returns posting_id ->
    (family_key_or_None, confidence). Every input id is always present --
    missing/failed/low-confidence/invalid-family all resolve to
    (None, confidence-or-None), never silently dropped."""
    results: dict[str, tuple[str | None, float | None]] = {}
    if not postings:
        return results

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning(f"GROQ_API_KEY not set -- stage 2 (LLM) skipped, {len(postings)} remaining title(s) stay unmatched")
        return {p["id"]: (None, None) for p in postings}

    batches = [postings[i:i + GROQ_BATCH_SIZE] for i in range(0, len(postings), GROQ_BATCH_SIZE)]
    for batch_index, batch in enumerate(batches):
        if batch_index > 0:
            time.sleep(GROQ_BATCH_DELAY_SECONDS)

        payload = [{"id": p["id"], "title": p.get("title") or ""} for p in batch]

        parsed = None
        last_exc: Exception | None = None
        for attempt in range(GROQ_MAX_RETRIES):
            try:
                resp = requests.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": GROQ_MODEL,
                        "messages": [{"role": "user", "content": _groq_prompt(payload)}],
                        "temperature": 0,
                    },
                    timeout=60,
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else (2 ** attempt) * 3
                    logger.warning(f"Groq batch {batch_index + 1}/{len(batches)} rate-limited (attempt {attempt + 1}/{GROQ_MAX_RETRIES}), waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                raw = resp.json()
                content = raw["choices"][0]["message"]["content"]
                parsed = json.loads(_extract_json_array(content))
                break
            except Exception as exc:
                last_exc = exc
                break

        if parsed is None:
            reason = last_exc if last_exc else "exhausted retries on 429 rate limiting"
            logger.error(f"Groq batch {batch_index + 1}/{len(batches)} failed ({len(batch)} titles): {reason}")
            for p in batch:
                results[p["id"]] = (None, None)
            continue

        batch_ids = {p["id"] for p in batch}
        for item in parsed:
            pid = item.get("id")
            if pid not in batch_ids:
                continue
            family = item.get("family")
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            # Closed-vocabulary enforcement: a family name the model invented
            # (not in FAMILY_KEYS) is treated exactly like 'unmatched', never
            # stored -- this is the hard guarantee "it may never invent a family."
            if family not in FAMILY_KEYS or confidence < LLM_CONFIDENCE_THRESHOLD:
                results[pid] = (None, confidence)
            else:
                results[pid] = (family, confidence)

        for p in batch:
            if p["id"] not in results:
                results[p["id"]] = (None, None)

    return results


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def classify_postings(postings: list[dict], with_llm: bool = True) -> list[dict]:
    """postings: [{"id", "title", "domain"}, ...]. Returns
    [{"id", "job_family", "family_method", "family_confidence"}, ...],
    one row per input posting."""
    results: dict[str, dict] = {}
    stage2_candidates: list[dict] = []

    stage1_count = 0
    for p in postings:
        pid = p["id"]
        rule_result = classify_title_rule(p.get("title"), p.get("domain"))
        if rule_result:
            family, confidence = rule_result
            results[pid] = {"id": pid, "job_family": family, "family_method": "rule", "family_confidence": confidence}
            stage1_count += 1
            continue
        stage2_candidates.append(p)

    logger.info(f"Stage 1 (title rules, domain-scoped then unscoped): {stage1_count}, remaining for stage 2 (LLM): {len(stage2_candidates)}")

    llm_results = classify_via_llm(stage2_candidates) if with_llm else {p["id"]: (None, None) for p in stage2_candidates}
    llm_assigned = 0
    for p in stage2_candidates:
        pid = p["id"]
        family, confidence = llm_results.get(pid, (None, None))
        if family is not None:
            method = "llm"
            llm_assigned += 1
        else:
            method = "unmatched"
        results[pid] = {"id": pid, "job_family": family, "family_method": method, "family_confidence": confidence}

    logger.info(f"Stage 2 (LLM) assigned a real family to {llm_assigned}/{len(stage2_candidates)}")

    return [results[p["id"]] for p in postings]
