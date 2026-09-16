"""What it is doing, while it is doing it.

The old labels came from LangGraph's `updates` stream, which fires when a node
*finishes* -- so a turn that spent nine seconds investigating displayed "reading
the Pension Code" at the moment it stopped reading the Pension Code.

Every step here is rendered from state the turn already holds. A second model
call to describe the first is what §9 forbids, and it would make a slow turn
slower.
"""

from __future__ import annotations

from test_loop import _script, _tools, _turn

from pension_chat.agent import loop as loop_mod


def _capture(monkeypatch) -> list[str]:
    seen: list[str] = []

    def writer():
        def write(payload):
            seen.append(str(payload.get("progress") or ""))

        return write

    monkeypatch.setattr(loop_mod, "get_stream_writer", writer)
    return seen


async def test_a_search_reports_what_it_searched_for(monkeypatch):
    seen = _capture(monkeypatch)
    _script(monkeypatch, [_turn(content="Here.")])
    _tools(monkeypatch)

    await loop_mod.investigate({"question": "when can i retire", "messages": []})

    assert any("when can i retire" in text for text in seen)


async def test_reading_a_section_reports_which_one(monkeypatch):
    seen = _capture(monkeypatch)
    _script(
        monkeypatch,
        [_turn(("get_section", {"section": "11-134"})), _turn(content="Here.")],
    )
    _tools(monkeypatch)

    await loop_mod.investigate({"question": "q", "messages": []})

    assert any("11-134" in text for text in seen)


async def test_a_missing_writer_is_not_an_error(monkeypatch):
    """`get_stream_writer` raises outside a graph run, and `investigate` is
    called directly by every loop test and by the eval harness. Progress is a
    courtesy; it must never be the reason a turn fails."""

    def raises():
        raise RuntimeError("not inside a graph")

    monkeypatch.setattr(loop_mod, "get_stream_writer", raises)
    _script(monkeypatch, [_turn(content="Here.")])
    _tools(monkeypatch)

    out = await loop_mod.investigate({"question": "q", "messages": []})

    assert out["loop_messages"]


async def test_progress_costs_no_model_call(monkeypatch):
    """The whole point. Describing the work must not be more work.

    Counted against the same turn run with no writer at all, rather than
    against a fixed number: what the loop spends on its own is this test's
    control, not its subject.
    """
    silent: list = []

    def raises():
        raise RuntimeError("no writer")

    monkeypatch.setattr(loop_mod, "get_stream_writer", raises)
    _script(monkeypatch, [_turn(content="Here.")], calls_seen=silent)
    _tools(monkeypatch)
    await loop_mod.investigate({"question": "q", "messages": []})

    reported: list = []
    seen = _capture(monkeypatch)
    _script(monkeypatch, [_turn(content="Here.")], calls_seen=reported)
    _tools(monkeypatch)
    await loop_mod.investigate({"question": "q", "messages": []})

    assert seen, "something was reported"
    assert len(reported) == len(silent), "and it cost no extra model call"


# ── On the wire ───────────────────────────────────────────────────


def test_the_wire_format_carries_the_elapsed_time():
    """Without it the client cannot tell a fast turn from a slow one, and would
    have to show the machinery on every turn or on none."""
    from pension_chat.sse import sse_progress

    frame = sse_progress("reading Section 11-134", 5.4)
    assert frame.startswith("event: progress\n")
    assert '"text": "reading Section 11-134"' in frame
    assert '"at": 5.4' in frame


def test_a_step_is_described_in_the_members_terms():
    """"running related_sections" describes the machinery. The member is owed
    what is being found out, not which function is running."""
    assert loop_mod._step_for("related_sections", {"section": "11-134"}) == (
        "checking what else bears on Section 11-134"
    )
    assert loop_mod._step_for("check_coverage", {}) == "working out which fund covers you"


def test_a_tool_with_nothing_worth_saying_reports_nothing():
    assert loop_mod._step_for("something_new", {}) == ""
