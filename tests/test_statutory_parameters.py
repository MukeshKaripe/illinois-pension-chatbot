"""An Article's own thresholds are citable, not guessable.

A recipe's `parameters` already carry `{value, source: {section, subsection}}`,
because derivation refuses a constant without a citation and the validator
refuses a section that was never retrieved. So a normal retirement age of 60 is
sitting in the recipe with 11-134 attached to it -- while the member is told the
agent could not establish a retirement age.
"""

from __future__ import annotations

from pension_chat.calculation.statutory import FILLS, fills_from

PARAMETERS = {
    "age_60": {"value": 60, "source": {"section": "11-134", "subsection": "(a)"}},
    "reduction_rate": {"value": 0.0025, "source": {"section": "11-134"}},
}
RECIPE = {"spec": {"parameters": PARAMETERS}}


def test_a_retirement_age_the_code_states_can_fill_a_missing_age():
    fills = fills_from({"retirement_annuity": RECIPE})
    assert fills["age_at_annuity_start"].value == 60
    assert fills["age_at_annuity_start"].section == "11-134"
    assert fills["age_at_annuity_start"].subsection == "(a)"
    assert "11-134(a)" in fills["age_at_annuity_start"].basis


def test_a_rate_is_not_a_fact_about_the_member():
    """`reduction_rate` is arithmetic the recipe does for itself. Offering it as
    a member fact is how "maximum_salary_percentage" would become a salary."""
    fills = fills_from({"retirement_annuity": RECIPE})
    assert "reduction_rate" not in {f.parameter for f in fills.values()}


def test_a_parameter_with_no_citation_is_refused():
    """The whole point is that the member can go and read it."""
    assert fills_from({"x": {"spec": {"parameters": {"age_60": {"value": 60}}}}}) == {}


def test_the_code_never_supplies_a_salary_or_a_service_figure():
    """The Code states rates and periods. It does not contain this member."""
    from pension_chat.calculation.assumption_policy import NEVER_ASSUMABLE

    assert not set(FILLS.values()) & NEVER_ASSUMABLE


def test_no_recipes_means_no_fills():
    assert fills_from({}) == {}


def test_the_first_recipe_to_name_a_threshold_keeps_it():
    """Two formula types can both name a minimum age. Letting the later one win
    would make the answer's citation depend on dict ordering."""
    other = {"spec": {"parameters": {"minimum_age": {"value": 55, "source": {"section": "11-123"}}}}}
    fills = fills_from({"retirement_annuity": RECIPE, "early_annuity": other})
    assert fills["age_at_annuity_start"].section == "11-134"


async def test_a_cited_threshold_fills_the_gap_instead_of_blocking():
    """The whole point, end to end: the input resolves, it is not reported
    missing, and it is not counted as an assumption."""
    from pension_chat.agent import nodes

    state = {
        "question": "what would my annuity be?",
        "messages": [],
        "requires_calculation": True,
        "recipes": {
            "retirement_annuity": {
                "spec": {
                    "parameters": PARAMETERS,
                    "inputs": [
                        {"name": "age_at_annuity_start", "type": "integer", "required": True}
                    ],
                }
            }
        },
        "turn_index": 1,
    }
    out = await nodes.resolve_inputs(state)

    assert out["resolved_inputs"]["age_at_annuity_start"] == 60
    assert out["missing_inputs"] == []
    assert out["input_sources"]["age_at_annuity_start"] == "statutory_parameter"
    assert out["statutory_fills"][0]["section"] == "11-134"


async def test_a_gap_the_code_cannot_fill_is_still_reported():
    """A salary is not in the Code. The fill path must not quietly swallow it."""
    from pension_chat.agent import nodes

    state = {
        "question": "what would my annuity be?",
        "messages": [],
        "requires_calculation": True,
        "recipes": {
            "retirement_annuity": {
                "spec": {
                    "parameters": PARAMETERS,
                    "inputs": [
                        {"name": "final_average_salary", "type": "money", "required": True}
                    ],
                }
            }
        },
        "turn_index": 1,
    }
    out = await nodes.resolve_inputs(state)

    assert [m["name"] for m in out["missing_inputs"]] == ["final_average_salary"]
    assert out["statutory_fills"] == []
