"""Whether a turn may compute on the member's record at all.

`assess_coverage` went with the pipeline at cutover, and its three tests here
went with it -- the loop settles coverage through `check_coverage`, which has
its own file. What stayed is the half that was never the node's: the rule
CLAUDE.md states, that **a figure is computable exactly when no input is
borrowed from a record filed under another Article**, enforced inside
`resolve_inputs` and therefore reached by every agent that computes anything.

Offline: these read state and never touch the corpus.
"""

from __future__ import annotations

from pension_chat.agent.nodes import _available_facts, _coverage_unresolved


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
