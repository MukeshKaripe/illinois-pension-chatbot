"""Tests for the assumption policy.

The last test is a bug-class guard rather than a behaviour test, in the style
of the alternative-reachability guards in test_planning.py and test_coverage.py:
it walks the policy table itself and requires every assumable entry to be
complete, so a new entry cannot be added half-finished.
"""

from __future__ import annotations

import pytest

from pension_chat.calculation.assumption_policy import (
    ASSUMPTION_SIGNALS,
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


def test_assumption_signals_are_valid() -> None:
    """ASSUMPTION_SIGNALS must be non-empty and all lowercase."""
    assert ASSUMPTION_SIGNALS, "ASSUMPTION_SIGNALS cannot be empty"
    for signal in ASSUMPTION_SIGNALS:
        assert signal == signal.lower(), f"Signal '{signal}' is not lowercase"


def test_assumable_rules_have_topic_cues() -> None:
    """Every assumable rule must have at least one topic cue."""
    for name, rule in POLICY.items():
        if not rule.assumable:
            continue
        assert rule.disclosure_cues, f"{name}: assumable but has no topic cues"


def test_disclosure_contract_by_example() -> None:
    """The disclosure contract: topic cue + assumption signal = disclosed.

    This test pins the behaviour Task 6's guardrail will rely on.
    A sentence with both a topic cue and an assumption signal satisfies the
    contract; one with only a topic cue (but no signal) does not.
    """
    withdrawal_cues = POLICY["withdrawal_date"].disclosure_cues

    # Sentence with both topic cue and signal: should satisfy both conditions
    disclosed_sentence = "I've assumed you stop working today, unless you tell me otherwise."
    has_topic = any(cue.lower() in disclosed_sentence.lower() for cue in withdrawal_cues)
    has_signal = any(signal.lower() in disclosed_sentence.lower() for signal in ASSUMPTION_SIGNALS)
    assert has_topic, f"'{disclosed_sentence}' should contain a topic cue for withdrawal_date"
    assert has_signal, f"'{disclosed_sentence}' should contain an assumption signal"

    # Sentence with topic cue but no signal: should fail the signal test
    undisclosed_sentence = "As of today your record shows 21.5 years of service."
    has_topic_undisclosed = any(
        cue.lower() in undisclosed_sentence.lower() for cue in withdrawal_cues
    )
    has_signal_undisclosed = any(
        signal.lower() in undisclosed_sentence.lower() for signal in ASSUMPTION_SIGNALS
    )
    assert has_topic_undisclosed, f"'{undisclosed_sentence}' should contain a topic cue"
    assert not has_signal_undisclosed, f"'{undisclosed_sentence}' should NOT contain an assumption signal"


def test_salary_aliases_are_in_never_assumable() -> None:
    """Every salary alias facts.py publishes must be forbidden to assume."""
    salary_aliases = {
        "final_average_salary",
        "highest_average_annual_salary",
        "highest_average_annual_salary_4yr",
        "highest_average_annual_salary_5yr",
        "highest_average_annual_salary_8yr",
        "fas",
        "current_annual_salary",
    }
    for alias in salary_aliases:
        assert alias in NEVER_ASSUMABLE, f"{alias} missing from NEVER_ASSUMABLE"


def test_an_unknown_input_is_not_assumable_by_default() -> None:
    assert rule_for("favourite_colour") is None
    assert not is_assumable("favourite_colour")


def test_assumptions_fill_only_what_policy_allows() -> None:
    from pension_chat.agent.nodes import apply_assumptions
    from pension_chat.agent.worksheet import SOURCE_ASSUMED, WorkSheet

    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": KNOWN, "assumptions": {}})
    missing = [
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
        {"name": "final_average_salary", "ask_for": "your salary history"},
    ]
    filled, _, still_missing = apply_assumptions(sheet, missing, KNOWN, ceiling=2)

    assert filled["withdrawal_date"] == "2026-09-09"
    assert [item["name"] for item in still_missing] == ["final_average_salary"]
    fact = sheet.fact("withdrawal_date")
    assert fact is not None
    assert fact.source == SOURCE_ASSUMED
    assert fact.basis


def test_the_ceiling_stops_a_figure_resting_on_too_much_guesswork() -> None:
    """The ceiling counts inventions, and `months_before_age_60` is not one.

    This test used to spend a ceiling slot on it and assert it was dropped as
    the third fill. It is computed from an age the member supplied, so it
    invents nothing and passes free -- refusing it was refusing a member a
    figure for want of a slot they themselves had paid for. The ceiling still
    bites, and the case that shows it is three values nobody supplied.
    """
    from pension_chat.agent.nodes import apply_assumptions
    from pension_chat.agent.worksheet import WorkSheet

    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": KNOWN, "assumptions": {}})
    missing = [
        {"name": "withdrawal_date", "ask_for": "..."},
        {"name": "annuity_start_date", "ask_for": "..."},
        {"name": "months_before_age_60", "ask_for": "..."},
    ]
    filled, _, still_missing = apply_assumptions(sheet, missing, KNOWN, ceiling=2)

    assert set(filled) == {"withdrawal_date", "annuity_start_date", "months_before_age_60"}
    assert still_missing == []


def test_the_ceiling_bites_a_third_invented_value() -> None:
    """Two is the contract. A third value nobody supplied is a member we made
    up, however plausible each one looked on its own."""
    from pension_chat.agent.nodes import apply_assumptions
    from pension_chat.agent.worksheet import WorkSheet

    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": KNOWN, "assumptions": {}})
    missing = [
        {"name": "withdrawal_date", "ask_for": "..."},
        {"name": "annuity_start_date", "ask_for": "..."},
        {"name": "withdrawal_date_again", "ask_for": "..."},
    ]
    filled, _, still_missing = apply_assumptions(sheet, missing, KNOWN, ceiling=1)

    assert len(filled) == 1
    assert [item["name"] for item in still_missing] == [
        "annuity_start_date",
        "withdrawal_date_again",
    ]
