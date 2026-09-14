"""Which Article governs the question, decided while the investigation is running.

The pipeline settles coverage in a node before retrieval, on a regex that has to
spot a self-description in the member's sentence. The loop cannot: it has no
stage to put it in, and it does not need one -- the model reading the question
knows perfectly well that "if I were a Chicago police officer" is a supposition
about somebody else's fund. What it must not do is decide *which* fund, or
whether to believe it over the record. So the model supplies the description and
the act; `retrieve_coverage`, `assess` and `adjudicate` do exactly what they do
in the pipeline, and `adjudicate` still decides in pure Python.

Offline: retrieval and the assessment are faked. `adjudicate` is real, because
it is the determination under test.
"""

from __future__ import annotations

from pension_chat.agent import coverage as coverage_mod
from pension_chat.agent import tools
from pension_chat.agent.retrieval import RetrievedSection

_POLICE = RetrievedSection(
    section="5-214",
    article="5",
    title="Policeman",
    text="Any member of the regularly constituted police department of the city.",
    score=2.4,
)
_PARK = RetrievedSection(
    section="12-101",
    article="12",
    title="Terms defined",
    text="Employee of a board of park commissioners.",
    score=2.0,
)
_LABORER = RetrievedSection(
    section="11-110",
    article="11",
    title="Employee",
    text="Any person employed by the board.",
    score=1.1,
)

# The member's own words, alongside the record. `check_coverage` adjudicates a
# description against a file, and a question it raises is a question put to the
# member -- so what they actually said has to be in the state the tool reads.
_MEMBER = {
    "employee_record": {"article_number": 11},
    "turn_index": 2,
    "question": "i am a park employee, what is my pension?",
}


def _corpus(monkeypatch, sections, candidates):
    """Fake the two calls that reach the network, recording what they saw."""
    seen: dict = {}

    async def fake_retrieve(description, *, also_covering=None, **kwargs):
        seen["description"] = description
        seen["also_covering"] = also_covering
        return list(sections)

    async def fake_assess(description, retrieved, **kwargs):
        seen["assessed"] = description
        return coverage_mod.CoverageReport(
            candidates=list(candidates),
            considered_sections=[s.section for s in retrieved],
        )

    monkeypatch.setattr(tools, "retrieve_coverage", fake_retrieve)
    monkeypatch.setattr(tools.coverage_mod, "assess", fake_assess)
    return seen


_CHICAGO_POLICE = [
    {"article": "5", "section": "5-214", "test": "member of the police department", "rank": 0}
]
_PARK_EMPLOYEE = [
    {"article": "12", "section": "12-101", "test": "board of park commissioners", "rank": 0}
]
_LABORERS = [{"article": "11", "section": "11-110", "test": "employed by the board", "rank": 0}]


async def test_a_supposed_job_moves_the_article_and_stays_a_premise(monkeypatch):
    """L08. The frame was created with `article: None`, so retrieval stayed on
    Article 11 and answered a question about somebody else's law with the
    member's own -- the harmful shape, not merely an unhelpful one."""
    _corpus(monkeypatch, [_POLICE, _LABORER], _CHICAGO_POLICE)

    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a Chicago police officer", "act": "stipulated"},
        dict(_MEMBER),
    )

    assert update["coverage"]["article"] == "5"
    assert update["coverage"]["basis"] == coverage_mod.BASIS_HYPOTHETICAL
    assert update["hypothetical_frame"]["article"] == "5"
    assert update["hypothetical_frame"]["premise"] == "a Chicago police officer"
    assert "5" in text
    assert "premise" in text.lower() or "suppos" in text.lower()


async def test_a_supposition_is_never_a_question(monkeypatch):
    """Asking someone to confirm a premise they just stipulated is the same
    deafness as ignoring one they asserted, wearing the opposite coat."""
    _corpus(monkeypatch, [_POLICE], _CHICAGO_POLICE)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a Chicago police officer", "act": "stipulated"},
        dict(_MEMBER),
    )
    assert update["coverage"]["conflict"] is False
    assert "ask" not in text.lower()


