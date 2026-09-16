"""One tool round, once the evidence was handed over complete.

The cap is what turns prefetch-plus-closure into two model round trips instead
of four. It is conditional on the prefetch having run, and that condition is the
whole difference between the measured win and three questions collapsing to one
citation: the turns that collapsed were exactly the turns where the prefetch is
skipped, which were left with no evidence AND no iterations.
"""

from __future__ import annotations

from test_loop import _script, _tools, _turn

from pension_chat.agent import loop as loop_mod


async def test_a_prefetched_turn_takes_one_tool_round(monkeypatch):
    seen: list = []
    _script(
        monkeypatch,
        [
            _turn(("get_section", {"section": "11-134"})),
            _turn(("get_section", {"section": "11-216"})),
            _turn(("get_section", {"section": "11-218"})),
            _turn(content="Here."),
        ],
        calls_seen=seen,
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    await loop_mod.investigate({"question": "q", "messages": []})

    assert len(seen) == 1, "one model call after the prefetch, then compose"


async def test_a_turn_without_prefetch_is_not_capped(monkeypatch):
    """The self-description case. No prefetch ran, so the loop must be free to
    investigate -- otherwise it has neither evidence nor the chance to get any."""
    seen: list = []
    _script(
        monkeypatch,
        [
            _turn(("check_coverage", {})),
            _turn(("search_statute", {"queries": ["park"]})),
            _turn(("get_section", {"section": "12-118"})),
            _turn(content="Here."),
        ],
        calls_seen=seen,
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "12-118", "text": "t"}]})

    await loop_mod.investigate(
        {"question": "i am a park employee, how is my pension worked out?", "messages": []}
    )

    assert len(seen) >= 3, "an unprefetched turn keeps its iterations"


async def test_the_capped_turn_still_keeps_what_the_round_found(monkeypatch):
    _script(
        monkeypatch,
        [_turn(("get_section", {"section": "11-216"})), _turn(content="Here.")],
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-216", "text": "t"}]})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert "11-216" in {s["section"] for s in out["retrieved_sections"]}


async def test_a_prefetched_turn_that_answers_at_once_uses_no_round(monkeypatch):
    """The prefetch has to have found something, or the model answering at once
    is answering from memory and the pushback guard is right to stop it."""
    seen: list = []
    _script(monkeypatch, [_turn(content="Here.")], calls_seen=seen)
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    await loop_mod.investigate({"question": "q", "messages": []})

    assert len(seen) == 1


async def test_a_capped_turn_is_not_reported_as_exhausted(monkeypatch):
    """Stopping because the evidence arrived complete is not running out.
    `compose` tells the member what it could not reach when a turn is
    exhausted, and that would be a false account of this one."""
    _script(
        monkeypatch,
        [_turn(("get_section", {"section": "11-134"})), _turn(content="Here.")],
    )
    _tools(monkeypatch, update={"retrieved_sections": [{"section": "11-134", "text": "t"}]})

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert out["loop_exhausted"] is False
