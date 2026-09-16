"""Every section the prefetched text leans on is read before the model decides.

Capping the loop without this is faster and cites a third less -- fast because
it answers with less, which is not a win. With the closure it is 2.1x faster
than the loop was AND cites more than the loop did, because these are sections
the model never got round to reading.

The same fetch already ran after the loop. Running it first is what lets the
model settle the question in one call instead of browsing to find them.
"""

from __future__ import annotations

from test_loop import _script, _turn
from test_prefetch import _script_snapshot

from pension_chat.agent import loop as loop_mod


def _tools_with_references(monkeypatch):
    """First call returns a section that cites another; later calls return it."""
    ran: list[tuple[str, dict]] = []

    async def fake_run(name, arguments, state):
        ran.append((name, arguments))
        if name == "search_statute":
            return "found", {
                "retrieved_sections": [
                    {
                        "section": "11-134",
                        "text": "computed as in Section 11-216",
                        "referenced_sections": ["11-216"],
                    }
                ]
            }
        return "the referenced text", {
            "retrieved_sections": [{"section": "11-216", "text": "service"}]
        }

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    return ran


async def test_a_cross_reference_is_read_before_the_model_decides(monkeypatch):
    """Not "it was read" -- the post-loop completion already made that true.
    The claim is that the model's *first* call already held it, which is what
    lets the turn settle in one round instead of browsing to find it."""
    seen: list = []
    _script_snapshot(monkeypatch, [_turn(content="Here.")], seen)
    ran = _tools_with_references(monkeypatch)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert ("get_section", {"section": "11-216"}) in ran
    assert {s["section"] for s in out["retrieved_sections"]} == {"11-134", "11-216"}
    assert any(
        "the referenced text" in str(message.get("content") or "")
        for message in seen[0]
    ), "the closure must be in front of the model before it decides"


async def test_the_closed_evidence_is_named_as_closed(monkeypatch):
    seen: list = []
    _script_snapshot(monkeypatch, [_turn(content="Here.")], seen)
    _tools_with_references(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    first = [str(m.get("content") or "") for m in seen[0]]
    assert any("evidence is closed" in text for text in first)


async def test_the_closure_is_bounded(monkeypatch):
    """A section citing forty others must not cost forty round trips."""
    ran: list[tuple[str, dict]] = []

    async def fake_run(name, arguments, state):
        ran.append((name, arguments))
        if name == "search_statute":
            return "found", {
                "retrieved_sections": [
                    {
                        "section": "11-134",
                        "text": "t",
                        "referenced_sections": [f"11-{i}" for i in range(200, 240)],
                    }
                ]
            }
        return "text", {}

    monkeypatch.setattr(loop_mod, "run_tool", fake_run)
    _script(monkeypatch, [_turn(content="Here.")])

    await loop_mod.investigate({"question": "q", "messages": []})

    # Two bounded phases, not one: the prefetch closes what it can before the
    # model decides, and the post-loop completion picks up whatever the model's
    # own round newly leaned on. Both are capped; neither is unbounded.
    fetched = [name for name, _ in ran if name == "get_section"]
    assert len(fetched) <= loop_mod.MAX_PREFETCH_CLOSURE + loop_mod.MAX_COMPLETION_FETCHES