async def test_an_assertion_the_code_supports_is_settled(monkeypatch):
    _corpus(monkeypatch, [_LABORER], _LABORERS)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a city laborer", "act": "asserted"},
        dict(_MEMBER),
    )
    assert update["coverage"]["basis"] == coverage_mod.BASIS_STATUTORY
    assert update["coverage"]["conflict"] is False
    assert "hypothetical_frame" not in update, "only a supposition opens a frame"


async def test_an_assertion_against_the_record_is_a_question(monkeypatch):
    """The park-employee failure. Their words point one way and their file the
    other; the tool must say so rather than pick a side quietly."""
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a park employee", "act": "asserted"},
        dict(_MEMBER),
    )
    assert update["coverage"]["conflict"] is True
    assert update["coverage"]["article"] == "11", "the record holds until they settle it"
    assert "12" in text and "11" in text
    assert "ask" in text.lower()


async def test_doubt_is_always_a_question(monkeypatch):
    """*They* are unsure. Everything else here is the system being unsure."""
    _corpus(monkeypatch, [_PARK], _PARK_EMPLOYEE)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a park employee", "act": "doubted"},
        dict(_MEMBER),
    )
    assert update["coverage"]["conflict"] is True
    assert "ask" in text.lower()


async def test_the_record_goes_in_alongside_the_description(monkeypatch):
    """`assess` is being asked to weigh a description against a record, and
    cannot do that having read only one side."""
    seen = _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    await tools.run_tool(
        "check_coverage", {"description": "a park employee", "act": "asserted"}, dict(_MEMBER)
    )
    assert seen["also_covering"] == "11"


async def test_the_sections_behind_the_verdict_become_readable(monkeypatch):
    """A verdict the answer may not cite is a verdict the answer cannot explain.

    The pipeline pins the candidates' own sections into the turn's retrieval for
    exactly this reason -- otherwise the model has a section number it never
    read and verification withdraws the sentence that names it.
    """
    _corpus(monkeypatch, [_POLICE, _LABORER], _CHICAGO_POLICE)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a Chicago police officer", "act": "stipulated"},
        dict(_MEMBER),
    )
    assert "5-214" in [s["section"] for s in update["retrieved_sections"]]
    assert "### 40 ILCS 5/5-214" in text, "rendered the way the citation rule expects"
    assert "regularly constituted police department" in text


async def test_a_description_the_code_does_not_know_falls_back_to_the_record(monkeypatch):
    _corpus(monkeypatch, [_LABORER], [])
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a dog walker", "act": "asserted"},
        dict(_MEMBER),
    )
    assert update["coverage"]["article"] == "11"
    assert update["coverage"]["basis"] == coverage_mod.BASIS_RECORD
    assert "no coverage test" in text.lower() or "matched nothing" in text.lower()


async def test_an_anonymous_member_can_still_be_placed(monkeypatch):
    """Most of what this assistant answers well needs no record."""
    _corpus(monkeypatch, [_POLICE], _CHICAGO_POLICE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "a Chicago police officer", "act": "asserted"},
        {"turn_index": 0},
    )
    assert update["coverage"]["article"] == "5"
    assert update["coverage"]["basis"] == coverage_mod.BASIS_STATUTORY


async def test_a_missing_description_is_reported_not_raised():
    text, update = await tools.run_tool("check_coverage", {"act": "asserted"}, {})
    assert "description" in text
    assert update == {}


async def test_an_unknown_act_is_reported_not_raised():
    text, update = await tools.run_tool(
        "check_coverage", {"description": "a park employee", "act": "wondered"}, {}
    )
    assert "wondered" in text
    assert "stipulated" in text, "say what it could have said"
    assert update == {}


