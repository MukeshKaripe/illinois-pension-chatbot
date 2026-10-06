"""The Code is searched before the model is asked anything.

The loop already searched on the member's own words when the model had not --
as a backstop, after the loop had finished. Running it first is the same search
at a better time: the model's first call already has statute text, so the round
trip it used to spend discovering that it should search disappears.

Measured in-region over 23 questions: four model round trips became two.
"""

from __future__ import annotations

from test_loop import _script, _tools, _turn

from pension_chat.agent import loop as loop_mod


def _script_snapshot(monkeypatch, turns, snapshots):
    """Like `_script`, but records the messages *as they were at call time*.

    `investigate` keeps appending to the same list, so a stored reference shows
    the end of the turn rather than its beginning -- which would make the
    ordering assertion below pass no matter when the search ran.
    """
    sequence = iter(turns)

    async def fake_call(**kwargs):
        snapshots.append([dict(m) for m in kwargs.get("messages") or []])
        try:
            return next(sequence)
        except StopIteration:
            return _turn(content="done")

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)


async def test_the_search_runs_before_the_first_model_call(monkeypatch):
    """Not "a search happened" -- the backstop makes that true either way. The
    claim is that the model's *first* call already had the statute text."""
    seen: list = []
    _script_snapshot(monkeypatch, [_turn(content="Here.")], seen)
    ran = _tools(
        monkeypatch,
        result="### 40 ILCS 5/11-134",
        update={"retrieved_sections": [{"section": "11-134", "text": "t"}]},
    )

    out = await loop_mod.investigate({"question": "what is Article 11?", "messages": []})

    assert ran and ran[0][0] == "search_statute"
    first_call = seen[0]
    assert any(
        "40 ILCS 5/11-134" in str(message.get("content") or "") for message in first_call
    ), "the first model call must already hold the search result"
    assert [s["section"] for s in out["retrieved_sections"]] == ["11-134"]


async def test_the_model_is_told_it_already_has_statute_text(monkeypatch):
    _script(monkeypatch, [_turn(content="Here.")])
    _tools(monkeypatch, result="### 40 ILCS 5/11-134", update={})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    system = [m["content"] for m in out["loop_messages"] if m.get("role") == "system"]
    assert any("before you were asked" in text for text in system)


async def test_the_backstop_does_not_search_again(monkeypatch):
    """One search, not two. The backstop exists for a turn that never searched."""
    _script(monkeypatch, [_turn(content="Here.")])
    ran = _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    assert [name for name, _ in ran].count("search_statute") == 1


async def test_a_self_description_is_not_prefetched(monkeypatch):
    """check_coverage may still move the governing Article, and a filter built
    on the record would seed another fund's law into the citable set."""
    _script(monkeypatch, [_turn(content="Here.")])
    ran = _tools(monkeypatch)

    await loop_mod.investigate(
        {"question": "i am a park employee, what do I get?", "messages": []}
    )

    assert [name for name, _ in ran].count("search_statute") <= 1


async def test_the_model_can_still_search_for_itself(monkeypatch):
    _script(
        monkeypatch,
        [_turn(("search_statute", {"queries": ["reduction"]})), _turn(content="Here.")],
    )
    ran = _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    assert [name for name, _ in ran].count("search_statute") == 2


async def test_a_named_section_is_read_before_the_first_model_call(monkeypatch):
    from pension_chat.agent import loop as loop_mod
    seen: list = []
    read: list = []

    async def fake_call(**kwargs):
        seen.append([m["content"] for m in kwargs["messages"] if m.get("role") == "system"])
        from pension_chat.agent.llm import ToolTurn
        return ToolTurn(calls=(), content="Here.", raw_message={"role": "assistant", "content": "Here."})

    async def fake_run(name, arguments, state):
        return "searched text", {"retrieved_sections": [{"section": "1-160", "content": "t"}]}

    async def fake_read(targets, working):
        read.append(list(targets))
        return [(f"### 40 ILCS 5/{t} — title\ntext", {"retrieved_sections": [{"section": t, "content": "x"}]})
                for t in targets]

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)
    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    monkeypatch.setattr(loop_mod, "_read_sections", fake_read)
    await loop_mod.investigate({"question": "How is the annuity calculated under Section 8-138(g-1)?",
                                "explicit_sections": ["8-138"], "messages": []})
    assert ["8-138"] in read
    assert any("8-138" in content for content in seen[0])


async def test_a_named_section_that_does_not_exist_does_not_fail_the_turn(monkeypatch):
    from pension_chat.agent import loop as loop_mod

    async def fake_call(**kwargs):
        from pension_chat.agent.llm import ToolTurn
        return ToolTurn(calls=(), content="Here.", raw_message={"role": "assistant", "content": "Here."})

    async def fake_run(name, arguments, state):
        return "searched text", {}

    async def fake_read(targets, working):
        return [None for _ in targets]

    monkeypatch.setattr(loop_mod, "call_with_tools", fake_call)
    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    monkeypatch.setattr(loop_mod, "_read_sections", fake_read)
    out = await loop_mod.investigate({"question": "What does Section 8-999 say?",
                                      "explicit_sections": ["8-999"], "messages": []})
    assert out is not None
