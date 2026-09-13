"""Boundaries a member is approaching, found in the recipe that names them.

The Pension Code is a field of cliffs -- age 60, 20 years, 30 years, the Tier
1/2 hire date -- and a member four months from one is owed the sentence a good
advisor would say unprompted. Nothing in the system noticed them: Section
11-134 reduces an annuity by 0.25% for each month it begins before age 60, so
the standard member carries an 11% reduction that vanishes on a date nobody
ever told them about.
"""

from __future__ import annotations

from pension_chat.calculation.thresholds import find_thresholds

SPEC = {
    "parameters": {
        "age_60": {"value": 60, "source": {"section": "11-134", "subsection": "(a)"}},
        "minimum_service_years": {
            "value": 20,
            "source": {"section": "11-134", "subsection": "(a)"},
        },
        "maximum_salary_percentage": {
            "value": 0.8,
            "source": {"section": "11-134", "subsection": "(a)"},
        },
    },
    "applicability_predicates": [
        {
            "fact": "service_years",
            "operator": "ge",
            "value": 20,
            "source": {"section": "11-134", "subsection": "(a)"},
            "description": "at least 20 years of service",
        },
    ],
}

FACTS = {"age": 56, "service_years": 21.5, "months_before_age_60": 44}


def test_an_age_boundary_ahead_is_a_cliff():
    found = {t.fact: t for t in find_thresholds(SPEC, FACTS)}
    assert "age" in found
    assert found["age"].boundary == 60
    assert found["age"].current == 56
    # The exact count from the facts layer, not (60 - 56) * 12: the member is
    # 56 and some months, and 44 is the number the 11% reduction is built on.
    assert found["age"].distance_months == 44
    assert found["age"].section == "11-134"


def test_a_boundary_already_passed_is_not_a_cliff():
    """21.5 years is past the 20-year minimum. Saying so helps nobody."""
    found = {t.fact for t in find_thresholds(SPEC, FACTS)}
    assert "service_years" not in found


def test_a_service_boundary_ahead_is_a_cliff():
    early = {"age": 47, "service_years": 9.0, "months_before_age_60": 156}
    found = {t.fact: t for t in find_thresholds(SPEC, early, horizon_months=240)}
    assert found["service_years"].boundary == 20
    assert found["service_years"].current == 9.0


def test_an_age_cliff_falls_back_to_whole_years_without_an_exact_count():
    """An anonymous member who says only "I am 56" has no months on file."""
    found = {t.fact: t for t in find_thresholds(SPEC, {"age": 56, "service_years": 21.5})}
    assert found["age"].distance_months == 48


def test_a_boundary_beyond_the_horizon_is_not_reported():
    """A 47-year-old is thirteen years from 60. That is not news they can act on."""
    early = {"age": 47, "service_years": 9.0, "months_before_age_60": 156}
    found = {t.fact for t in find_thresholds(SPEC, early, horizon_months=60)}
    assert "age" not in found


def test_a_percentage_parameter_is_not_mistaken_for_a_boundary():
    """0.8 is a cap on the answer, not a date or a count the member approaches."""
    found = {t.fact for t in find_thresholds(SPEC, FACTS)}
    assert "maximum_salary_percentage" not in found


def test_nothing_is_found_without_facts_to_compare_against():
    assert find_thresholds(SPEC, {}) == []


def test_the_nearest_cliff_comes_first():
    spec = {
        "parameters": {
            "age_60": {"value": 60, "source": {"section": "11-134"}},
            "minimum_service_years": {"value": 25, "source": {"section": "11-134"}},
        }
    }
    facts = {"age": 56, "service_years": 24.0}
    order = [t.fact for t in find_thresholds(spec, facts)]
    assert order == ["service_years", "age"], "one year of service beats four years of age"