async def test_the_acts_the_schema_offers_are_the_acts_coverage_governs():
    """A guard against the two drifting apart: an act the schema offers that
    `adjudicate` has no branch for is silently read as an assertion."""
    schema = next(
        t for t in tools.COVERAGE_TOOLS if t["function"]["name"] == "check_coverage"
    )
    offered = set(schema["function"]["parameters"]["properties"]["act"]["enum"])
    assert offered == {coverage_mod.ASSERTED, coverage_mod.STIPULATED, coverage_mod.DOUBTED}


# ── A verdict with consequences ───────────────────────────────────
#
# `check_coverage` returned the right verdict on every failing case of the
# Phase 4 measurement -- `conflict: True` with the right `why` -- and the loop
# computed a figure and answered anyway. A determination the agent is merely
# told about is not a determination.


async def test_a_conflict_becomes_a_question_the_harness_can_see(monkeypatch):
    """The eval's `asks` check reads `clarification`, and so does
    `prepare_answer`. Without the key the turn cannot be said to have asked,
    however question-shaped its prose."""
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "a park employee", "act": "asserted"},
        dict(_MEMBER),
    )
    assert update["clarification"]["about"] == "coverage"
    assert update["clarification"]["described_as"] == "a park employee"
    assert [o["article"] for o in update["clarification"]["options"]] == ["12", "11"]
    assert update["clarifications_asked"] == 1


async def test_the_question_is_stored_so_the_next_turn_can_bind_it(monkeypatch):
    """G32, and it is CRITICAL. The member restated their coverage in the
    statute's own literal words and the loop had nothing to bind it to, so the
    verdict stayed on the record and the same figure came back."""
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "a park employee", "act": "asserted"},
        dict(_MEMBER),
    )
    assert update["open_question"] == update["clarification"]
    assert update["open_question"]["asked_turn"] == 2, "the turn it was asked on"


async def test_no_conflict_asks_nothing(monkeypatch):
    _corpus(monkeypatch, [_LABORER], _LABORERS)
    _, update = await tools.run_tool(
        "check_coverage", {"description": "a city laborer", "act": "asserted"}, dict(_MEMBER)
    )
    assert "clarification" not in update
    assert "open_question" not in update


async def test_a_supposition_asks_nothing(monkeypatch):
    """Adopted whole: no question, no budget spent."""
    _corpus(monkeypatch, [_POLICE], _CHICAGO_POLICE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "a Chicago police officer", "act": "stipulated"},
        dict(_MEMBER),
    )
    assert "clarification" not in update


async def test_the_budget_is_spent_and_the_turn_proceeds(monkeypatch):
    """At the cap the agent proceeds on the record and says what it assumed.
    Asking a fourth time is its own failure, and one the member cannot escape."""
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "a park employee", "act": "asserted"},
        {**_MEMBER, "clarifications_asked": 3},
    )
    assert "clarification" not in update
    assert update["coverage"]["conflict"] is False
    assert update["coverage"]["why"] == "budget_spent"
    assert "already asked" in text.lower() or "no more" in text.lower()


async def test_a_confirmation_outranks_a_fresh_assessment(monkeypatch):
    """G32 turn 2, and it is CRITICAL.

    The member answered the question in the statute's own words, the binding
    worked -- and then the model called check_coverage again with the same
    description, which re-adjudicated from nothing and put the conflict back.
    The pipeline cannot do this: `assess_coverage` binds and returns, never
    reaching the assessment. Here the model chooses when to call, so the
    settled verdict has to survive being asked about again.
    """
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "an employee of a board of park commissioners", "act": "asserted"},
        {
            **_MEMBER,
            "coverage": {
                "article": "12",
                "record_article": "11",
                "basis": coverage_mod.BASIS_CONFIRMED,
                "conflict": False,
                "confirmed_by": "described the employer",
            },
        },
    )
    assert update["coverage"]["basis"] == coverage_mod.BASIS_CONFIRMED
    assert update["coverage"]["article"] == "12"
    assert "clarification" not in update, "they already answered it"
    assert "12" in text
    assert "confirmed" in text.lower() or "settled" in text.lower()


