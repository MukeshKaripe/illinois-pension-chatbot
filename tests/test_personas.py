"""Tests for the synthetic members someone can sign up as.

Signup does not let a member author a pension record, it lets them pick one. The
reason is in `personas.py`'s docstring: the whole system rests on the record
being evidence independent of what the member says, and a self-authored record
is one voice agreeing with itself. These tests cover the two things that make a
picked persona safe — that it cannot be edited into something incoherent, and
that editing it changes what the member expects it to change.

The regression guard at the bottom is the important one. The two originally
seeded members are pinned by id in `eval_agent.py` and `gold_coverage.yaml`, and
their salary history feeds the expected figure of every calculation case in the
benchmark. Both were previously left to `gen_random_uuid()`, so a fresh database
bound those cases to nothing at all.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from pension_chat import personas
from pension_chat.agent.facts import derive_tier, highest_average_salary

TODAY = date(2026, 9, 9)


def _fas(persona) -> Decimal | None:
    """Final average salary the way Section 11-134(a) asks for it."""
    return highest_average_salary(
        list(persona.salary_history), 4, 10, TODAY
    )


# ── Tier is derived, never asked ──────────────────────────────────


@pytest.mark.parametrize(
    ("first_participation", "expected"),
    [
        (date(2010, 12, 31), 1),
        (date(2011, 1, 1), 2),
        (date(1995, 6, 5), 1),
        (date(2014, 4, 14), 2),
    ],
)
def test_tier_turns_on_one_date(first_participation, expected):
    assert derive_tier(first_participation) == expected


def test_moving_the_hire_date_across_the_boundary_changes_the_tier():
    """The edit with the least visible consequence and one of the largest.

    Tier selects the accrual rule through Section 1-160 and keys the recipe
    registry lookup, so a hire date dragged over 1 January 2011 changes the
    whole calculation with nothing on the form saying so. The UI has to show the
    derived tier back; this pins that there is something to show.
    """
    persona = personas.ARTICLE_11_STANDARD
    assert persona.tier == 1

    later = personas.apply_edits(persona, {"hire_date": date(2011, 1, 1)})
    assert later.tier == 2

    earlier = personas.apply_edits(later, {"hire_date": date(2010, 12, 31)})
    assert earlier.tier == 1


# ── Editing ───────────────────────────────────────────────────────


def test_editing_the_salary_moves_the_final_average_salary():
    """Otherwise the app looks broken, and would be.

    Article 11's annuity is computed from `final_average_salary`, which comes
    from the history rows rather than from `current_annual_salary`. A member who
    raises their salary and sees the pension barely move has changed a field
    that is not an input to the formula.
    """
    persona = personas.ARTICLE_11_STANDARD
    before = _fas(persona)

    edited = personas.apply_edits(persona, {"current_annual_salary": Decimal("90000.00")})
    after = _fas(edited)

    assert before is not None and after is not None
    assert after > before
    # Scaled by the same ratio, so the shape of the raises survives.
    ratio = Decimal("90000.00") / persona.current_annual_salary
    assert after == pytest.approx(float(before * ratio), rel=1e-6)
    assert len(edited.salary_history) == len(persona.salary_history)


def test_an_edit_that_changes_nothing_returns_the_same_persona():
    persona = personas.ARTICLE_11_STANDARD
    assert personas.apply_edits(persona, {}) is persona
    assert personas.apply_edits(persona, {"current_annual_salary": None}) is persona
    # Unknown keys are the caller's problem to validate, not this function's.
    assert personas.apply_edits(persona, {"tier": 2}) is persona


def test_editing_leaves_the_original_untouched():
    persona = personas.ARTICLE_11_STANDARD
    original_history = persona.salary_history
    personas.apply_edits(persona, {"current_annual_salary": Decimal("99000.00")})
    assert persona.salary_history == original_history
    assert persona.current_annual_salary == Decimal("72000.00")


# ── Bounds are relational ─────────────────────────────────────────


def test_every_shipped_persona_is_coherent():
    for persona in personas.PERSONAS:
        assert personas.validate(persona, today=TODAY) == [], persona.key


@pytest.mark.parametrize(
    ("edits", "fragment"),
    [
        # Hired before they were born. Independent per-field ranges admit this.
        ({"hire_date": date(1960, 1, 1)}, "in the job at"),
        ({"birth_date": date(2020, 1, 1)}, "age of"),
        ({"hire_date": date(2030, 1, 1)}, "future"),
        # More service than they have had the job.
        ({"total_service_years": Decimal("40.00")}, "exceeds"),
        ({"total_service_years": Decimal("-1.00")}, "negative"),
        # A zero salary is not merely odd — it yields a confident $0.00 annuity
        # that the numeric guardrail passes, because it traces to the record.
        ({"current_annual_salary": Decimal("0.00")}, "greater than zero"),
        ({"current_annual_salary": Decimal("5000000.00")}, "outside the range"),
        ({"employment_status": "on the moon"}, "employment status"),
    ],
)
def test_an_incoherent_persona_is_rejected(edits, fragment):
    broken = personas.apply_edits(personas.ARTICLE_11_STANDARD, edits)
    problems = personas.validate(broken, today=TODAY)
    assert any(fragment in problem for problem in problems), problems


def test_ineligibility_is_not_an_error():
    """Being too young or too short-served to draw an annuity is a real state.

    It is also one of the answers this system is meant to give well, so the
    bounds must not quietly forbid the persona that produces it.
    """
    assert personas.validate(personas.ARTICLE_11_EARLY_CAREER, today=TODAY) == []


def test_a_persona_without_salary_history_is_rejected():
    """No history means no final average salary, so no figure at all."""
    stripped = replace(personas.ARTICLE_11_STANDARD, salary_history=())
    assert any(
        "salary history" in problem
        for problem in personas.validate(stripped, today=TODAY)
    )


# ── The regression guard ──────────────────────────────────────────


def test_the_two_pilot_members_keep_the_ids_the_benchmark_binds_to():
    """`eval_agent.py:65` and `gold_coverage.yaml` name these outright.

    Both rows previously took `gen_random_uuid()`, so re-seeding a database
    produced ids that matched neither, and the cases bound to nothing without
    failing.
    """
    assert str(personas.ARTICLE_11_STANDARD.id) == "de75c498-20cc-446d-8858-ec26303349fa"
    assert str(personas.ARTICLE_12_STANDARD.id) == "8b33966a-8d4d-47e5-8fbf-fc950239f111"
    assert personas.SEEDED == (
        personas.ARTICLE_11_STANDARD,
        personas.ARTICLE_12_STANDARD,
    )


def test_the_two_pilot_members_keep_their_exact_salary_history():
    """Their final average salary is the expected figure of every calculation case.

    The generator used for the other personas lands a cent away in two years —
    these were written by hand and no single rounding rule reproduces them — so
    they are written out verbatim instead. A cent inside a four-year average is
    exactly the wrong thing to be approximately right about.
    """
    assert personas.ARTICLE_11_STANDARD.salary_history == (
        (date(2018, 1, 1), Decimal("58000.00")),
        (date(2019, 1, 1), Decimal("59740.00")),
        (date(2020, 1, 1), Decimal("61532.20")),
        (date(2021, 1, 1), Decimal("63378.17")),
        (date(2022, 1, 1), Decimal("65279.51")),
        (date(2023, 1, 1), Decimal("67237.89")),
        (date(2024, 1, 1), Decimal("69255.03")),
        (date(2025, 1, 1), Decimal("71332.68")),
        (date(2026, 1, 1), Decimal("72000.00")),
    )
    assert personas.ARTICLE_12_STANDARD.salary_history == (
        (date(2018, 1, 1), Decimal("55000.00")),
        (date(2019, 1, 1), Decimal("56650.00")),
        (date(2020, 1, 1), Decimal("58349.50")),
        (date(2021, 1, 1), Decimal("60099.99")),
        (date(2022, 1, 1), Decimal("61902.98")),
        (date(2023, 1, 1), Decimal("63760.07")),
        (date(2024, 1, 1), Decimal("65672.87")),
        (date(2025, 1, 1), Decimal("67643.06")),
        (date(2026, 1, 1), Decimal("68000.00")),
    )


def test_persona_keys_and_ids_are_unique():
    keys = [p.key for p in personas.PERSONAS]
    ids = [str(p.id) for p in personas.PERSONAS]
    assert len(keys) == len(set(keys))
    assert len(ids) == len(set(ids))


def test_lookup_helpers():
    assert personas.by_key("article-12-tier-2") is personas.ARTICLE_12_TIER_2
    assert personas.by_key("nope") is None
    assert personas.by_id("8b33966a-8d4d-47e5-8fbf-fc950239f111") is (
        personas.ARTICLE_12_STANDARD
    )
    assert personas.by_id("00000000-0000-0000-0000-000000000000") is None
    assert set(personas.for_article(12)) == {
        personas.ARTICLE_12_STANDARD,
        personas.ARTICLE_12_TIER_2,
    }


def test_a_tier_2_persona_exists_for_both_pilot_articles():
    """Section 1-160 is why Article 1 was ingested; something must reach it."""
    for article in (11, 12):
        tiers = {p.tier for p in personas.for_article(article)}
        assert 2 in tiers, f"Article {article} has no Tier 2 persona"


def test_employee_kwargs_derive_tier_rather_than_carry_it():
    kwargs = personas.to_employee_kwargs(personas.ARTICLE_11_TIER_2)
    assert kwargs["tier"] == 2
    assert kwargs["id"] == personas.ARTICLE_11_TIER_2.id
    # Whatever else moves, the pension columns the agent reads must all be there.
    assert set(kwargs) >= {
        "article_number",
        "tier",
        "birth_date",
        "hire_date",
        "employment_status",
        "total_service_years",
        "current_annual_salary",
    }
