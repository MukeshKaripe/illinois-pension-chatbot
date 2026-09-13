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

_MEMBER = {"employee_record": {"article_number": 11}, "turn_index": 2}


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
