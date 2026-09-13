"""Tests for where each calculation input came from, recorded as it resolves.

`resolve_inputs` merges three sources -- the record, what the model read out of
the conversation, and the assumption policy -- and used to return only the
merged values. The calculation card then guessed each input's origin from the
worksheet, which holds a stated value only when the model also reported it as a
revision: the same question was labelled "stated" on one run and "derived" on
the next. The origin is decided here instead, where it is actually known.
"""

from __future__ import annotations

from pension_chat.agent.nodes import _available_facts, _input_origins, _stand_ins_allowed
from pension_chat.agent.worksheet import (
    SOURCE_STAND_IN,
    SOURCE_WITHHELD,
    USABLE_SOURCES,
    WorkSheet,
)


def test_each_input_is_labelled_by_where_it_actually_came_from() -> None:
    sheet = WorkSheet.from_state({"member_facts": {"service_years": "21.5"}})
    origins = _input_origins(
        resolved={
            "service_years": "21.5",
            "final_average_salary": "70000.00",
            "months_before_age_60": "0",
            "age_at_annuity_start": "60",
        },
        taken={"final_average_salary": "70000.00", "months_before_age_60": "0"},
        assumed={"age_at_annuity_start": "60"},
        sheet=sheet,
        question="My final average salary is $70,000.",
    )
    assert origins == {
        # From the member's file.
        "service_years": "record",
        # Read from the message, and the member wrote it: "$70,000".
        "final_average_salary": "stated",
        # Read from the message, but nobody wrote 0 -- the model worked it out.
        "months_before_age_60": "derived",
        # Filled by the assumption policy.
        "age_at_annuity_start": "assumed",
    }


def test_a_record_input_keeps_the_sheets_own_label() -> None:
    # A value the member stated on an earlier turn is on the sheet as stated,
    # and resolving it again this turn does not turn it into "record".
    sheet = WorkSheet.from_state(
        {"assumptions": {"service_years": {"value": "25", "source": "stated"}}}
    )
    origins = _input_origins(
        resolved={"service_years": "25"},
        taken={},
        assumed={},
        sheet=sheet,
        question="What would my pension be?",
    )
    assert origins == {"service_years": "stated"}


async def test_an_extracted_age_moves_the_facts_derived_from_it(monkeypatch):
    """L03's base paired age 58 with months_before_age_60 44.

    44 months before 60 is a member of 56. Splicing one fact out of the message
    while every fact computed from it stays at today's value produces a figure
    whose own inputs contradict each other -- and that figure sits in
    `calculation_results`, where the guardrail admits its digits and the card
    may show them.
    """
    from pension_chat.agent.nodes import resolve_inputs

    state = {
        "question": "I'm thinking of retiring at 58. How much would I get?",
        "messages": [],
        "employee_record": {
            "id": "e1",
            "article_number": 11,
            "tier": 1,
            "birth_date": "1970-05-15",
            "hire_date": "2005-01-01",
            "employment_status": "active",
            "total_service_years": 21.5,
            "current_annual_salary": 72000.0,
            "extra_facts": {},
        },
        "salary_history": [],
        "recipes": {
            "retirement_annuity": {
                "spec": {
                    "inputs": [
                        {"name": "age_at_annuity_start", "type": "number", "required": True},
                        {"name": "months_before_age_60", "type": "number", "required": True},
                    ]
                }
            }
        },
        "member_facts": {
            "age": 56,
            "age_at_annuity_start": 56,
            "months_before_age_60": 44,
            "service_years": 21.5,
        },
    }

    async def fake_complete(**kwargs):
        return '{"resolved": {"age_at_annuity_start": 58}, "unresolved": []}'

    monkeypatch.setattr("pension_chat.agent.nodes.complete", fake_complete)

    out = await resolve_inputs(state)
    resolved = out["resolved_inputs"]

    assert str(resolved["age_at_annuity_start"]) == "58"
    assert str(resolved["months_before_age_60"]) != "44", (
        "months before 60 must move when the annuity start age does"
    )


# ── Stand-ins ─────────────────────────────────────────────────────
#
# Under a premise the member supposed, their own record is filed under another
# Article, so every figure drawn from it is wrong in the same four ways at once
# (input, predicate, quoted figure, verified source) -- which is why
# `_available_facts` starves it and everything becomes a missing input.
#
# The exception the spec draws is narrow and is the member's to draw: "if I were
# a Chicago police officer *with my salary and service*" invites the borrowing
# outright, and refusing it answers a question nobody asked. The model reads the
# invitation; code decides what it permits and labels every borrowed value.


_SUPPOSED = {
    "member_facts": {"service_years": "21.5", "final_average_salary": "69956.40"},
    "coverage": {"article": "5", "record_article": "11", "basis": "hypothetical"},
}


def test_a_supposition_withholds_the_record_by_default() -> None:
    assert _available_facts(dict(_SUPPOSED)) == {}


def test_an_invited_stand_in_makes_the_record_usable() -> None:
    facts = _available_facts({**_SUPPOSED, "stand_in_facts": True})
    assert facts["service_years"] == "21.5"
    assert facts["final_average_salary"] == "69956.40"


def test_the_flag_alone_does_not_relabel_a_members_own_facts() -> None:
    """There is nothing to stand in for when the conversation is reading their
    own Article. A model that sets the flag on every turn must not turn every
    member's record into a borrowed one."""
    own = {
        "member_facts": {"service_years": "21.5"},
        "coverage": {"article": "11", "record_article": "11", "basis": "record"},
        "stand_in_facts": True,
    }
    assert _stand_ins_allowed(own) is False
    assert _available_facts(own)["service_years"] == "21.5"

    sheet = WorkSheet.from_state(own, record_usable=True, as_stand_in=_stand_ins_allowed(own))
    origins = _input_origins(
        resolved={"service_years": "21.5"},
        taken={},
        assumed={},
        sheet=sheet,
        question="What would I get?",
    )
    assert origins == {"service_years": "record"}


def test_a_borrowed_value_is_labelled_as_a_stand_in() -> None:
    """Named as such in the answer and on the card -- otherwise a figure worked
    from another fund's formula on this fund's salary reads as simply theirs."""
    state = {**_SUPPOSED, "stand_in_facts": True}
    sheet = WorkSheet.from_state(
        state, record_usable=True, as_stand_in=_stand_ins_allowed(state)
    )
    origins = _input_origins(
        resolved={"service_years": "21.5", "final_average_salary": "69956.40"},
        taken={},
        assumed={},
        sheet=sheet,
        question="if I were a Chicago police officer with my salary and service?",
    )
    assert origins == {"service_years": "stand_in", "final_average_salary": "stand_in"}


def test_a_withheld_value_stays_withheld_without_the_invitation() -> None:
    sheet = WorkSheet.from_state(_SUPPOSED, record_usable=False)
    fact = sheet.fact("service_years")
    assert fact is not None and fact.source == SOURCE_WITHHELD
    assert fact.source not in USABLE_SOURCES, "known, and still not a basis for a figure"


def test_a_stand_in_may_be_computed_on() -> None:
    """The whole point: the figure is real and comparable, not an estimate."""
    assert SOURCE_STAND_IN in USABLE_SOURCES


def test_a_stand_in_is_never_written_to_durable_state() -> None:
    """It is a per-turn view of a record fact, exactly as `withheld` is. Stored,
    it would outlive the premise and overlay the record with a borrowed copy."""
    state = {**_SUPPOSED, "stand_in_facts": True}
    sheet = WorkSheet.from_state(state, record_usable=True, as_stand_in=True)
    assert "service_years" not in (sheet.to_state_update().get("assumptions") or {})
