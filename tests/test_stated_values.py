"""Tests for deciding whether the member actually wrote a value.

`_stated_in` is what separates a figure the member typed ("I earn $72,000") from
one the extraction step inferred, and that distinction is load-bearing in two
places: a stated value may override the record, and the guardrail traces figures
to "the member's own messages" as a provenance class of its own.

As a substring test an extracted 0 counted as stated against "at age 60", and
the inferred value then overrode the record -- the early-retirement reduction
silently became zero while service stayed at 21.5 years.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from pension_chat.agent import nodes
from pension_chat.agent.nodes import _stated_in


class TestStatedIn:
    """`_stated_in` decides whether an extracted value may override the record.

    It was a substring test, so any extracted 0 counted as stated by the member
    whenever a 0 appeared anywhere in their message. "Now calculate it at age
    60." made `months_before_age_60 = 0` look member-supplied; the inferred
    value then beat the record, and the early-retirement reduction silently
    vanished while service stayed at 21.5 years -- an annuity figure whose own
    listed inputs contradicted it.

    The same digit-boundary hazard was fixed once already in `_corroborated`,
    where $72,000 was corroborating a retirement age of 72.
    """

    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (0, "Now calculate it at age 60."),      # the reported failure
            (5, "I am 56 with 21.5 years"),
            (1, "21.5 years of service"),
            (44, "Now calculate it at age 60."),
            (2000, "I have $72,000 in salary"),
            ("active", "I am inactive"),
        ],
    )
    def test_a_number_hiding_inside_another_is_not_stated(self, value, text):
        assert not _stated_in(value, text)

    @pytest.mark.parametrize(
        ("value", "text"),
        [
            (60, "Now calculate it at age 60."),
            (25, "Actually I have 25 years of service"),
            (21.5, "I have 21.5 years"),
            # Thousands separators and trailing zeros are how a person writes a
            # number, not a different number.
            (72000, "My salary is $72,000 a year"),
            (72000, "My salary is 72000"),
            (69956.40, "my best four years came to $69,956.40"),
            ("2026-09-08", "I retire on 2026-09-08"),
            ("active", "I am an active employee"),
        ],
    )
    def test_a_value_the_member_wrote_is_stated(self, value, text):
        assert _stated_in(value, text)


# -- Skipping the planner round trip -------------------------------


_SPEC = {"formula_id": "f", "execution_type": "declarative",
         "inputs": [{"name": "service_years", "type": "years", "required": True},
                    {"name": "final_average_salary", "type": "money", "required": True},
                    {"name": "age_at_annuity_start", "type": "integer", "required": True}],
         "parameters": {}, "calculation": {"variable": "service_years"},
         "output_name": "annual_annuity", "output_type": "money"}


def _state(said: str, stated: dict, record: dict | None = None) -> dict:
    return {"employee_record": None, "member_facts": record or {}, "fact_ledger": {}, "salary_history": [],
            "question": said, "messages": [HumanMessage(content=said)], "turn_index": 1,
            "coverage": {"article": "8", "basis": "deployment", "record_article": None},
            "recipes": {"retirement_annuity": {"source": "registry", "spec": _SPEC}},
            "requires_calculation": True, "assumptions": {}, "stated_by_model": stated}


@pytest.fixture
def no_model(monkeypatch):
    # Recorded, not raised: resolve_inputs catches a failing extraction call
    # and carries on, so an exception here would be swallowed.
    calls = []

    async def refuse(**kwargs):
        calls.append(kwargs.get("node"))
        return "{}"
    monkeypatch.setattr(nodes, "complete", refuse)
    yield
    assert not calls, f"resolve_inputs called the model: {calls}"


async def test_stated_values_reach_the_engine_without_a_model_call(no_model):
    said = "A member who joined in 2005 is 61, has 10 years of service and an average salary of $50,000."
    out = await nodes.resolve_inputs(_state(said, {"age": 61, "service_years": 10, "final_average_salary": 50000,
                                                   "year_joined": 2005}))
    assert out["resolved_inputs"]["service_years"] == 10
    assert out["resolved_inputs"]["age_at_annuity_start"] == 61
    assert out["input_sources"]["service_years"] == "stated"


async def test_a_value_the_user_never_wrote_is_rejected(no_model):
    said = "A member who joined in 2015 has 11 years of service."
    out = await nodes.resolve_inputs(_state(said, {"age": 62, "service_years": 11}))
    assert out["resolved_inputs"].get("age_at_annuity_start") is None    # "62" was never written
    assert out["resolved_inputs"]["service_years"] == 11


async def test_a_correction_overrides_the_record(no_model):
    said = "Actually it's 23 years of service, not 21.5."
    out = await nodes.resolve_inputs(_state(said, {"service_years": 23}, record={"service_years": 21.5}))
    assert out["resolved_inputs"]["service_years"] == 23


async def test_a_name_no_formula_reads_is_recorded_not_lost(no_model):
    said = "She is 61 with 10 years of service, salary $50,000 and a spouse aged 58."
    out = await nodes.resolve_inputs(_state(said, {"age": 61, "service_years": 10, "spouse_age": 58}))
    assert out["unused_stated"] == {"spouse_age": 58}


async def test_a_stated_salary_stands_in_for_the_average(no_model):
    said = "Someone aged 61 with 10 years of service earning $80k."
    out = await nodes.resolve_inputs(_state(said, {"age": 61, "service_years": 10, "salary": 80000}))
    assert out["resolved_inputs"]["final_average_salary"] == 80000
    assert out["input_sources"]["final_average_salary"] == "assumed"


async def test_the_separate_call_can_be_restored(monkeypatch):
    from pension_chat.config import get_settings
    called = []

    async def heard(**kwargs):
        called.append(1)
        return '{"resolved": {}}'

    monkeypatch.setattr(nodes, "complete", heard)
    monkeypatch.setattr(get_settings(), "extract_with_separate_call", True)
    await nodes.resolve_inputs(_state("I have 10 years of service.", {}))
    assert called == [1]
