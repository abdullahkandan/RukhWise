"""Three-stage domain classification for postings.

Controlled vocabulary lives in domains.yaml (see that file's own header),
not hardcoded here -- this module only implements the matching/scoring
logic against whatever domains.yaml currently defines.

Stage 1 (rule, title): count keyword hits per domain in the posting
TITLE only. Highest count wins; a tie (including 0-0, i.e. no keyword at
all) is left unresolved and falls through to stage 2. Titles are short
and usually written in a conventional "Job Title" register, so this is
expected to be high precision and cover the obvious cases outright.

Stage 2 (rule, description): for postings stage 1 missed, count keyword
hits per domain in the DESCRIPTION. A domain only wins if its count meets
STAGE2_MIN_COUNT and beats the runner-up domain by at least
STAGE2_MIN_MARGIN -- a single incidental keyword mention, or two domains
close enough to be a coin flip, is not enough to assign a domain; those
postings fall through to stage 3 instead of guessing.

Stage 3 (LLM): whatever remains after stages 1-2 is batched to Groq
(title + first 400 chars of description, GROQ_BATCH_SIZE postings per
call) and asked for one domain from the controlled vocabulary plus a
confidence 0-1. Anything below LLM_CONFIDENCE_THRESHOLD stays 'other' --
the confidence is never overridden/forced upward, and it is still stored
(domain_confidence) even when the outcome is 'other', so a low-confidence
LLM guess is distinguishable from "the LLM was never reached at all."

Every posting gets exactly one of these domain_method values:
  rule_title        -- stage 1 assigned a real domain
  rule_description   -- stage 2 assigned a real domain
  llm                -- stage 3 assigned a real domain, from title+description
  llm_title_only     -- stage 3 assigned a real domain from TITLE ALONE (the
                         posting's description was blank/null at classification
                         time). Investigation found 67% of the original 'llm'
                         tier had blank descriptions, with the model still
                         self-reporting confidence up to 1.0 -- a model cannot
                         be grounded-confident from a two-word title, so this
                         method's domain_confidence is capped at
                         LLM_TITLE_ONLY_CONFIDENCE_CAP regardless of what the
                         model returned, the same principle applied to
                         forecasts without a baseline elsewhere in this project.
  unclassified        -- ended up 'other', regardless of which stage(s) were tried
Storing the method is the point: every classification is auditable, and
which stage is carrying the corpus is directly measurable.
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

DOMAINS_PATH = Path(__file__).parent / "domains.yaml"

STAGE2_MIN_COUNT = 2
STAGE2_MIN_MARGIN = 2

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BATCH_SIZE = 20
DESCRIPTION_SNIPPET_CHARS = 400
LLM_CONFIDENCE_THRESHOLD = 0.6
LLM_TITLE_ONLY_CONFIDENCE_CAP = 0.6  # a title-only guess is never stored as more confident than the acceptance threshold itself
GROQ_MAX_RETRIES = 5
GROQ_BATCH_DELAY_SECONDS = 2.5


def _load_domains() -> list[dict]:
    with open(DOMAINS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["domains"]


_DOMAINS = _load_domains()
DOMAIN_KEYS = [d["key"] for d in _DOMAINS]
_REAL_DOMAIN_KEYS = [k for k in DOMAIN_KEYS if k != "other"]


def _phrase_pattern(keyword: str) -> "re.Pattern[str]":
    """Same convention as extract_skills.py/structured_extraction.py:
    whole-word/phrase match, internal whitespace becomes \\s+ so a
    multi-word phrase still matches across a newline."""
    tokens = keyword.strip().split()
    fragment = r"\s+".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<!\w)" + fragment + r"(?!\w)", re.IGNORECASE)


_PATTERNS_BY_DOMAIN: dict[str, list["re.Pattern[str]"]] = {
    d["key"]: [_phrase_pattern(kw) for kw in d["keywords"]]
    for d in _DOMAINS
    if d["key"] != "other"
}


def _count_domain_keywords(text: str) -> dict[str, int]:
    """domain -> count of DISTINCT keywords matched (not raw occurrence
    count) -- a phrase repeated many times in one posting still only
    counts once, the same anti-templating stance extract_skills.py and
    drift.py already take elsewhere in this codebase. "Weight by keyword
    count" means weight by how many DIFFERENT keywords for that domain
    showed up, not how many times one of them repeats."""
    counts: dict[str, int] = {}
    for domain, patterns in _PATTERNS_BY_DOMAIN.items():
        n = sum(1 for p in patterns if p.search(text))
        if n > 0:
            counts[domain] = n
    return counts


def classify_title(title: str | None) -> tuple[str, float] | None:
    """(domain, confidence) from title keywords, or None if nothing
    matched or the top two domains tied (ambiguous -- don't guess)."""
    if not title:
        return None
    counts = _count_domain_keywords(title)
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    top_domain, top_count = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top_count:
        return None
    return top_domain, 1.0


def classify_description(description: str | None) -> tuple[str, float] | None:
    """(domain, confidence) from description keywords, requiring both a
    minimum count and a minimum margin over the runner-up domain. None if
    either bar isn't cleared. Confidence reflects the actual margin
    (top / (top + runner-up)), not a flat constant."""
    if not description:
        return None
    counts = _count_domain_keywords(description)
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    top_domain, top_count = ranked[0]
    runner_up_count = ranked[1][1] if len(ranked) > 1 else 0

    if top_count < STAGE2_MIN_COUNT:
        return None
    if (top_count - runner_up_count) < STAGE2_MIN_MARGIN:
        return None

    confidence = round(top_count / (top_count + runner_up_count), 2)
    return top_domain, confidence


# --------------------------------------------------------------------------
# Stage 3: LLM (Groq), batched. Same batching/parsing pattern drift.py's
# run_groq_pass already established (fence/prose-tolerant JSON extraction,
# per-batch failure logging rather than a single all-or-nothing call).
# --------------------------------------------------------------------------

def _extract_json_array(content: str) -> str:
    """Groq sometimes wraps the requested JSON array in markdown code
    fences and/or leading prose despite being asked for JSON only
    (observed repeatedly in this codebase's other Groq passes). Strips
    fences and extracts the substring from the first '[' to the last ']'
    rather than assuming the content is bare JSON."""
    text = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


def _groq_prompt(payload: list[dict]) -> str:
    return (
        "Classify each job posting below into exactly ONE domain from this fixed controlled "
        "list: " + ", ".join(_REAL_DOMAIN_KEYS) + ". Use 'other' only when none of the listed "
        "domains genuinely fit -- do not force a weak match. For each posting, return its id, "
        "the assigned domain (must be one of the listed keys, or 'other'), and a confidence "
        "between 0 and 1 reflecting how certain you are. Respond as a JSON array of objects "
        "with keys: id, domain, confidence.\n\n" + json.dumps(payload, ensure_ascii=False)
    )


def classify_via_llm(postings: list[dict]) -> dict[str, tuple[str, float | None]]:
    """postings: [{"id", "title", "description"}, ...]. Returns
    posting_id -> (domain, confidence). Every input posting_id is always
    present in the result -- missing/failed/low-confidence all resolve to
    ("other", confidence-or-None), never silently dropped."""
    results: dict[str, tuple[str, float | None]] = {}
    if not postings:
        return results

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning(
            f"GROQ_API_KEY not set -- stage 3 (LLM) skipped, {len(postings)} remaining "
            "posting(s) stay domain='other', method='unclassified'"
        )
        return {p["id"]: ("other", None) for p in postings}

    batches = [postings[i:i + GROQ_BATCH_SIZE] for i in range(0, len(postings), GROQ_BATCH_SIZE)]
    for batch_index, batch in enumerate(batches):
        if batch_index > 0:
            time.sleep(GROQ_BATCH_DELAY_SECONDS)

        payload = [
            {
                "id": p["id"],
                "title": p.get("title") or "",
                "description_snippet": (p.get("description") or "")[:DESCRIPTION_SNIPPET_CHARS],
            }
            for p in batch
        ]

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
                    logger.warning(
                        f"Groq batch {batch_index + 1}/{len(batches)} rate-limited "
                        f"(attempt {attempt + 1}/{GROQ_MAX_RETRIES}), waiting {wait}s"
                    )
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
            logger.error(f"Groq batch {batch_index + 1}/{len(batches)} failed ({len(batch)} postings): {reason}")
            for p in batch:
                results[p["id"]] = ("other", None)
            continue

        batch_ids = {p["id"] for p in batch}
        for item in parsed:
            pid = item.get("id")
            if pid not in batch_ids:
                continue
            domain = item.get("domain")
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            if domain not in _REAL_DOMAIN_KEYS or confidence < LLM_CONFIDENCE_THRESHOLD:
                results[pid] = ("other", confidence)
            else:
                results[pid] = (domain, confidence)

        for p in batch:
            if p["id"] not in results:
                results[p["id"]] = ("other", None)

    return results


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def classify_postings(postings: list[dict], with_llm: bool = True) -> list[dict]:
    """postings: [{"id", "title", "description"}, ...]. Returns
    [{"id", "domain", "domain_method", "domain_confidence"}, ...], one row
    per input posting, in the same stage-1 -> stage-2 -> stage-3 order
    this module's docstring describes."""
    results: dict[str, dict] = {}
    stage3_candidates: list[dict] = []

    stage1_count = 0
    stage2_count = 0

    for p in postings:
        pid = p["id"]
        title_result = classify_title(p.get("title"))
        if title_result:
            domain, confidence = title_result
            results[pid] = {"id": pid, "domain": domain, "domain_method": "rule_title", "domain_confidence": confidence}
            stage1_count += 1
            continue

        desc_result = classify_description(p.get("description"))
        if desc_result:
            domain, confidence = desc_result
            results[pid] = {"id": pid, "domain": domain, "domain_method": "rule_description", "domain_confidence": confidence}
            stage2_count += 1
            continue

        stage3_candidates.append(p)

    logger.info(
        f"Stage 1 (title rules): {stage1_count}, Stage 2 (description rules): {stage2_count}, "
        f"remaining for stage 3 (LLM): {len(stage3_candidates)}"
    )

    llm_results = classify_via_llm(stage3_candidates) if with_llm else {
        p["id"]: ("other", None) for p in stage3_candidates
    }
    llm_assigned = 0
    llm_title_only_assigned = 0
    for p in stage3_candidates:
        pid = p["id"]
        domain, confidence = llm_results.get(pid, ("other", None))
        title_only = not (p.get("description") or "").strip()
        if domain != "other" and title_only:
            method = "llm_title_only"
            confidence = min(confidence, LLM_TITLE_ONLY_CONFIDENCE_CAP) if confidence is not None else LLM_TITLE_ONLY_CONFIDENCE_CAP
            llm_title_only_assigned += 1
        elif domain != "other":
            method = "llm"
            llm_assigned += 1
        else:
            method = "unclassified"
        results[pid] = {"id": pid, "domain": domain, "domain_method": method, "domain_confidence": confidence}

    logger.info(
        f"Stage 3 (LLM) assigned a real domain to {llm_assigned + llm_title_only_assigned}/{len(stage3_candidates)} "
        f"({llm_title_only_assigned} of those from title alone, confidence capped at {LLM_TITLE_ONLY_CONFIDENCE_CAP})"
    )

    return [results[p["id"]] for p in postings]
