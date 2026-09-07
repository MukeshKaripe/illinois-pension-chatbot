"""Tests for the provenance a calculation result carries.

A reviewer reading an answer that quoted a final average salary of $69,956.40,
21.5 years of service and a 2.40% rate worked those three numbers out to
$36,097.50, saw the reported $32,126.78, and concluded the engine had done
something undocumented. It had not — the difference is the early-retirement
reduction of 0.25% per month for 44 months, which the answer cited in prose but
never applied to a visible number.

The arithmetic was right and unverifiable at the same time. These tests pin down
that a result now carries the path to itself: the inputs it consumed, the
statutory constants with the section each was read from, and the named steps in
the order a reader would work through them.
"""

from __future__ import annotations

from decimal import Decimal

from pension_chat.agent.nodes import _present
from pension_chat.calculation.dsl_interpreter import DSLInterpreter

SOURCE = {"section": "11-134", "subsection": "(a)"}

# Section 11-134(a) as it applies to a withdrawal on or after 1 January 2002:
# 2.40% per year of service on the highest average annual salary for any 4
# consecutive years, reduced 0.25% per full month below age 60, capped at 80%.
ELECTED_ANNUITY = {
    "formula_id": "11_retirement_annuity_11-134",
    "inputs": [
        {"name": "highest_average_annual_salary_4yr", "type": "money"},
        {"name": "creditable_service_years", "type": "years"},
        {"name": "months_before_age_60", "type": "integer"},
    ],
    "parameters": {
        "accrual_rate": {"value": 0.024, "source": SOURCE},
        "monthly_reduction": {"value": 0.0025, "source": SOURCE},
        "maximum_fraction": {"value": 0.8, "source": SOURCE},
    },
    "calculation": {
        "operation": "cap",
        "result_name": "annual_annuity",
        "limit": {
            "operation": "percentage",
            "result_name": "statutory_maximum",
            "operands": [
                {"variable": "highest_average_annual_salary_4yr"},
                {"variable": "maximum_fraction"},
            ],
        },
        "operands": [
            {
                "operation": "multiply",
                "operands": [
                    {
                        "operation": "multiply",
                        "result_name": "unreduced_annuity",
                        "operands": [
                            {"variable": "highest_average_annual_salary_4yr"},
                            {"variable": "creditable_service_years"},
                            {"variable": "accrual_rate"},
                        ],
                    },
                    {
                        "operation": "subtract",
                        "result_name": "age_reduction_factor",
                        "operands": [
                            {"value": 1},
                            {
                                "operation": "percentage",
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

MEMBER_INPUTS = {
    "highest_average_annual_salary_4yr": Decimal("69956.40"),
    "creditable_service_years": Decimal("21.5"),
    "months_before_age_60": 44,
}


def _run(inputs=None):
    return DSLInterpreter().execute(ELECTED_ANNUITY, inputs or MEMBER_INPUTS)


def test_the_reported_figure_is_reproducible_by_hand():
    result = _run()

    assert Decimal(str(result["result"])).quantize(Decimal("0.01")) == Decimal("32126.78")


def test_the_steps_close_the_gap_between_the_base_and_the_final_figure():
    steps = {s["name"]: Decimal(str(s["value"])) for s in _run()["steps"]}

    # The number a reader gets from salary x service x rate...
    assert steps["unreduced_annuity"].quantize(Decimal("0.01")) == Decimal("36097.50")
    # ...and the factor that accounts for the rest of the difference.
    assert steps["age_reduction_factor"] == Decimal("0.8900")
    assert steps["annual_annuity"].quantize(Decimal("0.01")) == Decimal("32126.78")


def test_steps_arrive_in_the_order_a_reader_follows_them():
    names = [s["name"] for s in _run()["steps"]]

    assert names.index("unreduced_annuity") < names.index("age_reduction_factor")
    assert names[-1] == "annual_annuity"


def test_every_statutory_constant_arrives_with_its_citation():
    used = _run()["parameters_used"]

    assert set(used) == {"accrual_rate", "monthly_reduction", "maximum_fraction"}
    assert used["accrual_rate"] == {"value": 0.024, "source": SOURCE}


def test_the_inputs_the_figure_rests_on_are_reported():
    used = _run()["inputs_used"]

    assert used["highest_average_annual_salary_4yr"] == Decimal("69956.4")
    assert used["creditable_service_years"] == Decimal("21.5")


def test_only_the_taken_branch_contributes_steps():
    """A trace must describe what happened, not what might have."""
    spec = {
        **ELECTED_ANNUITY,
        "calculation": {
            "operation": "if",
            "condition": {
                "operation": "compare",
                "left": {"variable": "creditable_service_years"},
                "operator": "ge",
                "right": {"value": 25},
            },
            "then_branch": {
                "operation": "multiply",
                "result_name": "unreduced_annuity",
                "operands": [
                    {"variable": "highest_average_annual_salary_4yr"},
                    {"variable": "accrual_rate"},
                ],
            },
            "else_branch": {
                "operation": "multiply",
                "result_name": "reduced_annuity",
                "operands": [
                    {"variable": "highest_average_annual_salary_4yr"},
                    {"variable": "monthly_reduction"},
                ],
            },
        },
    }
    names = [s["name"] for s in DSLInterpreter().execute(spec, MEMBER_INPUTS)["steps"]]

    # 21.5 years does not meet the 25-year waiver, so only the else branch ran.
    assert names == ["reduced_annuity"]


def test_a_statutory_cap_is_visible_as_a_step():
    # The 80% maximum did not bind here, but a member cannot tell that unless
    # the ceiling itself is shown.
    steps = {s["name"]: Decimal(str(s["value"])) for s in _run()["steps"]}

    assert steps["statutory_maximum"] == Decimal("55965.12")
    assert steps["annual_annuity"] < steps["statutory_maximum"]


# ── Presentation ──────────────────────────────────────────────────

def test_presentation_rounds_and_carries_the_breakdown():
    presented = _present(_run(), "annual_annuity")

    assert presented["annual_amount"] == "32126.78"
    assert presented["monthly_amount"] == "2677.23"
    assert presented["statutory_values_used"]["accrual_rate"]["source"] == SOURCE
    assert presented["steps"][0]["name"] == "unreduced_annuity"


def test_scenarios_omit_the_breakdown_they_would_only_repeat():
    """Four scenarios of the same recipe restate the same constants four times.

    What differs between them is already reported as the scenario's assumptions,
    so the breakdown is carried once, on the base result.
    """
    presented = _present(_run(), "annual_annuity", breakdown=False)

    assert presented["annual_amount"] == "32126.78"
    assert "steps" not in presented
    assert "statutory_values_used" not in presented


# ── Rendering quantities that are not money ───────────────────────

def test_service_years_are_not_padded_to_two_decimals():
    """"21.50 years" is not how anyone says it.

    The answer is required to copy figures character for character, so a
    presentation layer that quantizes everything to two places does not merely
    look untidy — it dictates the wording. Money keeps its second decimal;
    everything else is rendered as the quantity it is, on the type the recipe
    itself declared.
    """
    presented = _present(_run(), "annual_annuity")

    assert presented["inputs_used"]["creditable_service_years"] == "21.5"
    assert presented["inputs_used"]["months_before_age_60"] == "44"
    assert presented["inputs_used"]["highest_average_annual_salary_4yr"] == "69956.40"
    # The headline figures are money and keep both places.
    assert presented["annual_amount"] == "32126.78"


def test_the_declared_type_decides_not_the_input_name():
    # "creditable_service_years" contains "credit"; a keyword rule reads that as
    # money and pads it. The recipe declares it as years, so that governs.
    assert _run()["input_types"]["creditable_service_years"] == "years"
    assert _run()["input_types"]["highest_average_annual_salary_4yr"] == "money"
