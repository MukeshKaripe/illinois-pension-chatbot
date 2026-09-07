"""Tests for machine-checkable statutory applicability.

The hole these close: a recipe declared who it applied to in one sentence of
prose, written against the facts of the member it was derived for. Nothing
re-tested that sentence when the recipe was reused, and the registry matched on
article, formula type and tier alone — so a recipe derived for a 56-year-old
with 21.5 years of service would have been handed unchanged to a member who
cannot reach the provision at all.

Evaluation is three-valued on purpose. A condition over a fact nobody supplied
is unknown, not false: an anonymous user who never stated a hire date has not
thereby failed an eligibility test, and refusing them an estimate for it would
be a worse answer, not a safer one.
"""

from __future__ import annotations

import pytest

from pension_chat.calculation.applicability import (
    NOT_SATISFIED,
    PARTIALLY_VERIFIED,
    UNVERIFIED,
    VERIFIED,
    evaluate_predicates,
    validate_predicate,
)
from pension_chat.calculation.dsl_types import RuleSpecification
from pension_chat.calculation.validator import RecipeValidator

SOURCE = {"section": "11-134", "subsection": "(a)"}

# The conditions Section 11-134(a) actually attaches to the elected 2.40%
# annuity: 20+ years of service, commencement no earlier than age 55.
MIN_ANNUITY = [
    {"fact": "service_years", "operator": "ge", "value": 20,
     "description": "20 or more years of service", "source": SOURCE},
    {"fact": "age", "operator": "ge", "value": 55,
     "description": "annuity to begin not earlier than age 55", "source": SOURCE},
]

MEMBER = {"age": 56, "service_years": 21.5, "employment_status": "active"}


# ── The gate ──────────────────────────────────────────────────────

def test_eligible_member_passes_every_condition():
    report = evaluate_predicates(MIN_ANNUITY, MEMBER)

    assert report.status == VERIFIED
    assert not report.blocks_execution
    assert len(report.satisfied) == 2


def test_member_short_of_the_service_threshold_is_blocked():
    """The reuse hazard: same article, same formula type, different member."""
    report = evaluate_predicates(MIN_ANNUITY, {"age": 56, "service_years": 9})

    assert report.status == NOT_SATISFIED
    assert report.blocks_execution
    assert [f["fact"] for f in report.failed] == ["service_years"]
    assert "20 or more years of service" in report.summary()


def test_member_below_the_commencement_age_is_blocked():
    report = evaluate_predicates(MIN_ANNUITY, {"age": 47, "service_years": 24})

    assert report.blocks_execution
    assert [f["fact"] for f in report.failed] == ["age"]


def test_unknown_facts_do_not_block():
    # An anonymous member who stated only their service. Not knowing their age
    # is not the same as knowing they are too young.
    report = evaluate_predicates(MIN_ANNUITY, {"service_years": 21.5})

    assert report.status == PARTIALLY_VERIFIED
    assert not report.blocks_execution
    assert [u["fact"] for u in report.unknown] == ["age"]


def test_a_recipe_declaring_nothing_is_unverified_not_blocked():
    report = evaluate_predicates([], MEMBER)

    assert report.status == UNVERIFIED
    assert not report.blocks_execution
    assert "no machine-checkable" in report.summary()


def test_a_malformed_predicate_proves_nothing_either_way():
    report = evaluate_predicates(
        [{"fact": "age", "operator": "roughly", "value": 55, "source": SOURCE}], MEMBER
    )

    assert not report.blocks_execution
    assert report.unknown and "not supported" in report.unknown[0]["reason"]


# ── Fact vocabulary ───────────────────────────────────────────────

def test_predicates_resolve_through_the_fact_aliases():
    # facts.py publishes age under several names, and a derivation may pick any
    # of them; the same is true of service.
    facts = {"attained_age": 56, "creditable_service_years": 21.5}

    assert evaluate_predicates(MIN_ANNUITY, facts).status == VERIFIED


