"""Tests for the conversation working sheet.

The sheet is a view over durable state keys that already exist, so these
tests pin two things: that a round trip through state loses nothing, and
that provenance actually governs what a figure is allowed to rest on.
"""

from __future__ import annotations

from typing import Any

from pension_chat.agent.worksheet import (
    SOURCE_ASSUMED,
    SOURCE_RECORD,
    SOURCE_STATED,
    SOURCE_WITHHELD,
    Fact,
    WorkSheet,
)


def _state() -> dict[str, Any]:
    return {
        "turn_index": 2,
        "member_facts": {"age": 56, "service_years": 21.5, "final_average_salary": 69956.4},
        "assumptions": {},
        "baseline": None,
        "scenario_set": {},
        "open_question": None,
    }


def test_member_facts_arrive_as_record_provenance() -> None:
    sheet = WorkSheet.from_state(_state())
    fact = sheet.fact("age")
    assert fact is not None
    assert fact.source == SOURCE_RECORD
    assert fact.value == 56


def test_a_stated_value_overrides_the_record_and_keeps_its_source() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 23.0, SOURCE_STATED, turn=2)
    fact = sheet.fact("service_years")
    assert fact is not None
    assert fact.value == 23.0
    assert fact.source == SOURCE_STATED


def test_withheld_facts_are_known_but_not_usable() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 21.5, SOURCE_WITHHELD, basis="premise is Article 12")
    assert sheet.knows("service_years") is True
    assert "service_years" not in sheet.usable_values()


def test_assumed_facts_are_usable_and_reportable() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today")
    assert sheet.usable_values()["withdrawal_date"] == "2026-09-09"
    assert "withdrawal_date" in sheet.assumed_facts()


def test_revising_an_input_makes_the_figures_that_used_it_stale() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_figure(
        "now",
        {"annual_amount": "32126.78"},
        {"section": "11-134", "subsection": "(a)"},
        rests_on=("service_years", "final_average_salary"),
        turn=1,
    )
    stale = sheet.apply_revisions({"service_years": 23.0}, turn=2)
    assert stale == {"now"}
    assert sheet.stale_figures() == ["now"]
    fact = sheet.fact("service_years")
    assert fact is not None
    assert fact.source == SOURCE_STATED


def test_revising_an_unrelated_input_leaves_figures_alone() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_figure(
        "now", {"annual_amount": "1"}, {"section": "11-134"},
        rests_on=("service_years",), turn=1,
    )
    assert sheet.apply_revisions({"spouse_age": 53}, turn=2) == set()
    assert sheet.stale_figures() == []


def test_asks_for_things_already_known_are_dropped() -> None:
    sheet = WorkSheet.from_state(_state())
    kept = sheet.filter_asks([
        {"name": "age", "ask_for": "your date of birth"},
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
    ])
    assert [item["name"] for item in kept] == ["withdrawal_date"]


def test_a_withheld_ask_is_kept_but_marked_so_it_can_be_phrased_honestly() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 21.5, SOURCE_WITHHELD, basis="premise is Article 12")
    kept = sheet.filter_asks([{"name": "service_years", "ask_for": "your years of service"}])
    assert len(kept) == 1
    assert kept[0]["withheld"] is True
    assert kept[0]["withheld_basis"] == "premise is Article 12"


def test_a_round_trip_through_state_preserves_facts_figures_and_dependencies() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("withdrawal_date", "2026-09-09", SOURCE_ASSUMED, basis="today", turn=2)
    sheet.record_figure(
        "now", {"annual_amount": "32126.78"}, {"section": "11-134"},
        rests_on=("service_years",), turn=2,
    )
    state = {**_state(), **sheet.to_state_update()}
    restored = WorkSheet.from_state(state)
    fact = restored.fact("withdrawal_date")
    assert fact is not None
    assert fact.source == SOURCE_ASSUMED
    assert fact.basis == "today"
    assert restored.figures["now"].rests_on == ("service_years",)


def test_filter_asks_is_idempotent_and_preserves_order() -> None:
    """Running the filter on its own output must change nothing.

    The fixtures deliberately cover all three outcomes -- a fact the sheet holds
    usably (dropped), one it holds but cannot use (kept and marked), and one it
    does not hold (kept) -- because with fixtures that match no fact at all this
    test passed against the pre-change code and guarded none of the marking.
    """
    sheet = WorkSheet.from_state(_state())
    sheet.record_fact("service_years", 21.5, SOURCE_WITHHELD, basis="premise is Article 12")
    sheet.facts["spouse_age"] = Fact("spouse_age", None, SOURCE_STATED, asked=True, turn=1)
    asks = [
        {"name": "age", "ask_for": "your date of birth"},
        {"name": "service_years", "ask_for": "your years of service"},
        {"name": "spouse_age", "ask_for": "your spouse's age"},
        {"name": "withdrawal_date", "ask_for": "the date you plan to stop working"},
    ]

    once = sheet.filter_asks(asks)
    twice = sheet.filter_asks(once)

    # `age` is held usably, so it is dropped and stays dropped.
    assert [a["name"] for a in once] == ["service_years", "spouse_age", "withdrawal_date"]
    assert [a["name"] for a in twice] == ["service_years", "spouse_age", "withdrawal_date"]
    # The marks survive a second pass rather than being recomputed away.
    assert once[0]["withheld"] is True and twice[0]["withheld"] is True
    assert once[0]["withheld_basis"] == twice[0]["withheld_basis"] == "premise is Article 12"
    assert once[1]["already_asked"] is True and twice[1]["already_asked"] is True
    assert "withheld" not in twice[2] and "already_asked" not in twice[2]


