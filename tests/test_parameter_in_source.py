"""A recipe's constants must be written in the text they cite.

On 2026-09-30 a derived Tier 2 recipe carried `0.0125` cited to §1-160(g).
§1-160 contains no 1.25% anywhere -- (g) is about Article 14 fire fighters; the
rate belongs to §1-161(g) and §1-162(g), optional benefits for other funds. The
validator checked only that the cited section had been retrieved, so the recipe
executed and would have been saved for reuse. A constant must now be found, in
one of the ways the Code writes numbers, in the subsection it cites.
"""
from __future__ import annotations

import pytest

from pension_chat.calculation.validator import RecipeValidator, value_in_text

FIRE_FIGHTERS_G = (
    "(g) The benefits in Section 14-110 apply if the person is a fire fighter in the fire "
    "protection service of a department, a security employee of the Department of Corrections"
)


@pytest.mark.parametrize("value, text", [
    (0.0125, "computed by multiplying 1.25% for each year of service credit"),
    (0.0125, "shall be 1 1/4% of final average salary"),
    (0.024, "equal to 2.2% if withdrawal is before January 1, 2002, or 2.4% if withdrawal is on or after"),
    (0.005, "reduced by one-half of 1% for each full month that the member's age is under age 67"),
    (0.005, "reduced by 1/2 of 1% for each month"),
    (0.8, "subject to a maximum of 75% of final average salary, or 80% if withdrawal is on or after"),
    (106800, "shall not exceed $106,800; however, that amount shall annually thereafter"),
    (67, "if he or she has attained age 67 and has at least 10 years"),
    (10, "has at least 10 years of service credit"),
    (10, "has at least ten years of service"),
    (0.016667, "1 2/3% for each year of service over 20 years"),
    (0.065, "beginning January 1, 1972, 6-1/2% of each payment of the salary"),
])
def test_values_the_code_writes_are_found(value, text) -> None:
    assert value_in_text(value, text)


@pytest.mark.parametrize("value, text", [
    (0.0125, FIRE_FIGHTERS_G),
    (67, "age 6700 is not a thing"),
    (0.024, "2.45% is a different rate"),
    (106800, "a limit of $106,8000"),
])
def test_values_that_are_not_there_are_not_found(value, text) -> None:
    assert not value_in_text(value, text)


def _spec(value: float, section: str = "1-160", subsection: str = "(g)") -> dict:
    return {
        "formula_id": "article_8_retirement_annuity_1-160",
        "execution_type": "declarative",
        "inputs": [{"name": "final_average_salary", "type": "money", "required": True},
                   {"name": "service_years", "type": "years", "required": True}],
        "parameters": {"rate": {"value": value, "source": {"section": section, "subsection": subsection}}},
        "calculation": {"op": "MULTIPLY", "operands": [{"variable": "rate"}, {"variable": "service_years"},
                                                      {"variable": "final_average_salary"}]},
        "output_name": "annual_annuity", "output_type": "money",
    }


def _codes(result) -> list[str]:
    return [e.code for e in result.errors]


def test_a_constant_missing_from_its_cited_subsection_blocks_the_recipe() -> None:
    result = RecipeValidator().validate(
        _spec(0.0125), ["1-160"], {"1-160"}, set(), strict=True,
        parameter_texts={"1-160": {"(g)": FIRE_FIGHTERS_G}},
    )
    assert "PARAMETER_NOT_IN_SOURCE" in _codes(result) and not result.is_valid


def test_a_constant_found_in_its_cited_subsection_passes_that_check() -> None:
    result = RecipeValidator().validate(
        _spec(0.024, "8-138", "(g-1)"), ["8-138"], {"8-138"}, set(), strict=True,
        parameter_texts={"8-138": {"(g-1)": "equal to 2.2% ... or 2.4% if withdrawal is on or after"}},
    )
    assert "PARAMETER_NOT_IN_SOURCE" not in _codes(result)


def test_it_blocks_even_outside_strict_mode() -> None:
    result = RecipeValidator().validate(
        _spec(0.0125), ["1-160"], {"1-160"}, set(), strict=False,
        parameter_texts={"1-160": {"(g)": FIRE_FIGHTERS_G}},
    )
    assert "PARAMETER_NOT_IN_SOURCE" in _codes(result)


def test_a_subsection_not_found_falls_back_to_the_whole_section() -> None:
    result = RecipeValidator().validate(
        _spec(0.024, "8-138", "(zz)"), ["8-138"], {"8-138"}, set(), strict=True,
        parameter_texts={"8-138": {"(g-1)": "or 2.4% if withdrawal is on or after"}},
    )
    assert "PARAMETER_NOT_IN_SOURCE" not in _codes(result)


def test_without_the_text_the_check_cannot_run_and_says_so() -> None:
    result = RecipeValidator().validate(_spec(0.0125), ["1-160"], {"1-160"}, set(), strict=True)
    assert "PARAMETER_NOT_IN_SOURCE" not in _codes(result)


@pytest.mark.asyncio
async def test_validate_recipes_blocks_a_recipe_whose_rate_its_citation_does_not_state(monkeypatch) -> None:
    from pension_chat.agent import nodes

    async def fetch(section_ids, articles=None):
        return {"1-160": [{"section": "1-160", "subsection_id": "(g)", "content": FIRE_FIGHTERS_G}]}

    monkeypatch.setattr(nodes, "fetch_sections", fetch)
    state = {
        "recipes": {"retirement_annuity": {"source": "derived", "spec": _spec(0.0125),
                                           "source_sections": ["1-160"], "section": "1-160"}},
        "retrieved_sections": [{"section": "1-160"}],
        "coverage": {"article": "8", "basis": "record"},
        "member_facts": {}, "resolved_inputs": {},
    }
    out = await nodes.validate_recipes(state)
    assert out["can_execute"] is False
    assert "PARAMETER_NOT_IN_SOURCE" in [e["code"] for e in out["validation_errors"]]
