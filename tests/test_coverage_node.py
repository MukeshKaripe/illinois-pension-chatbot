"""The coverage node on a turn that talks about employment status.

"If I retired at the end of this year" is a remark about employment status, not
about which Article governs, and it used to leave a first turn with no coverage
verdict at all -- the status branch returned before anything set one. Every
case in `evals/fund_smoke.yaml` found it, because every one asks that question.

The status branch settles by looking at the row, never the corpus, so these run
offline.
"""

from __future__ import annotations

from pension_chat.agent.nodes import (
    _available_facts,
    _coverage_unresolved,
    assess_coverage,
)

RETIRING = "Roughly what would my pension be if I retired at the end of this year?"


async def test_a_supposition_about_retiring_leaves_coverage_on_the_record() -> None:
    update = await assess_coverage(
        {
            "question": RETIRING,
            "turn_index": 0,
            "employee_record": {"article_number": 3, "employment_status": "active"},
        }
    )
    assert update["coverage"]["article"] == "3"
    assert update["coverage"]["basis"] == "record"
    # A supposition is not a claim about the file, so there is nothing to ask.
    assert "clarification" not in update


async def test_a_status_claim_that_disagrees_still_asks_and_keeps_coverage() -> None:
    update = await assess_coverage(
        {
            "question": "I'm retired now. What is my pension?",
            "turn_index": 0,
            "employee_record": {"article_number": 11, "employment_status": "active"},
        }
    )
    assert update["clarification"]["about"] == "employment_status"
    assert update["coverage"]["article"] == "11"


async def test_a_status_remark_does_not_overwrite_settled_coverage() -> None:
    settled = {"article": "12", "basis": "member_confirmed", "record_article": "11"}
    update = await assess_coverage(
        {
            "question": RETIRING,
            "turn_index": 2,
            "employee_record": {"article_number": 11, "employment_status": "active"},
            "coverage": settled,
        }
    )
    assert "coverage" not in update


# ── An unsettled Article starves the figure ───────────────────────
#
# The rule CLAUDE.md already states -- a figure is computable exactly when no
# input is borrowed from a record filed under another Article -- applied where
# it was not. `_coverage_elsewhere` is false for a `record` basis, so an
# unresolved routing conflict stopped no calculation on either agent. The
# pipeline passed G31 because `understand_turn` happened to classify "how is my
# pension calculated?" as wanting no figure; the loop walked through the gap and
# quoted $32,126.78 on a question about which fund the member is even in.


_OPEN = {
    "member_facts": {"service_years": "21.5", "age": 56},
    "coverage": {
        "article": "11",
        "record_article": "11",
        "basis": "record",
        "conflict": True,
        "why": "elsewhere",
    },
}


def test_an_open_coverage_question_starves_the_record() -> None:
    """While the Article is unsettled, every input is potentially borrowed."""
    assert _coverage_unresolved(_OPEN) is True
    assert _available_facts(_OPEN) == {}


def test_the_member_being_unsure_starves_it_too() -> None:
    """`why: doubted` is the member telling you the question is open, which
    outranks every other kind of uncertainty here."""
    state = {**_OPEN, "coverage": {**_OPEN["coverage"], "why": "doubted"}}
    assert _available_facts(state) == {}


def test_a_settled_record_costs_nothing() -> None:
    """Nearly every turn. A member who said nothing about their job must not
    pay for this."""
    state = {
        "member_facts": {"service_years": "21.5"},
        "coverage": {"article": "11", "record_article": "11", "basis": "record"},
    }
    assert _coverage_unresolved(state) is False
    assert _available_facts(state)["service_years"] == "21.5"


def test_a_spent_budget_settles_it() -> None:
    """At the cap the agent proceeds on the record and says what it assumed.
    `assess_coverage` clears `conflict` explicitly to mean that, and starving
    here would be asking a fourth time by another route."""
    state = {
        **_OPEN,
        "coverage": {**_OPEN["coverage"], "conflict": False, "why": "budget_spent"},
    }
    assert _coverage_unresolved(state) is False
    assert _available_facts(state)["service_years"] == "21.5"


def test_a_confirmed_answer_settles_it() -> None:
    """`bind_reply` clears the conflict, and from there the ordinary
    coverage-elsewhere rule takes over -- which starves for its own reason."""
    state = {
        "member_facts": {"service_years": "21.5"},
        "coverage": {
            "article": "12",
            "record_article": "11",
            "basis": "member_confirmed",
            "conflict": False,
        },
    }
    assert _coverage_unresolved(state) is False
    assert _available_facts(state) == {}, "elsewhere, which is the older rule"


def test_no_coverage_verdict_at_all_is_not_a_conflict() -> None:
    assert _coverage_unresolved({"member_facts": {"age": 56}}) is False