def test_an_ask_already_put_to_the_member_is_not_repeated() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.facts["spouse_age"] = Fact("spouse_age", None, SOURCE_STATED, asked=True, turn=1)
    kept = sheet.filter_asks([{"name": "spouse_age", "ask_for": "your spouse's age"}])
    assert kept[0]["already_asked"] is True


def test_record_usable_false_marks_member_facts_withheld() -> None:
    sheet = WorkSheet.from_state(_state(), record_usable=False)
    fact = sheet.fact("service_years")
    assert fact is not None
    assert fact.source == SOURCE_WITHHELD
    assert fact.basis
    assert sheet.knows("service_years") is True
    assert "service_years" not in sheet.usable_values()


def test_record_usable_false_still_asks_for_a_starved_fact() -> None:
    sheet = WorkSheet.from_state(_state(), record_usable=False)
    kept = sheet.filter_asks([{"name": "service_years", "ask_for": "your years of service"}])
    assert len(kept) == 1
    assert kept[0]["withheld"] is True
    assert kept[0]["withheld_basis"]


def test_record_usable_false_leaves_an_assumptions_overlay_fact_alone() -> None:
    state = {
        **_state(),
        "assumptions": {
            "withdrawal_date": {"value": "2026-09-09", "source": SOURCE_ASSUMED, "basis": "today"},
        },
    }
    sheet = WorkSheet.from_state(state, record_usable=False)
    fact = sheet.fact("withdrawal_date")
    assert fact is not None
    assert fact.source == SOURCE_ASSUMED
    assert fact.value == "2026-09-09"
    assert "withdrawal_date" in sheet.usable_values()


def test_record_usable_defaults_to_true_and_leaves_the_normal_path_unchanged() -> None:
    sheet = WorkSheet.from_state(_state())
    fact = sheet.fact("service_years")
    assert fact is not None
    assert fact.source == SOURCE_RECORD
    assert fact.basis == ""
    assert "service_years" in sheet.usable_values()


# ── Against the shapes production actually persists ───────────────
#
# The tests above build state by hand, which is how the sheet came to hold no
# figures at all in production: `graph.turn_record` writes `baseline` flat --
# `annual_amount`, `monthly_amount`, `inputs_used` -- and the reader only
# recognised a nested `{"result": ...}`. Every downstream consequence followed
# from that one mismatch, so these go through the real writer.

def _persisted_turn() -> dict[str, Any]:
    """Durable state as `graph.turn_record` writes it after a real calculation."""
    from pension_chat.agent.graph import turn_record

    finished = {
        "final_answer": "Your pension is ...",
        "turn_index": 1,
        "calculation_result": {
            "retirement_annuity": {
                "section": "11-134",
                "subsection": "(a)",
                "base": {
                    "ok": True,
                    "annual_amount": "32126.78",
                    "monthly_amount": "2677.23",
                    "inputs_used": {
                        "highest_average_annual_salary_4yr": "69956.40",
                        "creditable_service_years": "21.5",
                        "months_before_age_60": "44",
                    },
                },
            }
        },
    }
    update = turn_record(finished)
    assert update is not None
    return {**_state(), **update}


def test_the_sheet_holds_the_figure_that_turn_record_actually_persisted() -> None:
    sheet = WorkSheet.from_state(_persisted_turn())

    figure = sheet.figures.get("now")
    assert figure is not None
    assert figure.result["annual_amount"] == "32126.78"
    assert figure.provision["section"] == "11-134"


def test_rests_on_comes_from_the_inputs_the_figure_actually_used() -> None:
    sheet = WorkSheet.from_state(_persisted_turn())

    assert set(sheet.figures["now"].rests_on) == {
        "highest_average_annual_salary_4yr",
        "creditable_service_years",
        "months_before_age_60",
    }


def test_a_correction_makes_the_persisted_figure_stale() -> None:
    """The headline case: "actually it's more like 23 years" must recompute.

    The member revises `service_years`; the recipe consumed
    `creditable_service_years`. Same quantity, so the same figure is stale.
    """
    sheet = WorkSheet.from_state(_persisted_turn())

    # `turn_record` also files the unscenarioed base under the label a member
    # would use for it, so both copies of the same figure go stale together.
    assert sheet.apply_revisions({"service_years": 23}, turn=2) == {
        "now", "if you retire now"
    }
    assert "now" in sheet.stale_figures()


