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
