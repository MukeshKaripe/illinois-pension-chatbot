"""Writes after `done`: run in order per conversation, waited for by the next reader."""
from __future__ import annotations

import asyncio

from pension_chat.agent import pending


async def test_writes_run_in_order_and_settle_waits_for_them() -> None:
    done: list[str] = []

    async def write(name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        done.append(name)

    pending.defer("s1", write("first", 0.05))
    pending.defer("s1", write("second", 0))
    await pending.settle("s1")
    assert done == ["first", "second"]


async def test_a_failed_write_neither_raises_nor_blocks_the_next() -> None:
    done: list[str] = []

    async def fail() -> None:
        raise RuntimeError("database gone")

    async def write() -> None:
        done.append("after")

    pending.defer("s2", fail())
    pending.defer("s2", write())
    await pending.settle("s2")
    assert done == ["after"]


async def test_other_conversations_do_not_wait() -> None:
    gate = asyncio.Event()

    async def blocked() -> None:
        await gate.wait()

    pending.defer("busy", blocked())
    await asyncio.wait_for(pending.settle("idle"), timeout=0.5)
    gate.set()
    await pending.settle("busy")
