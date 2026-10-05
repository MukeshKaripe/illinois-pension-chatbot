"""MEABF (Article 8) core retirement formulas, written by hand for reviewer sign-off.

Derivation stays the way the system learns a formula it has not seen. These are
the formulas nearly every MEABF question needs, fixed: every statutory number
quoted verbatim from the text it comes from, every one proved against figures
worked by hand (`tests/test_core_formulas_meabf.py`), and -- once a reviewer
approves them with `scripts/approve_core_formulas.py` -- reused on every
matching turn, so the figure no longer depends on a model reading "after
attaining age 62" correctly each time.

Readings a reviewer should confirm (they are interpretations, not arithmetic):

- **The formula is §8-138(g-1)'s 2.4% per year, capped at 80%.** (g-1) is an
  election ("may elect ... instead of any other retirement annuity"); for a
  member with at least 10 years it is the most favourable of the Article 8
  formulas at the post-2002 rate, which is what electing means in practice.
- **Tier 2 and Tier 3 use §1-160(b)'s 8-of-the-last-10-years average**, which
  applies "notwithstanding any other provision", in place of (g-1)'s own
  4-year definition. The engine's facts already publish that average for
  Tier 2 and 3 members under the generic name `final_average_salary`.
- **Tier 2: unreduced at 67; from 62, reduced one-half of 1% per full month
  under 67** (§1-160(c), (d)). **Tier 3: unreduced at 65; from 60, reduced
  one-half of 1% per full month under 65** (§1-160(c-5), (d-5)).
- **Tier 1 here is the age-60 formula only.** Retiring before 60 under
  §8-138(b) (20+ years, reduced 0.25% a month, waived at 30 years) is left to
  derivation, as are survivor, disability and refund benefits.
- **Months under the unreduced age are counted in calendar months** from the
  date of birth to the annuity start date, which is how the engine counts them.
"""

from __future__ import annotations

from typing import Any

G1 = {"section": "8-138", "subsection": "(g-1)"}
D = {"section": "1-160", "subsection": "(d)"}
D5 = {"section": "1-160", "subsection": "(d-5)"}

RATE = {
    "value": 0.024, "source": G1,
    "quote": "or 2.4% if withdrawal is on or after January 1, 2002, of final average salary "
             "for each year of service",
}
MAXIMUM = {"value": 0.8, "source": G1, "quote": "or 80% if withdrawal is on or after January 1, 2002"}

_INPUTS_SALARY_SERVICE = [
    {"name": "final_average_salary", "type": "money", "required": True,
     "description": "Final average salary under the rule for the member's tier.",
     "source_section": "member_facts"},
    {"name": "service_years", "type": "years", "required": True,
     "description": "Years of service credit.", "source_section": "member_facts"},
]
_INPUTS_DATES = [
    {"name": "birth_date", "type": "date", "required": True,
     "description": "Date of birth.", "source_section": "member_facts"},
    {"name": "annuity_start_date", "type": "date", "required": True,
     "description": "Date the annuity begins.", "source_section": "member_facts"},
]


def _unreduced() -> dict[str, Any]:
    """2.4% of final average salary per year of service, no more than 80% of it."""
    return {
        "operation": "min",
        "result_name": "unreduced_annuity",
        "operands": [
            {"operation": "percentage", "result_name": "accrued_annuity",
             "operands": [{"variable": "final_average_salary"},
                          {"operation": "multiply",
                           "operands": [{"variable": "service_years"}, {"variable": "accrual_rate"}]}]},
            {"operation": "percentage", "result_name": "maximum_annuity",
             "operands": [{"variable": "final_average_salary"}, {"variable": "maximum_percentage"}]},
        ],
    }


def _reduced(unreduced_age_param: str) -> dict[str, Any]:
    """The unreduced annuity less one-half of 1% for each full month under the unreduced age."""
    return {
        "operation": "multiply",
        "operands": [
            _unreduced(),
            {"operation": "subtract", "result_name": "reduction_factor",
             "operands": [
                 {"value": 1},
                 {"operation": "multiply",
                  "operands": [
                      {"operation": "max", "result_name": "full_months_under_unreduced_age",
                       "operands": [
                           {"value": 0},
                           {"operation": "subtract",
                            "operands": [
                                {"operation": "multiply",
                                 "operands": [{"variable": unreduced_age_param}, {"value": 12}]},
                                {"operation": "date_diff_months",
                                 "date_a": {"variable": "birth_date"},
                                 "date_b": {"variable": "annuity_start_date"}},
                            ]},
                       ]},
                      {"variable": "reduction_per_month"},
                  ]},
             ]},
        ],
    }


