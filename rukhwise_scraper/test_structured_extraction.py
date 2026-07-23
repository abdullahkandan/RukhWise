"""Tests for structured_extraction.py.

Run directly: python test_structured_extraction.py
Or via unittest discovery: python -m unittest rukhwise_scraper.test_structured_extraction

No pytest dependency -- matches this project's existing no-test-framework
convention (requirements.txt has no test runner).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from structured_extraction import (  # noqa: E402
    extract_degree_field,
    extract_degree_level,
    extract_experience_level,
    extract_experience_years,
    extract_has_certification,
    extract_structured_fields,
)


class DegreeLevelGuardTests(unittest.TestCase):
    """The mandatory guard test: bare bs/ms/ba/be/ma must not fire on the
    "MS Office"/"MS Excel" collision -- the same failure class
    taxonomy_v1.yaml already documents for bare "r", and the one the
    2026-07-23 drift report surfaced live (bare "m" proposed as a
    Master's credential across 10 postings)."""

    def test_ms_office_does_not_match_masters(self):
        text = "Proficiency in MS Office and MS Excel is required."
        self.assertIsNone(extract_degree_level(text))

    def test_ms_excel_alone_does_not_match_masters(self):
        text = "Advanced knowledge of MS Excel, including pivot tables and VLOOKUP."
        self.assertIsNone(extract_degree_level(text))

    def test_bare_ms_matches_when_adjacent_to_in_plus_field(self):
        text = "MS in Computer Science required."
        self.assertEqual(extract_degree_level(text), "masters")

    def test_bare_bs_matches_when_directly_followed_by_field(self):
        text = "BS Computer Science or equivalent."
        self.assertEqual(extract_degree_level(text), "bachelors")

    def test_bare_be_verb_does_not_match_bachelors(self):
        text = "Candidates must be able to work independently and be a team player."
        self.assertIsNone(extract_degree_level(text))

    def test_bare_ba_without_context_does_not_match(self):
        text = "Reporting to the GM, the BA team handles quarterly reviews."
        self.assertIsNone(extract_degree_level(text))

    def test_bare_ba_with_degree_context_matches(self):
        text = "BA degree or equivalent experience considered."
        self.assertEqual(extract_degree_level(text), "bachelors")


class DegreeLevelGraduateGuardTests(unittest.TestCase):
    """Live-observed after the first extract.py --all run: 27 of 128
    bachelors labels rested SOLELY on the bare 'graduate' alias, and one
    sampled case was an institution describing its own programs, not a
    job requirement."""

    def test_fresh_graduate_does_not_set_degree_level(self):
        # Live-observed: 8f6b093d-faec-4515-bc97-17ab56971042
        text = "...insights and suggest improvements. Fresh Graduate"
        self.assertIsNone(extract_degree_level(text))

    def test_recent_graduate_does_not_set_degree_level(self):
        text = "Recent graduate with strong analytical skills encouraged to apply."
        self.assertIsNone(extract_degree_level(text))

    def test_institution_describing_own_programs_rejected(self):
        # Live-observed: 7b67b04b-3c4c-46b6-95d8-aebe12008451
        text = "...offers a range of undergraduate and graduate programs designed to equip learners."
        self.assertIsNone(extract_degree_level(text))

    def test_graduate_of_university_rejected(self):
        text = "Should be a graduate of a reputable university with strong references."
        self.assertIsNone(extract_degree_level(text))

    def test_graduate_alumni_context_rejected(self):
        text = "Our graduate alumni network spans over 50 countries worldwide."
        self.assertIsNone(extract_degree_level(text))

    def test_bare_graduate_with_degree_word_matches(self):
        text = "Candidate must be a graduate with a relevant degree in the field."
        self.assertEqual(extract_degree_level(text), "bachelors")

    def test_graduate_in_named_field_matches(self):
        text = "Candidate must be a graduate in Computer Science or a related discipline."
        self.assertEqual(extract_degree_level(text), "bachelors")

    def test_graduate_degree_matches(self):
        text = "Graduate degree preferred but not mandatory."
        self.assertEqual(extract_degree_level(text), "bachelors")


class DegreeLevelIntermediateGuardTests(unittest.TestCase):
    """Live-observed: 3 of 6 'intermediate' labels were skill-proficiency
    language, not the Pakistani Intermediate (11th/12th grade) qualification."""

    def test_intermediate_math_concepts_rejected(self):
        # Live-observed: 99218ca2-64c6-45d7-8996-c31ae8601117
        text = "Strong command of basic and intermediate math concepts required."
        self.assertIsNone(extract_degree_level(text))

    def test_intermediate_proficiency_in_excel_rejected(self):
        text = "Intermediate proficiency in Microsoft Excel (Pivot Tables) required."
        self.assertIsNone(extract_degree_level(text))

    def test_basic_to_intermediate_sql_rejected(self):
        text = "Basic to intermediate SQL (must-have) for this analyst role."
        self.assertIsNone(extract_degree_level(text))

    def test_intermediate_english_rejected(self):
        text = "Candidate should have intermediate English communication skills."
        self.assertIsNone(extract_degree_level(text))

    def test_intermediate_with_qualification_context_matches(self):
        text = "Qualification: Intermediate/ Bachelor's would both be considered for this role."
        # "intermediate" is the LOWER level per DEGREE_LEVELS order, so it
        # correctly wins over "bachelor's" appearing in the same sentence.
        self.assertEqual(extract_degree_level(text), "intermediate")

    def test_intermediate_with_matric_context_matches(self):
        text = "Minimum qualification: Matric or Intermediate, whichever the candidate holds."
        self.assertEqual(extract_degree_level(text), "intermediate")

    def test_intermediate_inside_requirements_section_matches(self):
        text = "**Qualifications:**\n* Intermediate\n* Strong communication skills"
        self.assertEqual(extract_degree_level(text), "intermediate")

    def test_bare_intermediate_outside_requirements_section_rejected(self):
        text = "The intermediate stage of the project involves data cleaning and validation."
        self.assertIsNone(extract_degree_level(text))


class DegreeLevelSafeAliasTests(unittest.TestCase):
    def test_bachelor_s_degree_matches(self):
        text = "Bachelor's degree in Business Administration required."
        self.assertEqual(extract_degree_level(text), "bachelors")

    def test_curly_apostrophe_bachelor_s_matches(self):
        text = "Bachelor’s degree in Computer Science preferred."
        self.assertEqual(extract_degree_level(text), "bachelors")

    def test_phd_matches(self):
        text = "PhD in Chemistry preferred."
        self.assertEqual(extract_degree_level(text), "phd")

    def test_diploma_matches(self):
        text = "DAE in Electrical Engineering required."
        self.assertEqual(extract_degree_level(text), "diploma")

    def test_matriculation_matches(self):
        text = "Matriculation or equivalent required for this role."
        self.assertEqual(extract_degree_level(text), "matriculation")

    def test_no_degree_mentioned(self):
        text = "Must be a team player with strong communication skills."
        self.assertIsNone(extract_degree_level(text))

    def test_lowest_level_wins_when_multiple_stated(self):
        text = "Bachelor's or Master's degree required, PhD a plus."
        self.assertEqual(extract_degree_level(text), "bachelors")

    def test_masters_alone_does_not_downgrade(self):
        text = "Master's degree in Statistics required."
        self.assertEqual(extract_degree_level(text), "masters")


class DegreeFieldTests(unittest.TestCase):
    def test_captures_field_after_degree_in(self):
        text = "Bachelor's degree in Computer Science required."
        self.assertEqual(extract_degree_field(text), "computer science")

    def test_captures_field_after_bs_in(self):
        text = "BS in Marketing or related field."
        self.assertEqual(extract_degree_field(text), "marketing")

    def test_no_field_when_not_stated(self):
        text = "Bachelor's degree or equivalent qualification preferred."
        self.assertIsNone(extract_degree_field(text))

    def test_normalizes_whitespace_and_case_only(self):
        text = "Bachelor's degree in   Business   Administration required."
        self.assertEqual(extract_degree_field(text), "business administration")


class CertificationTests(unittest.TestCase):
    def test_certification_singular_detected(self):
        self.assertTrue(extract_has_certification("PMP certification required."))

    def test_certifications_plural_detected(self):
        self.assertTrue(extract_has_certification("Relevant certifications are a plus."))

    def test_no_certification_mentioned(self):
        self.assertFalse(extract_has_certification("Strong communication skills required."))


class ExperienceYearsTests(unittest.TestCase):
    """Description-sourced matches all have "experience" adjacent, so they
    pass the requirements-context guard; passed as (description, None)."""

    def test_exact_years(self):
        self.assertEqual(extract_experience_years("3 years of experience required.", None), (3, 3))

    def test_dash_range_years(self):
        self.assertEqual(extract_experience_years("2-3 years of experience.", None), (2, 3))

    def test_to_range_years(self):
        self.assertEqual(extract_experience_years("2 to 3 years of relevant experience.", None), (2, 3))

    def test_minimum_years(self):
        self.assertEqual(extract_experience_years("Minimum 5 years of experience.", None), (5, None))

    def test_min_dot_years(self):
        self.assertEqual(extract_experience_years("Min. 4 years experience required.", None), (4, None))

    def test_at_least_years(self):
        self.assertEqual(extract_experience_years("At least 4 years of experience required.", None), (4, None))

    def test_plus_years(self):
        self.assertEqual(extract_experience_years("3+ years of experience.", None), (3, None))

    def test_no_years_mentioned(self):
        self.assertEqual(extract_experience_years("Strong communication skills required.", None), (None, None))

    def test_range_priority_over_bare_pattern(self):
        # Would wrongly extract (3, 3) if the bare "N years" pattern won.
        self.assertEqual(extract_experience_years("3-5 years of experience needed.", None), (3, 5))


class ExperienceYearsSubjectAttributionTests(unittest.TestCase):
    """The mandatory guard test for numeric years: same failure class as
    the bare-"senior" guard. Live-observed after the first extract.py
    --all run: 8 of 10 postings with experience_min_years > 15 were the
    COMPANY's years in business, a candidate AGE threshold, or years of
    EDUCATION (the Pakistani HEC "18 Years of Education" convention for a
    PhD) -- none of which are the candidate's required work experience."""

    def test_experience_raw_trusted_unconditionally(self):
        # A bare, structured experience_raw value is never guarded --
        # it's a site-provided "years required" field, not free text.
        self.assertEqual(extract_experience_years(None, "3 Years"), (3, 3))

    def test_experience_raw_trusted_even_when_unusually_high(self):
        self.assertEqual(extract_experience_years(None, "21 Years"), (21, 21))

    def test_company_years_in_business_rejected(self):
        text = (
            "Zones, a Minority Business Enterprise (MBE) in business for over 35 years, "
            "specializes in Digital Workplace, Cloud & Data Center, Networking, Security."
        )
        self.assertEqual(extract_experience_years(text, None), (None, None))

    def test_company_heritage_years_rejected(self):
        text = "With our strong heritage of nearly 60 years, we are a responsible and diverse group."
        self.assertEqual(extract_experience_years(text, None), (None, None))

    def test_candidate_age_threshold_rejected(self):
        text = "Candidate should be 18 years or older (you will be required to show your CNIC)."
        self.assertEqual(extract_experience_years(text, None), (None, None))

    def test_candidate_age_range_rejected(self):
        text = "Presentable personality with a professional attitude. Age: 26-50 years. Please note."
        self.assertEqual(extract_experience_years(text, None), (None, None))

    def test_years_of_education_rejected(self):
        text = "Qualification: Ph D / M Phil (18 Years of Education) in the relevant discipline required."
        self.assertEqual(extract_experience_years(text, None), (None, None))

    def test_genuine_description_match_still_accepted(self):
        text = "Requirements: 10+ years of experience in database architecture and design."
        self.assertEqual(extract_experience_years(text, None), (10, None))

    def test_requirements_section_heading_accepted_without_adjacent_word(self):
        # No "experience"/"required"/"minimum" word directly adjacent to
        # the number -- only the section heading justifies the match.
        text = "**Requirements:** 3-5 years in a related field, strong communication skills."
        self.assertEqual(extract_experience_years(text, None), (3, 5))

    def test_description_match_rejected_outside_requirements_section(self):
        text = "About the company: founded 12 years ago, we have grown steadily every year."
        self.assertEqual(extract_experience_years(text, None), (None, None))

    def test_description_match_after_other_section_heading_rejected(self):
        # A requirements heading appears, but the number is under a LATER,
        # unrelated heading -- no longer "inside" the requirements section.
        text = "Requirements: strong communication skills. Benefits: paid leave after 5 years of service."
        self.assertEqual(extract_experience_years(text, None), (None, None))

    def test_clean_experience_raw_not_shadowed_by_description_company_age(self):
        # Regression: the old implementation concatenated description +
        # experience_raw and took the FIRST match in that combined string,
        # so a company-age mention earlier in the description could shadow
        # a clean experience_raw value entirely.
        description = "Zones has been in business for over 35 years, serving clients worldwide."
        self.assertEqual(extract_experience_years(description, "3 Years"), (3, 3))


class ExperienceLevelTests(unittest.TestCase):
    def test_fresh_graduate_phrase(self):
        self.assertEqual(extract_experience_level("Fresh graduate welcome to apply.", None, None), "fresh")

    def test_entry_level_phrase(self):
        self.assertEqual(extract_experience_level("This is an entry level position.", None, None), "fresh")

    def test_zero_years_is_fresh(self):
        self.assertEqual(extract_experience_level("0 years of experience required.", 0, 0), "fresh")

    def test_junior_from_years(self):
        self.assertEqual(extract_experience_level("2 years of experience.", 2, 2), "junior")

    def test_mid_from_years(self):
        self.assertEqual(extract_experience_level("4-5 years of experience.", 4, 5), "mid")

    def test_senior_from_years(self):
        self.assertEqual(extract_experience_level("7 years of experience.", 7, None), "senior")

    def test_senior_word_overrides_low_years(self):
        text = "Senior role, 2 years of relevant experience considered."
        self.assertEqual(extract_experience_level(text, 2, 2), "senior")

    def test_no_signal(self):
        self.assertIsNone(extract_experience_level("Strong communication skills required.", None, None))

    def test_senior_describing_colleague_does_not_override(self):
        # "senior" here describes who the candidate supports, not the
        # candidate's own required level -- live-observed false positive.
        text = "Provide administrative and secretarial support to senior management."
        self.assertEqual(extract_experience_level(text, 2, 2), "junior")

    def test_senior_reporting_to_does_not_override(self):
        text = "Reporting directly to a Senior Investment Analyst, the candidate will assist with research."
        self.assertEqual(extract_experience_level(text, 1, 1), "junior")

    def test_senior_stakeholders_does_not_override(self):
        text = "Confident voice when communicating with senior stakeholders in USA/UK markets."
        self.assertEqual(extract_experience_level(text, 3, 3), "mid")

    def test_senior_or_junior_role_still_matches(self):
        # No excluded before/after word adjacent -- should still count.
        text = "At this company, every role, whether senior or junior, plays a pivotal part."
        self.assertEqual(extract_experience_level(text, None, None), "senior")


class OrchestrationTests(unittest.TestCase):
    def test_combined_posting_extracts_all_fields(self):
        posting = {
            "description": (
                "Employment Type: Full-Time. Bachelor's degree in Computer Science required. "
                "3-5 years of experience. PMP certification is a plus."
            ),
            "experience_raw": None,
        }
        result = extract_structured_fields(posting)
        self.assertEqual(result["degree_level"], "bachelors")
        self.assertEqual(result["degree_field"], "computer science")
        self.assertTrue(result["has_certification"])
        self.assertEqual(result["experience_min_years"], 3)
        self.assertEqual(result["experience_max_years"], 5)
        self.assertEqual(result["experience_level"], "mid")

    def test_empty_posting_returns_all_none(self):
        result = extract_structured_fields({"description": None, "experience_raw": None})
        self.assertIsNone(result["degree_level"])
        self.assertIsNone(result["degree_field"])
        self.assertFalse(result["has_certification"])
        self.assertIsNone(result["experience_min_years"])
        self.assertIsNone(result["experience_max_years"])
        self.assertIsNone(result["experience_level"])

    def test_uses_experience_raw_too(self):
        posting = {"description": "Great team culture.", "experience_raw": "Minimum 6 years required."}
        result = extract_structured_fields(posting)
        self.assertEqual(result["experience_min_years"], 6)
        self.assertEqual(result["experience_level"], "senior")


if __name__ == "__main__":
    unittest.main(verbosity=2)
