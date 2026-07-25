"""Parses HEC/NCEAC computing-curriculum PDFs into curriculum_courses rows.

SCOPE LIMITATION -- state this everywhere this data is used: both source
documents (NCEAC BS Computing Disciplines 2023, HEC Computer Science 2025)
cover COMPUTING disciplines only (BS Computer Science, Software
Engineering, Artificial Intelligence, Data Science, Cyber Security,
Bioinformatics, Information Systems, Multimedia & Gaming, Information
Technology, Computer Engineering, and Associate Degree Computing). The
alignment index built from this data analyses computing education against
computing-sector market demand -- it says nothing about trades, healthcare,
education, or any other domain this project tracks.

  python curriculum.py
      Parses every PDF in data/curricula/, clears and rewrites
      curriculum_courses (a small, rarely-updated reference table -- full
      recomputation each run, not an incremental append), and prints a
      parse-coverage report: courses found per document/program, how many
      got a topics_raw match, and specific lines that looked like they
      should have parsed as a course row but didn't. Nothing is silently
      dropped -- unparsed candidate lines are logged, not skipped quietly.

      Then maps every course to taxonomy_v3 using the SAME matcher
      extract_skills.py uses on postings (extract_skills._SKILL_PATTERNS --
      one source of truth, not a reimplementation), checking course_title
      and topics_raw separately so a match's provenance (match_source:
      'title' or 'topics') is always traceable. Writes
      curriculum_skill_map and prints which skills were matched and which
      courses matched nothing at all.

Both PDFs are messy in different ways and are handled by dedicated parsers:

  BS Curriculum Computing Disciplines 2023 (10 BS programs + Associate
  Degree): each program has its own "Curriculum Model" table (course code
  -- a TEMPLATE pattern like "CS1xx", not a real unique code; the document
  itself never assigns literal codes -- title, domain, credit hours), and
  a SEPARATE shared "Course Contents" section (title-keyed blocks with
  Course Introduction / CLOs / Course Outline text) that many different
  programs' tables reference by title. Course rows and topic text are
  joined by normalized title, not by code, since code is not a reliable
  per-course key in this document.

  HEC Computer Science 2025 (BS Computer Science + Associate Degree
  Computing): course code doesn't appear anywhere in this document at all.
  Titles + credit hours come from "Scheme of Studies" semester tables and
  a "Pool of Electives" section (organized by specialization cluster);
  topic text comes from a separate "Course Learning Outcomes (CLOs)"
  section, again joined by normalized title.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent / "rukhwise_scraper"))

from config import setup_logging  # noqa: E402

logger = setup_logging()

import extract_skills  # noqa: E402 -- reuse the exact same matcher/taxonomy postings extraction uses, not a reimplementation

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path("data/curricula")

BS_2023_PATH = DATA_DIR / "BS Curriculm Computing Disciplines-2023.pdf"
CS_2025_PATH = DATA_DIR / "COMPUTER-SCIENCE.pdf"


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


# --------------------------------------------------------------------------
# BS Curriculum Computing Disciplines 2023
# --------------------------------------------------------------------------

# Program section boundaries -- found by locating each program's "Curriculum
# Model" heading page (see module docstring); end boundary is the next
# program's start page. Hardcoded to this specific PDF's layout, verified
# by hand against its table of contents and section headings -- if HEC
# revises this document's structure, these will need re-verifying, which
# is exactly what the parse-coverage report below is for.
_BS2023_PROGRAM_PAGES = [
    ("BS Computer Science", 16),
    ("BS Software Engineering", 19),
    ("BS Artificial Intelligence", 22),
    ("BS Data Science", 25),
    ("BS Cyber Security", 28),
    ("BS Bioinformatics", 31),
    ("BS Information Systems", 34),
    ("BS Multimedia and Gaming", 37),
    ("BS Information Technology", 40),
    ("BS Computer Engineering", 43),
    ("Associate Degree Computing", 46),
]

_BS2023_ROW_RE = re.compile(
    r"^\s*(?:\d+\s+\d*\s*|\.\s*)?"                        # leading row #+semester #, a bare "." bullet, or nothing
                                                             # (some elective/example rows have no leading marker at all)
    r"(?:([A-Z]{2,4}\d[\dx]*)\s+)?"                       # course code (template, e.g. CS1xx) -- some bonus-elective bullets omit it entirely
    r"(.+?)\s+"                                            # title (may carry a leading prereq-abbrev artifact)
    r"((?:Domain\s+)?(?:Core|Elective)|Maths|GER|EW|SS)\s+"  # domain label
    r"(\d+\s*\([\d\-]+\))\s*$"                              # credit hours, e.g. "4 (3-3)"
)
_STUDY_PLAN_MARKER_RE = re.compile(r"Suggested Semester|Study Plan for", re.IGNORECASE)
_LOOKS_LIKE_COURSE_ROW_RE = re.compile(r"\d\s*\(\d-\d\)")  # credit-hour shape, for flagging near-misses
_LEADING_ABBREV_RE = re.compile(r"^[A-Z]{2,6}\s+(?=[^\s&])")  # "HCI & Computer Graphics" is a real title, not a "HCI" prereq artifact -- '&' right after signals coordination, never a prereq-prefix pattern


def _clean_bs2023_title(raw_title: str) -> str:
    """Strips a leading prerequisite-abbreviation artifact (e.g. "PF
    Object Oriented Programming" -> "Object Oriented Programming") --
    real titles in this document are Title Case and never start with an
    all-caps token, so an all-caps leading token followed by more words
    is always this artifact, never part of the title itself."""
    m = _LEADING_ABBREV_RE.match(raw_title)
    if m and not raw_title.isupper():
        return raw_title[m.end():].strip()
    return raw_title.strip()


def _find_course_contents_start(pdf: "pdfplumber.PDF") -> int:
    for i in range(50, len(pdf.pages)):
        text = pdf.pages[i].extract_text() or ""
        if "Course Name:" in text:
            return i
    raise RuntimeError("Could not find the 'Course Contents' section (no page with 'Course Name:' found)")


def _parse_bs2023_topics(pdf: "pdfplumber.PDF", start_page: int) -> dict[str, str]:
    """title (normalized) -> topics_raw, scanned globally across every
    'Course Name: ... Course Introduction: ...' block from start_page to
    the end of the document, regardless of which named sub-section
    (Computing Core / Math / GenEd / a domain's electives / ...) it falls
    under -- that boundary doesn't matter for this join, only the title
    does."""
    text = "\n".join((pdf.pages[i].extract_text() or "") for i in range(start_page, len(pdf.pages)))

    block_re = re.compile(
        r"Course Name:\s*(.+?)\s*\n"
        r".*?Course Introduction:\s*"
        r"(.+?)"
        r"(?=\nCourse Name:|\nReference Materials|\Z)",
        re.DOTALL,
    )
    topics: dict[str, str] = {}
    for m in block_re.finditer(text):
        title, topic_text = m.group(1).strip(), " ".join(m.group(2).split())
        if title and topic_text:
            topics[_normalize_title(title)] = topic_text
    return topics


def parse_bs2023(path: Path) -> tuple[list[dict], dict]:
    """Returns (course_rows, coverage_report)."""
    rows: list[dict] = []
    unparsed_lines: list[tuple[str, str]] = []  # (program, line)
    now = datetime.now(timezone.utc).isoformat()

    with pdfplumber.open(path) as pdf:
        topics_by_title = _parse_bs2023_topics(pdf, _find_course_contents_start(pdf))

        boundaries = _BS2023_PROGRAM_PAGES + [(None, len(pdf.pages))]
        for idx in range(len(_BS2023_PROGRAM_PAGES)):
            program, start = boundaries[idx]
            _, end = boundaries[idx + 1]
            text = "\n".join((pdf.pages[i].extract_text() or "") for i in range(start, end))

            # The "Mapping of X Program" table is the authoritative, complete
            # course list; a "Suggested Semester/Study Plan" restating the
            # SAME courses (with slightly different wording on some titles --
            # not a reliable post-hoc dedup key) always follows it. Truncate
            # there so it's never scanned at all, rather than risking
            # double-counted or inconsistently-worded duplicate rows.
            plan_marker = _STUDY_PLAN_MARKER_RE.search(text)
            if plan_marker:
                text = text[:plan_marker.start()]

            for line in text.split("\n"):
                m = _BS2023_ROW_RE.match(line)
                if m:
                    code, raw_title, _domain, cr_hr = m.groups()
                    title = _clean_bs2023_title(raw_title)
                    topics = topics_by_title.get(_normalize_title(title))
                    rows.append({
                        "source_document": path.name,
                        "degree_program": program,
                        "course_code": code,
                        "course_title": title,
                        "credit_hours": cr_hr.strip(),
                        "topics_raw": topics,
                        "extracted_at": now,
                    })
                elif _LOOKS_LIKE_COURSE_ROW_RE.search(line):
                    unparsed_lines.append((program, line.strip()))

    matched_topics = sum(1 for r in rows if r["topics_raw"])
    coverage = {
        "total_courses": len(rows),
        "by_program": {p: sum(1 for r in rows if r["degree_program"] == p) for p, _ in _BS2023_PROGRAM_PAGES},
        "matched_topics": matched_topics,
        "unmatched_topics": len(rows) - matched_topics,
        "unparsed_lines": unparsed_lines,
        "distinct_topics_blocks_found": len(topics_by_title),
    }
    return rows, coverage


# --------------------------------------------------------------------------
# HEC Computer Science 2025
# --------------------------------------------------------------------------

_CS2025_ELECTIVE_LIST_RE = re.compile(r"^\s*(\d+)[\.\)]?\s+([A-Za-z*].+?)\s+([\(\)\d\+\-/]+)\s*$")
# The Scheme of Studies table's row order (title, then credit, then
# category) extracts cleanly on some semester pages but not others (a
# pdfplumber word-order quirk on certain column layouts -- see module
# docstring's note on this being a genuinely messy source). This pattern
# catches the well-ordered case; rows on a garbled page are reported as
# unparsed rather than guessed at.
_CS2025_SCHEME_ROW_RE = re.compile(
    r"^\s*\d+\s+(.+?)\s+(\d+\s*\([\d\+\-]+\))\s+(General Education|Major|IDS[\s\-\w]*)\s*$"
)
_CS2025_CREDIT_TOKEN_RE = re.compile(r"^[\(\)\d\+\-/]+$")
_CS2025_CLO_HEADER_RE = re.compile(r"^\d+\.\s+([A-Za-z][^\n]{2,90})$")
_CS2025_LOOKS_LIKE_ROW_RE = re.compile(r"^\s*\d+[\.\)]?\s+\D.*\d[\+\-/\d\(\)]*\s*$")


def _find_page_containing(pdf: "pdfplumber.PDF", marker: str, start: int = 0) -> int | None:
    for i in range(start, len(pdf.pages)):
        if marker in (pdf.pages[i].extract_text() or ""):
            return i
    return None


def _parse_cs2025_topics(pdf: "pdfplumber.PDF") -> dict[str, str]:
    """title (normalized) -> topics_raw, from the 'Course Learning
    Outcomes (CLOs)' section. A numbered line immediately followed by a
    'By the end of this course...' line starts a new course's CLO block;
    text is captured until the next such numbered title line."""
    start = _find_page_containing(pdf, "COURSE LEARNING OUTCOMES (CLOS):")
    if start is None:
        return {}
    end = _find_page_containing(pdf, "CERTIFICATIONS", start=start) or len(pdf.pages)
    lines = "\n".join((pdf.pages[i].extract_text() or "") for i in range(start, end)).split("\n")

    topics: dict[str, str] = {}
    current_title: str | None = None
    current_lines: list[str] = []

    def _flush():
        if current_title and current_lines:
            topics[_normalize_title(current_title)] = " ".join(" ".join(current_lines).split())

    for i, line in enumerate(lines):
        header_m = _CS2025_CLO_HEADER_RE.match(line.strip())
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if header_m and next_line.startswith("By the end of this course"):
            _flush()
            current_title = header_m.group(1).strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line.strip())
    _flush()
    return topics


def _parse_cs2025_scheme_and_electives(pdf: "pdfplumber.PDF") -> tuple[list[tuple[str, str]], list[str]]:
    """Returns ([(title, credit_hours), ...], [unparsed_line, ...]) from
    the Scheme of Studies semester tables and the Pool of Electives
    section. Both list courses without any course code."""
    scheme_start = _find_page_containing(pdf, "SEMESTER-I")
    electives_start = _find_page_containing(pdf, "GUIDELINES FOR SPECIALIZATIONS") or _find_page_containing(pdf, "1: ")
    clo_start = _find_page_containing(pdf, "COURSE LEARNING OUTCOMES (CLOS):")

    found: list[tuple[str, str]] = []
    unparsed: list[str] = []

    if scheme_start is not None and electives_start is not None:
        text = "\n".join((pdf.pages[i].extract_text() or "") for i in range(scheme_start, electives_start))
        for line in text.split("\n"):
            m = _CS2025_SCHEME_ROW_RE.match(line)
            if m:
                title, credit, _category = m.groups()
                found.append((title.strip(), credit.strip()))
                continue
            m2 = _CS2025_ELECTIVE_LIST_RE.match(line)
            if m2:
                _, title, credit = m2.groups()
                found.append((title.strip(), credit.strip()))
                continue
            if _CS2025_LOOKS_LIKE_ROW_RE.match(line) and "Total Credits" not in line:
                unparsed.append(line.strip())

    if electives_start is not None and clo_start is not None:
        text = "\n".join((pdf.pages[i].extract_text() or "") for i in range(electives_start, clo_start))
        for line in text.split("\n"):
            m = _CS2025_ELECTIVE_LIST_RE.match(line)
            if m:
                _, title, credit = m.groups()
                found.append((title.strip(), credit.strip()))
            elif _CS2025_LOOKS_LIKE_ROW_RE.match(line) and "Total Credits" not in line:
                unparsed.append(line.strip())

    return found, unparsed


def parse_cs2025(path: Path) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    with pdfplumber.open(path) as pdf:
        topics_by_title = _parse_cs2025_topics(pdf)
        found, unparsed_lines = _parse_cs2025_scheme_and_electives(pdf)

        seen_titles: set[str] = set()
        for title, credit in found:
            norm = _normalize_title(title)
            if norm in seen_titles:
                continue  # same elective can legitimately appear once per cluster list; dedupe by title
            seen_titles.add(norm)
            rows.append({
                "source_document": path.name,
                "degree_program": "BS Computer Science",
                "course_code": None,
                "course_title": title,
                "credit_hours": credit,
                "topics_raw": topics_by_title.get(norm),
                "extracted_at": now,
            })

    matched_topics = sum(1 for r in rows if r["topics_raw"])
    coverage = {
        "total_courses": len(rows),
        "matched_topics": matched_topics,
        "unmatched_topics": len(rows) - matched_topics,
        "unparsed_lines": [("BS Computer Science", ln) for ln in unparsed_lines],
        "distinct_topics_blocks_found": len(topics_by_title),
    }
    return rows, coverage


# --------------------------------------------------------------------------
# TASK 2: map courses to taxonomy_v3 (same matcher as postings extraction)
# --------------------------------------------------------------------------

def match_courses_to_taxonomy(courses: list[dict]) -> list[dict]:
    """courses: rows as returned by store_curriculum_courses() (must have
    'id', 'course_title', 'topics_raw'). Returns curriculum_skill_map rows
    -- {"course_id", "skill", "match_source"}. title and topics_raw are
    checked SEPARATELY (not concatenated) so a skill matched via both
    produces two rows, one per source, preserving where the evidence
    actually came from."""
    rows: list[dict] = []
    for course in courses:
        title_text = (course.get("course_title") or "").lower()
        topics_text = (course.get("topics_raw") or "").lower()
        for skill_key, _category, pattern in extract_skills._SKILL_PATTERNS:
            if title_text and pattern.search(title_text):
                rows.append({"course_id": course["id"], "skill": skill_key, "match_source": "title"})
            if topics_text and pattern.search(topics_text):
                rows.append({"course_id": course["id"], "skill": skill_key, "match_source": "topics"})
    return rows


def print_mapping_report(courses: list[dict], skill_map_rows: list[dict]) -> None:
    matched_course_ids = {r["course_id"] for r in skill_map_rows}
    skill_counts: dict[str, int] = defaultdict(int)
    for r in skill_map_rows:
        skill_counts[r["skill"]] += 1

    print(f"\n{'=' * 78}\nTASK 2 -- COURSE-TO-TAXONOMY MAPPING\n{'=' * 78}")
    print(f"Courses: {len(courses)}, matched >=1 skill: {len(matched_course_ids)}, matched nothing: {len(courses) - len(matched_course_ids)}")
    print(f"Distinct skills matched: {len(skill_counts)}")
    print(f"Total (course, skill, source) rows: {len(skill_map_rows)}")

    print(f"\nTop 20 matched skills by distinct course count:")
    by_skill_courses: dict[str, set[str]] = defaultdict(set)
    for r in skill_map_rows:
        by_skill_courses[r["skill"]].add(r["course_id"])
    for skill, course_ids in sorted(by_skill_courses.items(), key=lambda kv: -len(kv[1]))[:20]:
        print(f"  {skill:<32} {len(course_ids)} courses")

    unmatched = [c for c in courses if c["id"] not in matched_course_ids]
    print(f"\nCourses matching nothing ({len(unmatched)} total, showing up to 30):")
    for c in unmatched[:30]:
        print(f"  [{c['degree_program']}] {c['course_title']}")
    if len(unmatched) > 30:
        print(f"  ... and {len(unmatched) - 30} more (not printed)")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def print_coverage_report(doc_name: str, coverage: dict) -> None:
    print(f"\n{'=' * 78}\nPARSE COVERAGE -- {doc_name}\n{'=' * 78}")
    print(f"Total courses extracted: {coverage['total_courses']}")
    if "by_program" in coverage:
        for program, n in coverage["by_program"].items():
            print(f"  {program:<32} {n}")
    print(f"Topics matched: {coverage['matched_topics']} / {coverage['total_courses']}")
    print(f"Distinct topic blocks found in document: {coverage['distinct_topics_blocks_found']}")
    unparsed = coverage["unparsed_lines"]
    print(f"Lines that looked like a course row but did NOT parse: {len(unparsed)}")
    for program, line in unparsed[:30]:
        print(f"    [{program}] {line!r}")
    if len(unparsed) > 30:
        print(f"    ... and {len(unparsed) - 30} more (not printed)")


def run() -> None:
    from storage import clear_curriculum_data, store_curriculum_courses, store_curriculum_skill_map

    print(
        "\nSCOPE LIMITATION: both source documents cover COMPUTING disciplines only. "
        "This analyses computing education against computing-sector demand, not the whole market.\n"
    )

    all_rows: list[dict] = []

    bs_rows, bs_coverage = parse_bs2023(BS_2023_PATH)
    all_rows.extend(bs_rows)
    print_coverage_report(BS_2023_PATH.name, bs_coverage)

    cs_rows, cs_coverage = parse_cs2025(CS_2025_PATH)
    all_rows.extend(cs_rows)
    print_coverage_report(CS_2025_PATH.name, cs_coverage)

    print(f"\n{'=' * 78}\nTOTAL: {len(all_rows)} course rows across both documents\n{'=' * 78}")

    clear_curriculum_data()
    inserted_courses = store_curriculum_courses(all_rows)
    logger.info(f"Inserted {len(inserted_courses)} curriculum_courses rows")

    skill_map_rows = match_courses_to_taxonomy(inserted_courses)
    write_result = store_curriculum_skill_map(skill_map_rows)
    logger.info(f"Inserted {write_result['inserted']} curriculum_skill_map rows")

    print_mapping_report(inserted_courses, skill_map_rows)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
