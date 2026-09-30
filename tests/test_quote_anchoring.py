"""Every statutory number in a derived recipe is anchored to a verbatim quote.

"The constant appears somewhere in the cited subsection" stopped an invented
1.25%, and would still pass a real number lifted from the wrong provision, or a
real rate wired into the wrong clause. So a derivation now copies, for each
parameter and each numeric condition, the words it read the number from; code
checks the words are in the cited subsection, character for character (spacing,
case and typographic quotes aside), and that the number is in those words. The
model still reasons out the formula -- code checks each piece it built it from.
"""
from __future__ import annotations

import pytest

from pension_chat.calculation.validator import RecipeValidator, quote_in_text

G1 = (
    "(g-1) Instead of any other retirement annuity provided in this Article, an employee who has "
    "at least 10 years of service and withdraws from service on or after January 1, 1999 may elect "
    "to receive a retirement annuity for life, beginning no earlier than upon attainment of age 60, "
    "equal to 2.2% if withdrawal is before January 1, 2002, or 2.4% if withdrawal is on or after "
    "January 1, 2002, of final average salary for each year of service, subject to a maximum of 75% "
    "of final average salary if withdrawal is before January 1, 2002, or 80% if withdrawal is on or "
    "after January 1, 2002."
)
TEXTS = {"8-138": {"(g-1)": G1}}


@pytest.mark.parametrize("quote, found", [
    ("or 2.4% if withdrawal is on or after January 1, 2002", True),
    ("OR 2.4%   IF withdrawal is on or after\nJanuary 1, 2002", True),          # case and spacing
    ("beginning no earlier than upon attainment of age 60 ... equal to 2.2%", True),   # an elision
    ("equal to 2.4% of final average salary for each year of service", False),  # a paraphrase
    ("2.2% ... beginning no earlier than", False),                                 # fragments out of order
])
def test_a_quote_is_verbatim_or_it_is_not(quote, found) -> None:
    assert quote_in_text(quote, G1) is found


def _spec(params: dict, predicates: list | None = None) -> dict:
    return {
        "formula_id": "article_8_retirement_annuity_8-138", "execution_type": "declarative",
        "inputs": [{"name": "final_average_salary", "type": "money", "required": True},
                   {"name": "service_years", "type": "years", "required": True}],
        "parameters": params,
        "calculation": {"op": "MULTIPLY", "operands": [{"variable": "rate"}, {"variable": "service_years"},
                                                      {"variable": "final_average_salary"}]},
        "applicability_predicates": predicates or [],
        "output_name": "annual_annuity", "output_type": "money",
    }


def _rate(value: float, quote: str | None) -> dict:
    param = {"value": value, "source": {"section": "8-138", "subsection": "(g-1)"}}
    if quote is not None:
        param["quote"] = quote
    return {"rate": param}


QUOTE_CODES = {"PARAMETER_QUOTE_MISSING", "PREDICATE_QUOTE_MISSING", "QUOTE_NOT_IN_SOURCE",
               "VALUE_NOT_IN_QUOTE"}


def _codes(spec: dict, require: bool = True) -> list[str]:
    """The quote findings alone; the calculation tree here is only a stand-in."""
    result = RecipeValidator().validate(
        spec, ["8-138"], {"8-138"}, set(), strict=True, parameter_texts=TEXTS, require_quotes=require,
    )
    return [e.code for e in result.errors if e.code in QUOTE_CODES]


def test_an_anchored_rate_passes() -> None:
    assert _codes(_spec(_rate(0.024, "or 2.4% if withdrawal is on or after January 1, 2002"))) == []


def test_a_derived_rate_without_a_quote_is_refused() -> None:
    assert "PARAMETER_QUOTE_MISSING" in _codes(_spec(_rate(0.024, None)))


def test_an_approved_recipe_without_quotes_is_not_refused_for_that() -> None:
    # Recipes saved before anchoring, and ones a reviewer approved, carry none.
    assert "PARAMETER_QUOTE_MISSING" not in _codes(_spec(_rate(0.024, None)), require=False)


