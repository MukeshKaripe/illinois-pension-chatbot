"""Every remote call a turn makes is timed, and costs nothing when no run is watching."""
from __future__ import annotations

import asyncio

import pytest

from pension_chat.agent import instrument


@pytest.mark.asyncio
async def test_timed_records_nothing_without_an_instrument() -> None:
    assert instrument.current() is None
    async with instrument.timed("search", "q"):
        pass                                # must not raise


@pytest.mark.asyncio
async def test_timed_records_a_call() -> None:
    inst = instrument.begin()
    async with instrument.timed("model", "investigate"):
        await asyncio.sleep(0.01)
    [call] = inst.calls
    assert call.kind == "model" and call.label == "investigate"
    assert call.end - call.start >= 0.009


@pytest.mark.asyncio
async def test_a_failed_call_is_still_recorded() -> None:
    inst = instrument.begin()
    with pytest.raises(RuntimeError):
        async with instrument.timed("search", "q"):
            raise RuntimeError("boom")
    assert [c.kind for c in inst.calls] == ["search"]


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_their_own_instruments() -> None:
    async def one(kind: str) -> list[str]:
        inst = instrument.begin()
        async with instrument.timed(kind, kind):
            await asyncio.sleep(0.01)
        return [c.kind for c in inst.calls]

    a, b = await asyncio.gather(one("model"), one("search"))
    assert a == ["model"] and b == ["search"]


def test_notes_and_rendered() -> None:
    inst = instrument.begin()
    instrument.note("semantic_fallback", "tier 2")
    instrument.rendered("1-160", ["(a)", "(c)"])
    instrument.rendered("1-160", ["(c)", "(d)"])
    assert inst.notes[0][1:] == ("semantic_fallback", "tier 2")
    assert inst.rendered["1-160"] == ["(a)", "(c)", "(d)"]


@pytest.mark.asyncio
async def test_postgres_statements_are_timed_through_engine_events() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    instrument.install_sqlalchemy(engine)
    inst = instrument.begin()
    async with engine.connect() as conn:
        await conn.execute(text("select 1"))
    await engine.dispose()
    assert "postgres" in [c.kind for c in inst.calls]


@pytest.mark.asyncio
async def test_the_semantic_fallback_is_noted(monkeypatch) -> None:
    from pension_chat.agent import retrieval

    calls = {"n": 0}

    class _Results:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _Client:
        async def search(self, **kwargs):
            calls["n"] += 1
            if "query_type" in kwargs:
                raise RuntimeError("semantic quota exhausted")
            return _Results()

    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: _Client())
    inst = instrument.begin()
    await retrieval._search_one("tier 2", None, None, 8)
    assert [n[1] for n in inst.notes] == ["semantic_fallback"]
    assert [c.kind for c in inst.calls] == ["search", "search"]