@pytest.mark.parametrize(
    ("predicate", "facts", "expected"),
    [
        ({"fact": "withdrawal_date", "operator": "ge", "value": "2002-01-01"},
         {"withdrawal_date": "2026-09-07"}, VERIFIED),
        ({"fact": "withdrawal_date", "operator": "ge", "value": "2002-01-01"},
         {"withdrawal_date": "1998-03-01"}, NOT_SATISFIED),
        ({"fact": "employment_status", "operator": "eq", "value": "active"},
         {"employment_status": "active"}, VERIFIED),
        ({"fact": "employment_status", "operator": "in", "value": ["active", "on_leave"]},
         {"employment_status": "retired"}, NOT_SATISFIED),
        ({"fact": "age", "operator": "between", "value": [55, 60]},
         {"age": 56}, VERIFIED),
        ({"fact": "age", "operator": "between", "value": [55, 60]},
         {"age": 62}, NOT_SATISFIED),
    ],
)
def test_operators_over_dates_strings_and_ranges(predicate, facts, expected):
    assert evaluate_predicates([{**predicate, "source": SOURCE}], facts).status == expected


def test_a_date_is_never_compared_against_a_number():
    # Coercing "2002-01-01" to a number would silently compare nonsense; the
    # pair is reported as uncheckable instead.
    report = evaluate_predicates(
        [{"fact": "age", "operator": "ge", "value": "2002-01-01", "source": SOURCE}],
        {"age": 56},
    )

    assert not report.blocks_execution
    assert report.unknown


# ── Predicate well-formedness ─────────────────────────────────────

@pytest.mark.parametrize(
    ("predicate", "fragment"),
    [
        ({"operator": "ge", "value": 20, "source": SOURCE}, "names no fact"),
        ({"fact": "age", "operator": "ge", "value": 55}, "cites no statute section"),
        ({"fact": "age", "operator": "approx", "value": 55, "source": SOURCE},
         "not supported"),
        ({"fact": "age", "operator": "in", "value": 55, "source": SOURCE},
         "requires a list"),
        ({"fact": "age", "operator": "between", "value": [55], "source": SOURCE},
         "two-element"),
    ],
)
def test_malformed_predicates_are_named_precisely(predicate, fragment):
    problem = validate_predicate(predicate)

    assert problem is not None and fragment in problem


def test_well_formed_predicate_has_no_complaint():
    assert validate_predicate(MIN_ANNUITY[0]) is None


# ── How the validator treats them ─────────────────────────────────

def _spec(predicates: list[dict]) -> dict:
    return RuleSpecification(
        formula_id="11_retirement_annuity_11-134",
        inputs=[{"name": "fas", "type": "money"}],
        parameters={"rate": {"value": 0.024, "source": SOURCE}},
        calculation={
            "operation": "percentage",
            "operands": [{"variable": "fas"}, {"variable": "rate"}],
        },
        applicability_predicates=predicates,
    ).model_dump(mode="json")


def test_absent_predicates_warn_but_never_refuse_the_calculation():
    """A weak derivation must not cost the member their estimate.

    Refusing to calculate whenever the model omits predicates would trade a
    real capability for no added safety — an undeclared condition is not a
    failed one. The gate that matters is a predicate known to be false.
    """
    result = RecipeValidator().validate(_spec([]), ["11-134"], {"11-134"}, strict=True)

    assert result.is_valid
    assert [w.code for w in result.warnings] == ["NO_APPLICABILITY_PREDICATES"]


def test_an_uncheckable_predicate_is_a_hard_error():
    # A condition that cannot be evaluated is worse than none: it looks like a
    # guarantee and enforces nothing.
    spec = _spec([{"fact": "age", "operator": "ge", "value": 55}])
    result = RecipeValidator().validate(spec, ["11-134"], {"11-134"}, strict=True)

    assert not result.is_valid
    assert any(e.code == "INVALID_APPLICABILITY_PREDICATE" for e in result.errors)


def test_well_formed_predicates_validate_cleanly():
    result = RecipeValidator().validate(
        _spec(MIN_ANNUITY), ["11-134"], {"11-134"}, strict=True
    )

    assert result.is_valid
    assert not any(w.code == "NO_APPLICABILITY_PREDICATES" for w in result.warnings)


def test_a_range_mixing_dates_and_numbers_is_incoherent_not_false():
    # Ordering a Decimal against a date raises rather than answering, so the
    # pair is reported as uncheckable and does not block the calculation.
    report = evaluate_predicates(
        [{"fact": "age", "operator": "between", "value": ["2002-01-01", 60],
          "source": SOURCE}],
        {"age": 56},
    )

    assert not report.blocks_execution
    assert report.unknown