def test_a_quote_not_in_the_cited_subsection_is_refused() -> None:
    codes = _codes(_spec(_rate(0.024, "shall be computed by multiplying 2.4% for each year")))
    assert "QUOTE_NOT_IN_SOURCE" in codes


def test_a_real_quote_that_does_not_state_the_value_is_refused() -> None:
    # The words are genuine; the number is not in them -- the 2.2% clause, 2.4% used.
    codes = _codes(_spec(_rate(0.024, "equal to 2.2% if withdrawal is before January 1, 2002")))
    assert "VALUE_NOT_IN_QUOTE" in codes


def test_a_numeric_condition_is_anchored_too() -> None:
    age = {"fact": "age_at_annuity_start", "operator": "ge", "value": 60,
           "description": "no earlier than age 60", "source": {"section": "8-138", "subsection": "(g-1)"}}
    rate = _rate(0.024, "or 2.4% if withdrawal is on or after January 1, 2002")
    assert "PREDICATE_QUOTE_MISSING" in _codes(_spec(rate, [age]))
    anchored = {**age, "quote": "beginning no earlier than upon attainment of age 60"}
    assert _codes(_spec(rate, [anchored])) == []


@pytest.mark.asyncio
async def test_validate_recipes_requires_quotes_of_derived_recipes_only(monkeypatch) -> None:
    from pension_chat.agent import nodes

    async def fetch(section_ids, articles=None):
        return {"8-138": [{"section": "8-138", "subsection_id": "(g-1)", "content": G1}]}

    monkeypatch.setattr(nodes, "fetch_sections", fetch)

    def state(source: str) -> dict:
        return {"recipes": {"retirement_annuity": {"source": source, "spec": _spec(_rate(0.024, None)),
                                                   "source_sections": ["8-138"], "section": "8-138"}},
                "retrieved_sections": [{"section": "8-138"}], "coverage": {"article": "8", "basis": "record"},
                "member_facts": {}, "resolved_inputs": {}}

    derived = await nodes.validate_recipes(state("derived"))
    reused = await nodes.validate_recipes(state("registry"))
    assert "PARAMETER_QUOTE_MISSING" in [e["code"] for e in derived["validation_errors"]]
    assert "PARAMETER_QUOTE_MISSING" not in [e["code"] for e in reused["validation_errors"]]


@pytest.mark.asyncio
async def test_sections_cited_only_by_conditions_are_fetched_too(monkeypatch) -> None:
    from pension_chat.agent import nodes

    asked: list = []

    async def fetch(section_ids, articles=None):
        asked.extend(section_ids)
        return {}

    monkeypatch.setattr(nodes, "fetch_sections", fetch)
    age = {"fact": "age_at_annuity_start", "operator": "ge", "value": 67,
           "source": {"section": "1-160", "subsection": "(c)"}, "quote": "attained age 67"}
    recipes = {"r": {"source": "derived", "spec": _spec({}, [age])}}
    await nodes._parameter_texts(recipes)
    assert asked == ["1-160"]


def test_the_derivation_prompt_asks_for_verbatim_quotes() -> None:
    from pension_chat.agent import derivation

    assert '"quote"' in derivation._SYSTEM_PROMPT
    assert "verbatim" in derivation._SYSTEM_PROMPT.lower()


def test_an_unapproved_saved_formula_without_quotes_is_not_reused() -> None:
    # Saved before anchoring existed: re-derived under today's validation, so
    # its numbers get quotes. A reviewer's approval is the only exemption.
    from pension_chat.agent.nodes import _meets_current_validation

    unquoted = {"status": "agent_derived", "spec": _spec(_rate(0.024, None))}
    quoted = {"status": "agent_derived",
              "spec": _spec(_rate(0.024, "or 2.4% if withdrawal is on or after January 1, 2002"))}
    approved = {"status": "approved", "spec": _spec(_rate(0.024, None))}
    assert not _meets_current_validation(unquoted)
    assert _meets_current_validation(quoted)
    assert _meets_current_validation(approved)
