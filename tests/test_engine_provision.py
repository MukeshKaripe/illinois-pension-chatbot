"""Which provision the engine will use, asked before it is asked to compute.

G17, and it fails in the way that matters most to a member: turn 1 named
"Section 11-130" as their retirement provision, and turn 3 computed the figure
from **Section 11-134**. The agent named one provision and used another, which
is the exact thing the case is called for.

The engine is the authority for both. `resolve_recipes` looks the provision up
in the registry -- for an Article 11 Tier 1 member, Section 11-134(a), chosen
over 11-130, 11-131 and 11-164 on a `statutory_election` basis -- and the loop
could only reach that code by calling `compute_benefit`, which is precisely what
"do not calculate anything yet" forbids.

So the registry is consulted without computing, and **its answer arrives with
the section's text**, which is the half `find_provision` got wrong: a provision
named but not read is one verification will not let the answer cite.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod


def _fake_lookup(monkeypatch, rows: dict[tuple[int, int | None], list[dict]]):
    """Stand in for the registry, so this stays offline."""
    seen: list[tuple] = []

    async def fake(article, tier):
        seen.append((article, tier))
        return rows.get((article, tier), [])

    monkeypatch.setattr(loop_mod, "recipes_for", fake)
    return seen


_ANNUITY = {
    "formula_type": "retirement_annuity",
    "section": "11-134",
    "subsection": "(a)",
    "formula_name": "2.4% Minimum Retirement Annuity",
    "selection_basis": "statutory_election",
    "alternatives_considered": [{"section": "11-130"}, {"section": "11-131"}],
}
_MEMBER = {
    "question": "Which retirement provision applies to me? Do not calculate anything yet.",
    "turn_index": 0,
    "employee_record": {"article_number": 11},
    "member_facts": {"tier": 1, "article_number": 11},
    "coverage": {"article": "11", "basis": "record", "record_article": "11"},
}


async def test_the_engines_provision_is_read_without_computing(monkeypatch):
    _fake_lookup(monkeypatch, {(11, 1): [_ANNUITY]})
    assert await loop_mod._engine_provisions(_MEMBER) == [_ANNUITY]


async def test_a_turn_that_already_worked_a_figure_asks_nothing(monkeypatch):
    """`_worked_from` is the sharper statement when a figure exists — it names
    the provision this turn's number actually came from — so the registry is not
    consulted and no call is spent."""
    seen = _fake_lookup(monkeypatch, {(11, 1): [_ANNUITY]})
    assert await loop_mod._engine_provisions({
        **_MEMBER,
        "calculation_result": {"retirement_annuity": {"base": {"ok": True}}},
    }) == []
    assert seen == [], "the registry was not asked"


async def test_an_unsettled_article_resolves_no_provision(monkeypatch):
    """While which fund governs is an open question, every answer about "your
    provision" is potentially the wrong fund's. The same rule that starves the
    figure starves this."""
    seen = _fake_lookup(monkeypatch, {(11, 1): [_ANNUITY]})
    assert await loop_mod._engine_provisions({
        **_MEMBER,
        "coverage": {"article": "11", "basis": "record", "record_article": "11",
                     "conflict": True, "why": "elsewhere"},
    }) == []
    assert seen == []


async def test_a_premise_resolves_the_supposed_articles_provision(monkeypatch):
    """Reading Article 12's own provision under an Article 12 premise is the
    law the question asked about, not a borrowed one — and it is text, not the
    member's data, so nothing is starved by being under a supposition."""
    supposed = {**_ANNUITY, "section": "12-133", "formula_type": "retirement_annuity"}
    _fake_lookup(monkeypatch, {(12, 1): [supposed]})
    out = await loop_mod._engine_provisions({
        **_MEMBER,
        "coverage": {"article": "12", "basis": "hypothetical", "record_article": "11"},
        "hypothetical_frame": {"premise": "a park employee", "article": "12"},
    })
    assert [p["section"] for p in out] == ["12-133"]


async def test_no_record_and_no_article_asks_nothing(monkeypatch):
    seen = _fake_lookup(monkeypatch, {})
    assert await loop_mod._engine_provisions({"question": "how are pensions worked out?"}) == []
    assert seen == []


# ── Both halves, because G30 settled that one is not enough ────────
#
# Reading the provision makes it citable; naming it in `compose` is what gets
# it into the answer. G30 failed with Section 11-134 sitting in its own
# retrieved sections because the pin lived in the middle of the tool history.


def _turn(*calls, content=""):
    from pension_chat.agent.llm import ToolCall, ToolTurn
    parsed = tuple(ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls))
    raw: dict = {"role": "assistant"}
    if parsed:
        raw["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
            for c in parsed
        ]
    if content:
        raw["content"] = content
    return ToolTurn(calls=parsed, content=content, raw_message=raw)


def _script(monkeypatch, turns):
    it = iter(turns)

    async def fake_call(**kwargs):
        try:
            return next(it)
        except StopIteration:
            return _turn(content="done")

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)


def _tools(monkeypatch, update=None):
    ran: list[tuple[str, dict]] = []

    async def fake_run(name, arguments, state):
        ran.append((name, arguments))
        return "a section", (update or {})

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    return ran


async def test_the_engines_provision_is_read_during_the_investigation(monkeypatch):
    _fake_lookup(monkeypatch, {(11, 1): [_ANNUITY]})
    _script(monkeypatch, [_turn(("outline", {"article": "11"})), _turn(content="Found it.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-130"}]})

    await loop_mod.investigate(dict(_MEMBER))
    fetched = [a.get("section") for n, a in ran if n == "get_section"]
    assert "11-134" in fetched, fetched


async def test_a_provision_already_read_is_not_fetched_twice(monkeypatch):
    _fake_lookup(monkeypatch, {(11, 1): [_ANNUITY]})
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Done.")])
    ran = _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134"}]})

    await loop_mod.investigate(dict(_MEMBER))
    assert [a.get("section") for n, a in ran if n == "get_section"] == []


async def test_compose_names_it_with_the_alternatives_it_was_chosen_over():
    """G17 turn 2 -- "why did you choose that one over the alternatives?" -- is
    answered by `alternatives_considered`, which the derivation recorded and
    every reuse reads back. The loop had it in the registry and never looked."""
    out = await loop_mod.compose(
        {
            "question": "Which retirement provision applies to me?",
            "loop_messages": [],
            "retrieved_sections": [{"section": "11-134", "article": "11"}],
            "engine_provisions": [_ANNUITY],
        }
    )
    tail = chr(10).join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "11-134" in tail
    assert "11-130" in tail, "the alternatives it was chosen over"
    assert "statutory_election" in tail or "election" in tail


async def test_compose_names_no_engine_provision_when_a_figure_was_worked():
    """`_worked_from` is the sharper statement, and two blocks naming
    provisions is how they come to disagree."""
    out = await loop_mod.compose(
        {
            "question": "what would I get?",
            "loop_messages": [],
            "retrieved_sections": [{"section": "11-134", "article": "11"}],
            "engine_provisions": [_ANNUITY],
            "calculation_result": {
                "retirement_annuity": {"section": "11-134", "base": {"ok": True}}
            },
            "recipe_provenance": [{"formula_type": "retirement_annuity", "section": "11-134"}],
        }
    )
    tail = chr(10).join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "figure you have was worked from Section 11-134" in tail
    assert "would work a retirement annuity" not in tail
