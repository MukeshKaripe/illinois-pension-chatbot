"""The investigation: search, read what came back, decide what to do next.

Offline: `call_with_tools` is scripted and the tools are faked, because what is
under test is the control flow -- when the loop stops, what it keeps, and what
it does when a budget runs out -- not whether the model is clever.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod
from pension_chat.agent.llm import ToolCall, ToolTurn


def _turn(*calls: tuple[str, dict], content: str = "") -> ToolTurn:
    parsed = tuple(
        ToolCall(id=f"c{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls)
    )
    raw: dict = {"role": "assistant"}
    if parsed:
        raw["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
            for c in parsed
        ]
    if content:
        raw["content"] = content
    return ToolTurn(calls=parsed, content=content, raw_message=raw)


def _script(monkeypatch, turns: list[ToolTurn], calls_seen: list | None = None):
    sequence = iter(turns)

    async def fake_call(**kwargs):
        if calls_seen is not None:
            calls_seen.append(kwargs)
        try:
            return next(sequence)
        except StopIteration:
            return _turn(content="done")

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)


def _tools(monkeypatch, result: str = "a section", update: dict | None = None):
    ran: list[tuple[str, dict]] = []

    async def fake_run(name, arguments, state):
        ran.append((name, arguments))
        return result, (update or {})

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    return ran


async def test_a_loop_that_searches_then_answers(monkeypatch):
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])
    ran = _tools(monkeypatch)

    out = await loop_mod.investigate({"question": "what is Article 11?", "messages": []})

    assert [name for name, _ in ran] == ["search_statute"]
    assert any(m.get("role") == "tool" for m in out["loop_messages"])


async def test_tool_results_accumulate_into_state(monkeypatch):
    """The key the pipeline writes, so nothing downstream has to change."""
    _script(monkeypatch, [_turn(("search_statute", {"queries": ["x"]})), _turn(content="Here.")])
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert [s["section"] for s in out["retrieved_sections"]] == ["11-134"]


async def test_sections_accumulate_across_iterations_without_duplicating(monkeypatch):
    _script(
        monkeypatch,
        [
            _turn(("search_statute", {"queries": ["a"]})),
            _turn(("search_statute", {"queries": ["b"]})),
            _turn(content="Here."),
        ],
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert [s["section"] for s in out["retrieved_sections"]] == ["11-134"], "same section twice is once"


async def test_the_total_budget_ends_the_turn(monkeypatch):
    """A model that never stops calling must still produce an answer."""
    _script(monkeypatch, [_turn(("get_section", {"section": "11-134"})) for _ in range(60)])
    ran = _tools(monkeypatch)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert len(ran) <= loop_mod.BUDGETS["total"]
    assert out["loop_exhausted"] is True


async def test_an_exhausted_sub_budget_removes_only_that_tool(monkeypatch):
    """The rule most likely to be implemented as "end the turn".

    A loop out of searches can still read a section it already knows about,
    follow a cross-reference, or answer. Ending the turn throws all of that
    away over one spent resource.
    """
    seen: list[dict] = []
    _script(
        monkeypatch,
        [_turn(("search_statute", {"queries": ["x"]})) for _ in range(loop_mod.BUDGETS["search_statute"] + 1)]
        + [_turn(content="Here.")],
        calls_seen=seen,
    )
    _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    offered = [{t["function"]["name"] for t in kwargs["tools"]} for kwargs in seen]
    assert "search_statute" not in offered[-1], "the spent tool is withdrawn"
    assert "get_section" in offered[-1], "the others stay"


async def test_a_spent_tool_is_explained_not_silently_dropped(monkeypatch):
    seen: list[dict] = []
    _script(
        monkeypatch,
        [_turn(("search_statute", {"queries": ["x"]})) for _ in range(loop_mod.BUDGETS["search_statute"] + 1)]
        + [_turn(content="Here.")],
        calls_seen=seen,
    )
    _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    last = seen[-1]["messages"]
    assert any("search_statute" in str(m.get("content", "")) and "no more" in str(m.get("content", "")).lower()
               for m in last), "the model is told why the tool vanished"


async def test_unread_cross_references_are_reported_on_exhaustion(monkeypatch):
    """Exhaustion is not a licence to answer.

    A figure cannot be fabricated -- compute_benefit has no number without
    inputs -- but prose can still conclude on law that was never read. The gap
    is computed, not judged: what the evidence cites, minus what was read.
    """
    _script(monkeypatch, [_turn(("get_section", {"section": "11-134"})) for _ in range(60)])
    _tools(
        monkeypatch,
        update={
            "retrieved_sections": [
                {"section": "11-134", "text": "t", "referenced_sections": ["11-216", "11-134"]}
            ]
        },
    )

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert out["unread_references"] == ["11-216"], "cited but never read; itself does not count"


async def test_a_malformed_tool_call_does_not_end_the_turn(monkeypatch):
    """`call_with_tools` gives {} for broken JSON. The tool reports what it needed."""
    _script(monkeypatch, [_turn(("search_statute", {})), _turn(content="Here.")])
    ran = _tools(monkeypatch, result="search_statute needs at least one query.")

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert ran == [("search_statute", {})]
    assert out["loop_exhausted"] is False
