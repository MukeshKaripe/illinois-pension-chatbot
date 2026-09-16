"""Which kind of gap this is decides what to do about it.

A value gap changes the number: fill it, say so, carry on. A governing gap
changes *which provision applies*, so filling it silently answers a question
about a member who may not exist. Those get asked about -- or computed both
ways and compared.
"""

from __future__ import annotations

from pension_chat.agent.gaps import GOVERNING, classify, split


def test_a_salary_gap_changes_the_number():
    assert classify("final_average_salary") == "value"
    assert classify("service_years") == "value"


def test_a_tier_gap_changes_which_law_applies():
    assert classify("tier") == "governing"
    assert classify("article_number") == "governing"
    assert classify("employment_status") == "governing"


def test_an_unknown_input_is_treated_as_a_value():
    """A routing field is a short, known list. Guessing that something
    unrecognised routes would turn every new recipe input into a question."""
    assert classify("months_before_age_60") == "value"
    assert classify("favourite_colour") == "value"


def test_the_routing_fields_are_the_ones_the_record_is_keyed_on():
    """Kept in step with the conflict rule coverage.py already applies: a
    conflict in one of these is a question, a conflict in a value is an
    override-with-note."""
    assert GOVERNING == frozenset({"article_number", "employment_status", "tier"})


def test_every_routing_field_is_one_nobody_may_assume():
    """The two tables must not drift: a field that routes and is assumable
    would be silently filled in before anyone thought to ask."""
    from pension_chat.calculation.assumption_policy import NEVER_ASSUMABLE

    assert GOVERNING <= NEVER_ASSUMABLE


def test_split_separates_the_two_kinds():
    governing, value = split(
        [{"name": "tier"}, {"name": "final_average_salary"}, {"name": "service_years"}]
    )
    assert [g["name"] for g in governing] == ["tier"]
    assert [v["name"] for v in value] == ["final_average_salary", "service_years"]


def test_split_copies_rather_than_aliases():
    """The caller annotates these for the answer; mutating the original list
    would rewrite what `missing_inputs` reports."""
    original = [{"name": "tier"}]
    governing, _ = split(original)
    governing[0]["name"] = "changed"
    assert original[0]["name"] == "tier"


def test_nothing_missing_splits_to_nothing():
    assert split([]) == ([], [])
