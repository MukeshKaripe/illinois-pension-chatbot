"""An eval run's conversations live in memory, never in the client's Postgres."""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from pension_chat.agent import graph as g


def test_override_is_used_and_reset() -> None:
    mine = g.build_loop_graph(InMemorySaver())
    assert g.active_graph() is g.loop_graph
    with g.use_graph(mine):
        assert g.active_graph() is mine
    assert g.active_graph() is g.loop_graph