def _spec(formula_id: str, inputs: list[dict[str, Any]], parameters: dict[str, Any],
          calculation: dict[str, Any], predicates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "formula_id": formula_id,
        "execution_type": "declarative",
        "inputs": inputs,
        "parameters": parameters,
        "calculation": calculation,
        "applicability_predicates": predicates,
        "output_name": "annual_annuity",
        "output_type": "money",
    }


TIER_1 = {
    "article_number": 8, "tier": 1, "formula_type": "retirement_annuity",
    "formula_name": "Tier 1 retirement annuity at 60 or over: 2.4% per year, capped at 80% (Section 8-138(g-1))",
    "controlling_section": "8-138", "controlling_subsection": "(g-1)",
    "source_sections": ["8-138"],
    "selection_basis": "statutory_election",
    "spec": _spec(
        "8_retirement_annuity_tier1_core",
        _INPUTS_SALARY_SERVICE,
        {"accrual_rate": RATE, "maximum_percentage": MAXIMUM},
        {**_unreduced(), "result_name": "annual_annuity"},
        [
            {"fact": "service_years", "operator": "ge", "value": 10, "source": G1,
             "description": "at least 10 years of service",
             "quote": "an employee who has at least 10 years of service"},
            {"fact": "age_at_annuity_start", "operator": "ge", "value": 60, "source": G1,
             "description": "the annuity begins no earlier than age 60",
             "quote": "beginning no earlier than upon attainment of age 60"},
        ],
    ),
}

TIER_2 = {
    "article_number": 8, "tier": 2, "formula_type": "retirement_annuity",
    "formula_name": "Tier 2 retirement annuity: 2.4% per year, capped at 80%, reduced "
                    "one-half of 1% a month under 67 (Sections 8-138(g-1), 1-160(b)-(d))",
    "controlling_section": "8-138", "controlling_subsection": "(g-1)",
    "source_sections": ["8-138", "1-160"],
    "selection_basis": "statutory_election",
    "spec": _spec(
        "8_retirement_annuity_tier2_core",
        _INPUTS_SALARY_SERVICE + _INPUTS_DATES,
        {
            "accrual_rate": RATE, "maximum_percentage": MAXIMUM,
            "reduction_per_month": {
                "value": 0.005, "source": D,
                "quote": "reduced by one-half of 1% for each full month that the member's age is under age 67"},
            "unreduced_age": {"value": 67, "source": D,
                              "quote": "for each full month that the member's age is under age 67"},
        },
        {**_reduced("unreduced_age"), "result_name": "annual_annuity"},
        [
            {"fact": "service_years", "operator": "ge", "value": 10, "source": D,
             "description": "at least 10 years of service credit",
             "quote": "with at least 10 years of service credit"},
            {"fact": "age_at_annuity_start", "operator": "ge", "value": 62, "source": D,
             "description": "retiring at 62 or over",
             "quote": "retiring after attaining age 62"},
        ],
    ),
}

TIER_3 = {
    "article_number": 8, "tier": 3, "formula_type": "retirement_annuity",
    "formula_name": "Tier 3 retirement annuity: 2.4% per year, capped at 80%, reduced "
                    "one-half of 1% a month under 65 (Sections 8-138(g-1), 1-160(b), (c-5), (d-5))",
    "controlling_section": "8-138", "controlling_subsection": "(g-1)",
    "source_sections": ["8-138", "1-160"],
    "selection_basis": "statutory_election",
    "spec": _spec(
        "8_retirement_annuity_tier3_core",
        _INPUTS_SALARY_SERVICE + _INPUTS_DATES,
        {
            "accrual_rate": RATE, "maximum_percentage": MAXIMUM,
            "reduction_per_month": {
                "value": 0.005, "source": D5,
                "quote": "reduced by one-half of 1% for each full month that the member's age is under age 65"},
            "unreduced_age": {"value": 65, "source": D5,
                              "quote": "for each full month that the member's age is under age 65"},
        },
        {**_reduced("unreduced_age"), "result_name": "annual_annuity"},
        [
            {"fact": "service_years", "operator": "ge", "value": 10, "source": D5,
             "description": "at least 10 years of service credit",
             "quote": "with at least 10 years of service credit"},
            {"fact": "age_at_annuity_start", "operator": "ge", "value": 60, "source": D5,
             "description": "retiring at 60 or over",
             "quote": "who is retiring at age 60"},
        ],
    ),
}

CORE_FORMULAS: list[dict[str, Any]] = [TIER_1, TIER_2, TIER_3]
