"""CHECKPOINTER=memory: conversation state in process, for a demo far from its database."""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from pension_chat.agent import graph
from pension_chat.agent.checkpointer import SqlAlchemyCheckpointSaver
from pension_chat.config import get_settings


def test_postgres_is_the_default() -> None:
    from pension_chat.config import Settings

    assert Settings.model_fields["checkpointer"].default == "postgres"
    settings = get_settings().model_copy(update={"checkpointer": "postgres"})
    assert isinstance(graph._checkpointer(settings), SqlAlchemyCheckpointSaver)


def test_memory_is_chosen_by_setting() -> None:
    settings = get_settings().model_copy(update={"checkpointer": "memory"})
    assert isinstance(graph._checkpointer(settings), InMemorySaver)


async def test_has_state_asks_the_graphs_own_checkpointer() -> None:
    compiled = graph.build_loop_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "t-1"}}
    with graph.use_graph(compiled):
        assert await graph._has_state(config) is False
        await compiled.aupdate_state(config, {"question": "q"}, as_node="compose")
        assert await graph._has_state(config) is True
