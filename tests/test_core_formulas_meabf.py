"""The MEABF core retirement formulas, written by hand for a reviewer's sign-off.

Derivation is probabilistic, and it got the most common MEABF question wrong in
a way no quote can catch: twice, a Tier 2 formula read "retiring after attaining
age 62 ... reduced ... for each full month ... under age 67" as `age > 62` and
paid a 62-year-old unreduced. These formulas are the fixed point: every number
quoted verbatim from the statute, every one proved here against a figure worked
by hand, and -- once a reviewer approves them -- reused on every matching turn.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from pension_chat.calculation.core_meabf import CORE_FORMULAS
from pension_chat.calculation.dsl_interpreter import DSLInterpreter
from pension_chat.calculation.validator import RecipeValidator, quote_in_text

STATUTES = Path(__file__).parents[1] / "data" / "statutes"
BY_TIER = {f["tier"]: f for f in CORE_FORMULAS}


def _annual(tier: int, **inputs) -> Decimal:
    result = DSLInterpreter().execute(BY_TIER[tier]["spec"], inputs)["result"]
    return Decimal(str(result)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def test_there_is_one_formula_per_tier() -> None:
    assert sorted(BY_TIER) == [1, 2, 3]
    assert all(f["article_number"] == 8 and f["formula_type"] == "retirement_annuity" for f in CORE_FORMULAS)


@pytest.mark.parametrize("tier, inputs, expected", [
    # Tier 2 at exactly 62 with 14 years (S4-007): 28,244.13 reduced 30% for 60 months under 67.
    (2, {"final_average_salary": 84059.90, "service_years": 14,
         "birth_date": "1964-09-30", "annuity_start_date": "2026-09-30"}, "19770.89"),
    # Tier 2 at 67 with 15 years on a flat 70,000 (S4-008): no reduction.
    (2, {"final_average_salary": 70000, "service_years": 15,
         "birth_date": "1959-09-30", "annuity_start_date": "2026-09-30"}, "25200.00"),
    # Tier 2 at 64 and 3 months: 33 full months under 67, 16.5%.
    (2, {"final_average_salary": 80000, "service_years": 11,
         "birth_date": "1962-06-30", "annuity_start_date": "2026-09-30"}, "17635.20"),
    # Tier 1 at 62 with 36 years (S4-009): 86.4% capped at 80%.
    (1, {"final_average_salary": 100000, "service_years": 36}, "80000.00"),
    # Tier 1 at 59-60 with 30.75 years on the 4-year average (S4-001's figure).
    (1, {"final_average_salary": 98328.60, "service_years": 30.75}, "72566.51"),
    # Tier 3 at 62 with 10 years: 36 months under 65, 18% off 15,600.
    (3, {"final_average_salary": 65000, "service_years": 10,
         "birth_date": "1964-09-30", "annuity_start_date": "2026-09-30"}, "12792.00"),
    # Tier 3 at 65: unreduced.
    (3, {"final_average_salary": 65000, "service_years": 10,
         "birth_date": "1961-09-30", "annuity_start_date": "2026-09-30"}, "15600.00"),
])
def test_each_formula_gives_the_hand_worked_figure(tier, inputs, expected) -> None:
    assert _annual(tier, **inputs) == Decimal(expected)


def _section(article: int, section: str) -> str:
    text = (STATUTES / f"article_{article}.txt").read_text(encoding="utf-8", errors="replace")
    start = text.find(f"(40 ILCS 5/{section})")
    end = text.find("(40 ILCS 5/", start + 10)
    return text[start:end]


def _anchors(formula: dict) -> list[dict]:
    spec = formula["spec"]
    return [*spec["parameters"].values(), *spec["applicability_predicates"]]


@pytest.mark.skipif(not STATUTES.exists(), reason="data/ is not in the repo")
@pytest.mark.parametrize("formula", CORE_FORMULAS, ids=lambda f: f"tier{f['tier']}")
def test_every_number_is_quoted_verbatim_from_the_statute(formula) -> None:
    for anchor in _anchors(formula):
        section = anchor["source"]["section"]
        assert anchor.get("quote"), anchor
        assert quote_in_text(anchor["quote"], _section(int(section.split("-")[0]), section)), anchor["quote"]


@pytest.mark.skipif(not STATUTES.exists(), reason="data/ is not in the repo")
@pytest.mark.parametrize("formula", CORE_FORMULAS, ids=lambda f: f"tier{f['tier']}")
def test_each_formula_passes_the_validator_a_new_derivation_must_pass(formula) -> None:
    cited = {a["source"]["section"] for a in _anchors(formula)}
    texts = {s: {"": re.sub(r"\s+", " ", _section(int(s.split("-")[0]), s))} for s in cited}
    result = RecipeValidator().validate(
        formula["spec"], formula["source_sections"], set(formula["source_sections"]), set(),
        strict=True, parameter_texts=texts, require_quotes=True,
    )
    assert result.is_valid, [(e.code, e.message) for e in result.errors]