def test_a_carried_scenario_goes_stale_with_the_figure_it_varied() -> None:
    from pension_chat.agent.graph import turn_record

    update = turn_record({
        "final_answer": "...",
        "turn_index": 1,
        "calculation_result": {
            "retirement_annuity": {
                "section": "11-134",
                "base": {
                    "ok": True,
                    "annual_amount": "32126.78",
                    "monthly_amount": "2677.23",
                    "inputs_used": {"creditable_service_years": "21.5"},
                },
                "scenarios": [
                    {
                        "label": "at age 60",
                        "annual_amount": "42280.10",
                        "monthly_amount": "3523.34",
                        "assumptions": {"service_years": "25.2"},
                    }
                ],
            }
        },
    })
    assert update is not None
    sheet = WorkSheet.from_state({**_state(), **update})

    assert sheet.apply_revisions({"years_of_service": 23}, turn=2) == {"now", "at age 60"}


def test_the_persisted_shape_survives_a_round_trip_through_the_sheet() -> None:
    """One shape, not two.

    `graph._verify` reads `baseline["turn"]` and each scenario's
    `annual_amount` straight off the durable key, so writing the sheet back in
    a nested shape would leave those readers looking at the wrong level on any
    turn that persisted the sheet without recalculating.
    """
    state = _persisted_turn()
    sheet = WorkSheet.from_state(state)
    written = sheet.to_state_update()

    assert written["baseline"]["annual_amount"] == "32126.78"
    assert written["baseline"]["turn"] == state["baseline"]["turn"]
    assert WorkSheet.from_state({**_state(), **written}).figures["now"].rests_on == (
        sheet.figures["now"].rests_on
    )


def test_a_withheld_fact_is_never_written_into_durable_state() -> None:
    """A stipulated premise must not poison the record for later turns.

    `withheld` is a per-turn view of a record fact. Persisted, it is overlaid
    back on top of `member_facts` next turn and wins forever -- so after one
    "if I were a park employee" turn the conversation would hold no usable
    values at all.
    """
    sheet = WorkSheet.from_state(_state(), record_usable=False)
    assert sheet.usable_values() == {}

    persisted = sheet.to_state_update()
    assert persisted["assumptions"] == {}

    later = WorkSheet.from_state({**_state(), **persisted})
    assert later.usable_values()["service_years"] == 21.5
    assert later.fact("age") is not None
    assert later.fact("age").source == SOURCE_RECORD  # type: ignore[union-attr]


def test_a_correction_of_the_same_number_written_differently_is_not_a_revision() -> None:
    sheet = WorkSheet.from_state(_state())
    sheet.record_figure(
        "now", {"annual_amount": "1"}, {"section": "11-134"},
        rests_on=("service_years",), turn=1,
    )
    assert sheet.apply_revisions({"service_years": "21.50"}, turn=2) == set()
    assert sheet.stale_figures() == []


# ── A framing default is not a record fact ────────────────────────


def test_a_framing_default_is_labelled_assumed_not_record() -> None:
    """`withdrawal_date` is a reading of the question, not a line in the file.

    The facts layer fills it with today so a `date_diff` has something to
    subtract. Inheriting the record's provenance made a figure that rests on
    "you stop working today" come out fully sourced: rung 1, no assumptions
    block, and nothing in the answer saying which retirement date it used --
    which matters most to the member whose pension grows with every further
    year of service.
    """
    sheet = WorkSheet.from_state(
        {
            "turn_index": 1,
            "member_facts": {
                "age": 56,
                "withdrawal_date": "2026-09-10",
                "annuity_start_date": "2026-09-10",
                "as_of_date": "2026-09-10",
            },
            "assumptions": {},
        }
    )
    assert sheet.facts["withdrawal_date"].source == SOURCE_ASSUMED
    assert sheet.facts["annuity_start_date"].source == SOURCE_ASSUMED
    assert sheet.facts["withdrawal_date"].basis
    # Today's date is not an assumption, and the member's age is still theirs.
    assert sheet.facts["as_of_date"].source == SOURCE_RECORD
    assert sheet.facts["age"].source == SOURCE_RECORD
    # Still usable: the label was the defect, so the figure must still compute.
    assert "withdrawal_date" in sheet.usable_values()


def test_a_stated_retirement_date_outranks_the_framing_default() -> None:
    """Labelling the default assumed must not stop the member overriding it."""
    sheet = WorkSheet.from_state(
        {
            "turn_index": 2,
            "member_facts": {"withdrawal_date": "2026-09-10"},
            "assumptions": {
                "withdrawal_date": {
                    "value": "2030-01-01",
                    "source": SOURCE_STATED,
                    "turn": 2,
                }
            },
        }
    )
    assert sheet.facts["withdrawal_date"].source == SOURCE_STATED
    assert sheet.facts["withdrawal_date"].value == "2030-01-01"
