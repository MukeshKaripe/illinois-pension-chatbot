"""A table exists before the first token, so it is sent before the first token.

Riding on `done` puts every table behind generation *and* verification: the
member waits the length of the whole answer to see a figure that was ready
before it started.
"""

from __future__ import annotations

from pension_chat.sse import sse_view


async def _events(views, monkeypatch):
    """The event sequence `run_agent_stream` produces, with the graph and the
    answer call both stubbed out."""
    from pension_chat.agent import graph as graph_mod

    state = {
        "views": views,
        "answer_messages": [{"role": "system", "content": "..."}],
        "retrieved_sections": [],
        "citations": [],
        "answer_blocks": {},
    }

    class _Graph:
        async def astream(self, *args, **kwargs):
            yield "values", state

    async def fake_stream(**kwargs):
        yield "Here is the answer."

    def fake_record_turn(*args, **kwargs):
        return {}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(graph_mod, "active_graph", lambda: _Graph())
    monkeypatch.setattr(graph_mod, "stream_llm", fake_stream)
    monkeypatch.setattr(graph_mod, "_defer_record", fake_record_turn)
    monkeypatch.setattr(graph_mod, "thread_has_state", noop)

    return [
        event
        async for event in graph_mod.run_agent_stream("q", "s1", None, [])
    ]


async def test_a_view_is_emitted_before_any_token(monkeypatch):
    events = await _events({"WORKED_STEPS": "| Step |"}, monkeypatch)
    kinds = [e["type"] for e in events]
    assert "view" in kinds
    assert kinds.index("view") < kinds.index("token")


async def test_each_view_is_its_own_event(monkeypatch):
    events = await _events(
        {"WORKED_STEPS": "| Step |", "COMPARISON": "| If you are |"}, monkeypatch
    )
    slots = [e["slot"] for e in events if e["type"] == "view"]
    assert slots == ["WORKED_STEPS", "COMPARISON"]


async def test_a_turn_with_no_views_emits_none(monkeypatch):
    events = await _events({}, monkeypatch)
    assert not [e for e in events if e["type"] == "view"]


def test_the_wire_format_carries_the_slot_and_the_markdown():
    frame = sse_view("WORKED_STEPS", "| Step | Amount |")
    assert frame.startswith("event: view\n")
    assert '"slot": "WORKED_STEPS"' in frame
    assert "Step | Amount" in frame
