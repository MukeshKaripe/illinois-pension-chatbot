"""Writes a turn leaves behind, run after the member already has their answer.

From this laptop each Postgres round trip is ~250 ms, and the end of a turn
made a dozen of them -- the checkpoint's own memory of the answer, the answer
memory, the transcript row, the people's order -- all before `done`, which is
what the client waits for to show the cards and free the composer. None of them
changes what the member sees on this turn; each changes what the *next* turn
reads. So they run in the background, and anything that reads this
conversation's state first waits for its pending writes: the order of turns is
kept exactly, and only the waiting moves to where it is needed.

In-process: correct for one worker. Several workers serving one conversation
would need the wait to live in the database instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

logger = logging.getLogger("pension_chat.agent.pending")

_pending: dict[str, asyncio.Task[None]] = {}


def defer(session_id: str, work: Awaitable[None]) -> None:
    """Run `work` after this conversation's earlier pending writes, in the background."""
    before = _pending.get(session_id)

    async def run() -> None:
        if before is not None:
            await asyncio.gather(before, return_exceptions=True)
        try:
            await work
        except Exception:
            logger.exception("A deferred write failed for session %s", session_id)

    task = asyncio.create_task(run())
    _pending[session_id] = task

    def forget(done: asyncio.Task[None]) -> None:
        if _pending.get(session_id) is done:
            del _pending[session_id]

    task.add_done_callback(forget)


async def settle(session_id: str) -> None:
    """Wait for this conversation's pending writes; never raises."""
    task = _pending.get(session_id)
    if task is not None:
        await asyncio.gather(asyncio.shield(task), return_exceptions=True)
