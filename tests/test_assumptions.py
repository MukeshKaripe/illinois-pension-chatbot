"""Tests for the assumption policy.

The last test is a bug-class guard rather than a behaviour test, in the style
of the alternative-reachability guards in test_planning.py and test_coverage.py:
it walks the policy table itself and requires every assumable entry to be
complete, so a new entry cannot be added half-finished.
"""

from __future__ import annotations

import pytest

from pension_chat.calculation.assumption_policy import (
    NEVER_ASSUMABLE,
    POLICY,
    default_for,
    is_assumable,
    plausible_range,
    rule_for,
)

KNOWN = {"age": 56, "as_of_date": "2026-09-09", "service_years": 21.5}


def test_timing_inputs_are_assumable() -> None:
    assert is_assumable("withdrawal_date")
    assert is_assumable("months_before_age_60")


@pytest.mark.parametrize(
    "name",
    ["article_number", "employment_status", "tier", "final_average_salary", "service_years"],
)
def test_identity_and_entitlement_inputs_are_never_assumable(name: str) -> None:
    assert not is_assumable(name)
    assert name in NEVER_ASSUMABLE
    assert default_for(name, KNOWN) is None


def test_withdrawal_date_defaults_to_today_as_the_member_knows_it() -> None:
    assert default_for("withdrawal_date", KNOWN) == "2026-09-09"


def test_months_before_sixty_assumes_a_mid_year_birthday() -> None:
    # 56 years old, so four years to 60, less six months for the unknown
    # position of the birthday within the year.
    assert default_for("months_before_age_60", KNOWN) == 42


def test_months_before_sixty_carries_the_whole_year_of_uncertainty() -> None:
    assert plausible_range("months_before_age_60", KNOWN) == (37, 48)


def test_a_range_is_absent_when_the_facts_do_not_support_one() -> None:
    assert plausible_range("months_before_age_60", {}) is None


def test_every_assumable_rule_is_complete() -> None:
    """A half-finished policy entry is a silent wrong number, so require the lot."""
    for name, rule in POLICY.items():
        if not rule.assumable:
            assert rule.default is None, f"{name}: not assumable but carries a default"
            continue
        assert rule.basis, f"{name}: assumable with no basis to state"
        assert rule.disclosure_cues, f"{name}: assumable with no words that disclose it"
        assert rule.default is not None, f"{name}: assumable with no default"
        assert rule.plausible is not None, f"{name}: assumable with no plausible range"
        assert default_for(name, KNOWN) is not None, f"{name}: default unusable on known facts"
        assert plausible_range(name, KNOWN) is not None, f"{name}: range unusable on known facts"


def test_an_unknown_input_is_not_assumable_by_default() -> None:
    assert rule_for("favourite_colour") is None
    assert not is_assumable("favourite_colour")