async def test_a_settled_verdict_does_not_block_a_new_description(monkeypatch):
    """Confirmation settles *that* question, not every later one. A member who
    confirmed Article 12 and then supposes a different job gets a fresh
    determination."""
    _corpus(monkeypatch, [_POLICE], _CHICAGO_POLICE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "a Chicago police officer", "act": "stipulated"},
        {
            **_MEMBER,
            "coverage": {
                "article": "12",
                "record_article": "11",
                "basis": coverage_mod.BASIS_CONFIRMED,
                "conflict": False,
            },
        },
    )
    assert update["coverage"]["basis"] == coverage_mod.BASIS_HYPOTHETICAL
    assert update["hypothetical_frame"]["article"] == "5"


# ── A question is put to the member, so the member must have spoken ──
#
# Case 16 and G30's twelfth turn, both CRITICAL, and the same defect. "An
# employee is covered by Article 12. At age 60 with 25 years of service, what
# retirement provisions should the system consider?" is a question about the
# law; "I think the pathway you picked was wrong. Re-run the applicability
# analysis without changing my facts" is a question about the analysis. Neither
# describes the member. The loop called `check_coverage` on both, the tool
# adjudicated the description against the member's record, found it disagreed,
# and answered "Which employer pays you?" -- to case 16 as its whole answer, and
# to G30 twelve turns into a conversation where coverage had been settled
# throughout.
#
# `assess_coverage` cannot do this: it fast-exits when `classify_self_reference`
# finds no self-description, and neither of these has one. The loop made the
# model the router and showed it none of that gate, which is the same shape as
# every other gap this phase found.
#
# The guard is narrow on purpose. It does not stop the model calling the tool,
# because noticing a self-description a regex misses is the loop's own
# advantage and the reason coverage is a tool here rather than a stage. It
# stops the one act that needs the member to have spoken: **turning a
# disagreement with their file into a question put to them.** Everything else
# the verdict carries -- which Article the law belongs to, the sections it
# rests on -- is about the Code and stands either way.


async def test_a_conflict_with_nobody_asks_nobody(monkeypatch):
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "an employee covered by Article 12", "act": "asserted"},
        {
            **_MEMBER,
            "question": (
                "An employee is covered by Article 12. At age 60 with 25 years of "
                "service, what retirement provisions should the system consider?"
            ),
        },
    )
    assert "clarification" not in update
    assert "open_question" not in update
    assert update["coverage"]["conflict"] is False


async def test_a_verdict_nobody_prompted_still_says_where_the_law_lives(monkeypatch):
    """The determination is not discarded, only its power to ask. Which Article
    the question's law belongs to is a fact about the Code, and the sections it
    rests on stay readable so the answer can cite them."""
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    text, update = await tools.run_tool(
        "check_coverage",
        {"description": "an employee covered by Article 12", "act": "asserted"},
        {**_MEMBER, "question": "what does Article 12 provide at age 60?"},
    )
    assert "12-101" in update["coverage_sections"]
    assert "12" in text


async def test_the_member_describing_themselves_is_still_asked(monkeypatch):
    """The guard must not swallow the case the layer exists for."""
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "a park employee", "act": "asserted"},
        {**_MEMBER, "question": "i am a park employee, what is my pension?"},
    )
    assert update["clarification"]["about"] == "coverage"


async def test_a_member_who_is_unsure_is_still_asked(monkeypatch):
    """"I think I'm a park employee" is the clearest case in the system for
    asking, and `classify_self_reference` reads it as doubted."""
    _corpus(monkeypatch, [_PARK, _LABORER], _PARK_EMPLOYEE)
    _, update = await tools.run_tool(
        "check_coverage",
        {"description": "a park employee", "act": "doubted"},
        {**_MEMBER, "question": "I think I'm a park employee -- what would I get?"},
    )
    assert update["clarification"]["about"] == "coverage"
