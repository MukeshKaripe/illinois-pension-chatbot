"""Four gap states, four behaviours -- decided by counting, not by the model.

"Assumptions are counted, never declared. Their number and identity come from
ledger provenance, so 'what I assumed' cannot be wrong and the ceiling cannot be
evaded."

The behaviour this replaces is one sentence: "Could not compute: missing X. Ask
the member for what is actually needed." It fired for every shape of gap,
including the ones the Code itself could have closed, and it is the sentence
behind a third of the measured hedges.
"""

from __future__ import annotations

from pension_chat.agent.nodes import gap_verdict


def test_nothing_missing_computes():
    assert gap_verdict([], filled={}) == "compute"


def test_filled_value_gaps_compute_and_say_so():
    assert (
        gap_verdict(
            [], filled={"withdrawal_date": "assumed", "months_before_age_60": "derived"}
        )
        == "compute_with_assumptions"
    )


def test_a_value_gap_nobody_can_fill_becomes_a_worked_example():
    """Not a refusal. The Code states the formula even when it does not state
    this member, so the answer shows the shape and what would make it theirs."""
    assert gap_verdict([{"name": "final_average_salary"}], filled={}) == "worked_example"


def test_a_governing_gap_is_asked_about_however_few_there_are():
    """One unknown tier outranks any number of fillable value gaps: it is not a
    missing number, it is two different provisions."""
    assert gap_verdict([{"name": "tier"}], filled={}) == "ask"
    assert gap_verdict([{"name": "tier"}], filled={"withdrawal_date": "assumed"}) == "ask"


def test_a_governing_gap_outranks_a_value_gap_in_the_same_turn():
    assert (
        gap_verdict([{"name": "final_average_salary"}, {"name": "tier"}], filled={})
        == "ask"
    )


def test_more_than_two_invented_fills_is_not_a_figure():
    """The ceiling is the contract. Three invented values is a member we made
    up, however plausible each one looked on its own."""
    assert (
        gap_verdict([], filled={"a": "assumed", "b": "assumed", "c": "assumed"})
        == "worked_example"
    )


def test_derivations_and_citations_do_not_count_against_the_ceiling():
    """Two of these three came from the member and from the Code. Counting them
    would refuse a figure that rests almost entirely on evidence."""
    assert (
        gap_verdict(
            [],
            filled={
                "months_before_age_60": "derived",
                "age_at_annuity_start": "statutory_parameter",
                "withdrawal_date": "assumed",
            },
        )
        == "compute_with_assumptions"
    )


# ── What the model is actually told ───────────────────────────────
#
# The verdict only matters if it reaches the model that writes the sentence.
# `_describe` is the one place a gap becomes words.

from pension_chat.agent.tools import _describe  # noqa: E402


def _described(missing, verdict):
    return _describe({}, missing, None, [], verdict=verdict)


def test_a_governing_gap_is_offered_the_branch_not_just_the_question():
    text = _described([{"name": "tier", "ask_for": "which tier you are in"}], "ask")
    assert "which tier you are in" in text
    assert "compute both and compare" in text
    assert "Do not pick one." in text


def test_a_value_gap_is_told_not_to_stop_at_saying_so():
    """The refusal this replaces -- "Could not compute: missing X. Ask the
    member" -- is the sentence behind a third of the measured hedges."""
    text = _described(
        [{"name": "final_average_salary", "ask_for": "your salary history"}],
        "worked_example",
    )
    assert "Do NOT stop at saying so" in text
    assert "work it through on the figures the Code itself states" in text
    assert "what you would need to make it theirs" in text


def test_a_governing_gap_still_names_the_values_it_also_lacks():
    text = _described(
        [
            {"name": "tier", "ask_for": "which tier you are in"},
            {"name": "final_average_salary", "ask_for": "your salary history"},
        ],
        "ask",
    )
    assert "your salary history" in text


def test_no_verdict_keeps_the_old_sentence():
    """`_describe` is called from more than one place. A caller that has not
    been given a verdict must not silently get the new instructions."""
    text = _described([{"name": "final_average_salary", "ask_for": "your salary"}], "")
    assert "Could not compute: missing your salary" in text


def test_a_value_gap_ends_with_a_direct_question():
    # Live, 2026-10-01: "the chat bot did not ask me about the required details"
    # -- the answer listed what it lacked as a statement, and asked for a birth
    # date where an age is enough.
    text = _described(
        [{"name": "service_years", "ask_for": "how many years of service you have with the fund"},
         {"name": "birth_date", "ask_for": "your age (or date of birth)"}],
        "worked_example",
    )
    assert "one direct question" in text
    assert "how many years of service you have with the fund" in text


def test_a_birth_date_is_asked_for_as_an_age() -> None:
    from pension_chat.agent.nodes import _ASK_INSTEAD

    assert _ASK_INSTEAD["birth_date"].startswith("your age")
