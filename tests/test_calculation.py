"""Tests for the deterministic calculation layer.

The headline case is the Article 11 retirement annuity the pilot is built
around, asserted against a figure worked by hand from the statute text:

    40 ILCS 5/11-134(a) — 2.40% for each year of service, of the highest average
    annual salary for any 4 consecutive years in the last 10, reduced 0.25% for
    each full month the annuity begins before age 60, capped at 80%.

    69,956.40 x 21.5 x 2.40%          = 36,097.5024   (gross)
    36,097.5024 x (1 - 44 x 0.25%)    = 32,126.7771   (44 months before 60)
    -> 32,126.78 a year, 2,677.23 a month
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pension_chat.calculation.dsl_interpreter import DSLExecutionError, DSLInterpreter
from pension_chat.calculation.risk_assessor import RiskAssessor
from pension_chat.calculation.validator import RecipeValidator

# The shape the derivation step actually produces for 11-134(a).
ANNUITY_RECIPE = {
    "formula_id": "11_retirement_annuity_11-134",
    "execution_type": "declarative",
    "inputs": [
        {"name": "service_years", "type": "years", "required": True},
        {"name": "highest_average_annual_salary_4yr", "type": "money", "required": True},
        {"name": "months_before_age_60", "type": "integer", "required": True},
    ],
    "parameters": {
        "annual_accrual_rate": {
            "value": 0.024,
            "source": {"section": "11-134", "subsection": "(a)"},
        },
        "monthly_reduction": {
            "value": 0.0025,
            "source": {"section": "11-134", "subsection": "(a)"},
        },
        "maximum_salary_percentage": {
            "value": 0.8,
            "source": {"section": "11-134", "subsection": "(a)"},
        },
    },
    "calculation": {
        "operation": "cap",
        "limit": {
            "operation": "percentage",
            "operands": [
                {"variable": "highest_average_annual_salary_4yr"},
                {"variable": "maximum_salary_percentage"},
            ],
        },
        "operands": [
            {
                "operation": "multiply",
                "operands": [
                    {
                        "operation": "percentage",
                        "operands": [
                            {
                                "operation": "multiply",
                                "operands": [
                                    {"variable": "highest_average_annual_salary_4yr"},
                                    {"variable": "service_years"},
                                ],
                            },
                            {"variable": "annual_accrual_rate"},
                        ],
                        "result_name": "gross_annuity",
                    },
                    {
                        "operation": "subtract",
                        "operands": [
                            {"value": 1},
                            {
                                "operation": "multiply",
                                "operands": [
                                    {"variable": "months_before_age_60"},
                                    {"variable": "monthly_reduction"},
                                ],
                            },
                        ],
                    },
                ],
            }
        ],
    },
    "output_name": "annual_annuity",
    "output_type": "money",
}

MEMBER = {
    "service_years": 21.5,
    "highest_average_annual_salary_4yr": 69956.40,
    "months_before_age_60": 44,
}


# ── Interpreter ───────────────────────────────────────────────────

def test_article_11_annuity_matches_the_statute():
    result = DSLInterpreter().execute(ANNUITY_RECIPE, MEMBER)

    assert result["result"].quantize(Decimal("0.01")) == Decimal("32126.78")
    assert result["intermediate"]["gross_annuity"] == Decimal("36097.50240")


def test_no_reduction_applies_at_age_60():
    result = DSLInterpreter().execute(ANNUITY_RECIPE, {**MEMBER, "months_before_age_60": 0})

    assert result["result"].quantize(Decimal("0.01")) == Decimal("36097.50")


def test_the_eighty_percent_cap_binds():
    # 40 years of service would otherwise exceed the statutory maximum.
    result = DSLInterpreter().execute(
        ANNUITY_RECIPE, {**MEMBER, "service_years": 40, "months_before_age_60": 0}
    )

    assert result["result"] == Decimal("69956.40") * Decimal("0.8")


def test_a_missing_required_input_stops_execution():
    with pytest.raises(DSLExecutionError, match="Missing required input"):
        DSLInterpreter().execute(
            ANNUITY_RECIPE, {"service_years": 21.5, "months_before_age_60": 44}
        )


def test_division_by_zero_is_reported_not_raised_as_arithmetic_error():
    spec = {
        "formula_id": "t",
        "inputs": [{"name": "a", "type": "money"}],
        "calculation": {
            "operation": "divide",
            "operands": [{"variable": "a"}, {"value": 0}],
        },
    }
    with pytest.raises(DSLExecutionError, match="Division by zero"):
        DSLInterpreter().execute(spec, {"a": 10})


def test_unknown_variable_is_rejected():
    spec = {
        "formula_id": "t",
        "inputs": [],
        "calculation": {"operation": "add", "operands": [{"variable": "nope"}, {"value": 1}]},
    }
    with pytest.raises(DSLExecutionError, match="Undefined variable"):
        DSLInterpreter().execute(spec, {})


def test_conditional_branches_select_the_right_arm():
    spec = {
        "formula_id": "t",
        "inputs": [{"name": "years", "type": "years"}],
        "calculation": {
            "operation": "if",
            "condition": {
                "left": {"variable": "years"},
                "operator": "ge",
                "right": {"value": 30},
            },
            "then_branch": {"value": 100},
            "else_branch": {"value": 50},
        },
    }
    interpreter = DSLInterpreter()
    assert interpreter.execute(spec, {"years": 31})["result"] == Decimal(100)
    assert interpreter.execute(spec, {"years": 21.5})["result"] == Decimal(50)


# ── Validator ─────────────────────────────────────────────────────

def test_well_sourced_recipe_passes_strict_validation():
    result = RecipeValidator().validate(
        ANNUITY_RECIPE, ["11-134"], {"11-134", "11-216"}, set(), strict=True
    )

    assert result.is_valid, [e.to_dict() for e in result.errors]


def test_strict_validation_rejects_an_uncited_rate():
    """The stubbed `salary x 0.5` recipe is the case this exists to stop."""
    spec = {
        "formula_id": "derived_retirement_annuity",
        "inputs": [{"name": "salary", "type": "money"}],
        "parameters": {"rate": {"value": 0.5, "source": {}}},
        "calculation": {
            "operation": "percentage",
            "operands": [{"variable": "salary"}, {"variable": "rate"}],
        },
    }
    lenient = RecipeValidator().validate(spec, ["11-134"], {"11-134"}, set())
    strict = RecipeValidator().validate(spec, ["11-134"], {"11-134"}, set(), strict=True)

    assert lenient.is_valid          # warnings only — how the stub got through
    assert not strict.is_valid
    assert any(e.code == "MISSING_SOURCE_CITATION" for e in strict.errors)


def test_strict_validation_rejects_a_recipe_with_no_sources():
    result = RecipeValidator().validate(ANNUITY_RECIPE, [], {"11-134"}, set(), strict=True)

    assert not result.is_valid
    assert any(e.code == "NO_SOURCE_SECTIONS" for e in result.errors)


def test_citing_a_section_outside_the_index_is_always_an_error():
    result = RecipeValidator().validate(ANNUITY_RECIPE, ["9-101"], {"11-134"}, set())

    assert not result.is_valid
    assert any(e.code == "SECTION_NOT_FOUND" for e in result.errors)


def test_parameter_citing_an_unretrieved_section_fails_strict_validation():
    spec = {
        **ANNUITY_RECIPE,
        "parameters": {
            "rate": {"value": 0.024, "source": {"section": "8-101"}},
        },
    }
    result = RecipeValidator().validate(spec, ["11-134"], {"11-134"}, set(), strict=True)

    assert not result.is_valid
    assert any(e.code == "PARAMETER_SOURCE_NOT_RETRIEVED" for e in result.errors)


def test_capability_ref_is_rejected_because_the_interpreter_cannot_run_it():
    spec = {
        "formula_id": "t",
        "inputs": [],
        "calculation": {"operation": "capability_ref", "capability_id": "actuarial.factor"},
    }
    result = RecipeValidator().validate(spec, ["11-134"], {"11-134"}, set(), strict=True)

    assert not result.is_valid
    assert any(e.code == "CAPABILITY_REF_NOT_EXECUTABLE" for e in result.errors)


def test_named_intermediates_count_as_declared_variables():
    result = RecipeValidator().validate(
        ANNUITY_RECIPE, ["11-134"], {"11-134"}, set(), strict=True
    )

    assert not any(e.code == "UNDECLARED_VARIABLE" for e in result.errors)


def test_unsupported_operation_is_rejected():
    spec = {
        "formula_id": "t",
        "inputs": [],
        "calculation": {"operation": "exponentiate", "operands": [{"value": 2}]},
    }
    result = RecipeValidator().validate(spec, ["11-134"], {"11-134"}, set())

    assert not result.is_valid
    assert any(e.code == "UNSUPPORTED_OPERATION" for e in result.errors)


# ── Risk assessor ─────────────────────────────────────────────────

def test_risk_rises_with_topic_and_cross_article_dependencies():
    assessor = RiskAssessor()

    simple = assessor.assess(ANNUITY_RECIPE, ["11-134"], "retirement_annuity", 11)
    complex_ = assessor.assess(
        ANNUITY_RECIPE,
        ["11-134", "12-133", "8-101", "11-216", "11-215", "11-217"],
        "survivor_annuity",
        11,
        has_cross_article_refs=True,
    )

    assert simple.risk_tier == "low"
    assert complex_.risk_tier == "high"
