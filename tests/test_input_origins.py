"""Tests for where each calculation input came from, recorded as it resolves.

`resolve_inputs` merges three sources -- the record, what the model read out of
the conversation, and the assumption policy -- and used to return only the
merged values. The calculation card then guessed each input's origin from the
worksheet, which holds a stated value only when the model also reported it as a
revision: the same question was labelled "stated" on one run and "derived" on
the next. The origin is decided here instead, where it is actually known.
"""

from __future__ import annotations

from pension_chat.agent.nodes import _input_origins
from pension_chat.agent.worksheet import WorkSheet


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
