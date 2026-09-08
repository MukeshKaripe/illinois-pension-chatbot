"""Durable conversation state for the agent graph.

Every turn used to start from nothing. ``initial_state()`` reset the whole
blackboard and the only thing carried forward was the text of the last few
messages, so facts, recipes and scenarios were re-derived from prose each time.
That is why "Put all four results in one comparison table" could not be
answered: the four results existed only as sentences in the transcript, and a
figure the model reads back out of its own earlier answer traces to nothing.

This is the storage half of the fix — a LangGraph checkpointer over the
project's own SQLAlchemy session, so a thread resumes from what the previous
turn established.

**Why not the library's Postgres saver.** It would have meant a third Postgres
driver on its own pool, and its tables would have sat outside the row-level
security every other user-scoped table has. Checkpoints carry member facts —
birth date, salary history, computed figures — so they belong under the same
policy as the messages. Going through ``db/session.py`` keeps that true. The
part that would have been genuinely hard, serialising ``Decimal``, ``date`` and
LangChain messages, is not hand-rolled here: LangGraph's own ``JsonPlusSerializer``
does it.

**On RLS and system callers.** The chat API knows who owns a thread and says so,
and those turns are scoped by the same policy as ``session_messages``. A system
caller — the evaluation harness — runs threads that are not session rows at all,
and for those the delegating policy would match nothing and silently discard
every write. Such callers fall through to the unrestricted session, the same
explicit escape hatch ``api/admin.py`` uses. The distinction is the presence of a
thread owner in the config, so it is visible at the call site rather than
inferred here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from pension_chat.db.models import AgentCheckpoint, AgentCheckpointWrite
from pension_chat.db.session import get_admin_session, get_db_session

logger = logging.getLogger("pension_chat.agent.checkpointer")


def thread_config(
    thread_id: str,
    *,
    employee_id: str | None = None,
    session_token: str | None = None,
) -> RunnableConfig:
    """Build the config that addresses one conversation.

    Passing an owner opts the thread's checkpoints into row-level security;
    omitting it marks the caller as a system one. See the module docstring.
    """
    configurable: dict[str, Any] = {"thread_id": str(thread_id)}
    if employee_id:
        configurable["employee_id"] = str(employee_id)
    if session_token:
        configurable["session_token"] = session_token
    return {"configurable": configurable}


async def thread_has_state(config: RunnableConfig) -> bool:
    """Whether this thread has been checkpointed before.

    Used to decide whether a caller's message history should seed the thread or
    be ignored because the thread already holds it. One indexed lookup, which
    replaces the larger transcript read it makes unnecessary.
    """
    thread_id, _ = SqlAlchemyCheckpointSaver._address(config)
    saver = SqlAlchemyCheckpointSaver()
    async with saver._db(config) as db:
        found = await db.execute(
            select(AgentCheckpoint.checkpoint_id)
            .where(AgentCheckpoint.thread_id == thread_id)
            .limit(1)
        )
        return found.scalars().first() is not None


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver):
    """LangGraph checkpointer backed by the application's own database session.

    Async only. The graph is only ever driven from async code, and offering
    working sync methods would invite a caller to reach for them from inside the
    event loop.
    """

    def __init__(self, serde: Any | None = None) -> None:
        super().__init__(serde=serde or JsonPlusSerializer())

    # ── Session scoping ───────────────────────────────────────────

    @asynccontextmanager
    async def _db(self, config: RunnableConfig) -> AsyncIterator[Any]:
        configurable = (config or {}).get("configurable") or {}
        employee_id = configurable.get("employee_id")
        session_token = configurable.get("session_token")

        if employee_id or session_token:
            async with get_db_session(
                employee_id=employee_id, session_token=session_token
            ) as session:
                yield session
            return

        # No owner named: a system caller, whose thread is not a session row at
        # all -- the evaluation harness, or a test. The delegating policy would
        # match nothing for such a thread and silently discard every write, so
        # this takes the declared escape instead of failing quietly.
        #
        # Both paths are transactional now. `set_config(..., local)` is scoped
        # to a transaction, and without one the identity would outlive the
        # statement and reach whoever the pool hands the connection to next.
        async with get_admin_session() as session:
            yield session

    @staticmethod
    def _address(config: RunnableConfig) -> tuple[str, str]:
        configurable = config["configurable"]
        return str(configurable["thread_id"]), configurable.get("checkpoint_ns") or ""

    # ── Reads ─────────────────────────────────────────────────────

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, checkpoint_ns = self._address(config)
        wanted = get_checkpoint_id(config)

        query = select(AgentCheckpoint).where(
            AgentCheckpoint.thread_id == thread_id,
            AgentCheckpoint.checkpoint_ns == checkpoint_ns,
        )
        query = (
            query.where(AgentCheckpoint.checkpoint_id == wanted)
            if wanted
            # Checkpoint ids are monotonic, so the highest is the newest.
            # Newest checkpoint, chosen by subquery rather than LIMIT: the join
            # below multiplies rows by pending writes, so limiting the joined
            # result would drop all but the first of them.
            else query.where(
                AgentCheckpoint.checkpoint_id
                == select(func.max(AgentCheckpoint.checkpoint_id))
                .where(
                    AgentCheckpoint.thread_id == thread_id,
                    AgentCheckpoint.checkpoint_ns == checkpoint_ns,
                )
                .scalar_subquery()
            )
        )

        # Checkpoint and its pending writes in one round trip. They used to be
        # two statements, which at ~232ms each is most of a second on the
        # critical path for a join the database can do itself.
        joined = (
            query.add_columns(AgentCheckpointWrite)
            .outerjoin(
                AgentCheckpointWrite,
                (AgentCheckpointWrite.thread_id == AgentCheckpoint.thread_id)
                & (AgentCheckpointWrite.checkpoint_ns == AgentCheckpoint.checkpoint_ns)
                & (
                    AgentCheckpointWrite.checkpoint_id
                    == AgentCheckpoint.checkpoint_id
                ),
            )
            .order_by(AgentCheckpointWrite.task_id, AgentCheckpointWrite.idx)
        )

        async with self._db(config) as db:
            rows = (await db.execute(joined)).all()

        if not rows:
            return None

        checkpoint = rows[0][0]
        writes = [
            (
                write.task_id,
                write.channel,
                self.serde.loads_typed((write.type or "", write.blob)),
            )
            for _, write in rows
            if write is not None
        ]
        return self._to_tuple(checkpoint, writes)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 — the interface's name
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        query = select(AgentCheckpoint).order_by(AgentCheckpoint.checkpoint_id.desc())

        if config:
            thread_id, checkpoint_ns = self._address(config)
            query = query.where(
                AgentCheckpoint.thread_id == thread_id,
                AgentCheckpoint.checkpoint_ns == checkpoint_ns,
            )
        if before and (cutoff := get_checkpoint_id(before)):
            query = query.where(AgentCheckpoint.checkpoint_id < cutoff)
        for key, value in (filter or {}).items():
            query = query.where(
                AgentCheckpoint.checkpoint_metadata[key].astext == json.dumps(value).strip('"')
            )
        if limit is not None:
            query = query.limit(limit)

        async with self._db(config or {"configurable": {}}) as db:
            rows = list((await db.execute(query)).scalars().all())
            writes = {
                row.checkpoint_id: await self._pending_writes(
                    db, row.thread_id, row.checkpoint_ns, row.checkpoint_id
                )
                for row in rows
            }

        for row in rows:
            yield self._to_tuple(row, writes[row.checkpoint_id])

    async def _pending_writes(
        self, db: Any, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        rows = (
            await db.execute(
                select(AgentCheckpointWrite)
                .where(
                    AgentCheckpointWrite.thread_id == thread_id,
                    AgentCheckpointWrite.checkpoint_ns == checkpoint_ns,
                    AgentCheckpointWrite.checkpoint_id == checkpoint_id,
                )
                .order_by(AgentCheckpointWrite.task_id, AgentCheckpointWrite.idx)
            )
        ).scalars().all()
        return [
            (row.task_id, row.channel, self.serde.loads_typed((row.type or "", row.blob)))
            for row in rows
        ]

    def _to_tuple(
        self, row: AgentCheckpoint, pending_writes: list[tuple[str, str, Any]]
    ) -> CheckpointTuple:
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint=self.serde.loads_typed((row.type or "", row.checkpoint)),
            metadata=row.checkpoint_metadata or {},
            parent_config=(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
            pending_writes=pending_writes,
        )

    # ── Writes ────────────────────────────────────────────────────

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, checkpoint_ns = self._address(config)
        parent_id = config["configurable"].get("checkpoint_id")
        type_, blob = self.serde.dumps_typed(checkpoint)

        values = {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint["id"],
            "parent_checkpoint_id": parent_id,
            "type": type_,
            "checkpoint": blob,
            "metadata": _jsonable(get_checkpoint_metadata(config, metadata)),
        }
        statement = insert(AgentCheckpoint.__table__).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["thread_id", "checkpoint_ns", "checkpoint_id"],
            set_={
                "parent_checkpoint_id": statement.excluded.parent_checkpoint_id,
                "type": statement.excluded.type,
                "checkpoint": statement.excluded.checkpoint,
                "metadata": statement.excluded.metadata,
            },
        )

        async with self._db(config) as db:
            await db.execute(statement)

        return {
            "configurable": {
                **config["configurable"],
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_ns = self._address(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]

        rows = []
        for index, (channel, value) in enumerate(writes):
            type_, blob = self.serde.dumps_typed(value)
            rows.append(
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    # Errors, interrupts and resumes claim reserved negative
                    # slots; ordinary channel writes are positional.
                    "idx": WRITES_IDX_MAP.get(channel, index),
                    "channel": channel,
                    "type": type_,
                    "blob": blob,
                    "task_path": task_path,
                }
            )
        if not rows:
            return

        statement = insert(AgentCheckpointWrite.__table__).values(rows)
        # A positional write is written once and never revised. The reserved
        # slots are the ones a retry legitimately overwrites.
        statement = statement.on_conflict_do_update(
            index_elements=["thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"],
            set_={
                "channel": statement.excluded.channel,
                "type": statement.excluded.type,
                "blob": statement.excluded.blob,
            },
            where=AgentCheckpointWrite.__table__.c.idx < 0,
        )

        async with self._db(config) as db:
            await db.execute(statement)

    async def adelete_thread(self, thread_id: str) -> None:
        """Forget a conversation entirely."""
        config = thread_config(thread_id)
        async with self._db(config) as db:
            for model in (AgentCheckpointWrite, AgentCheckpoint):
                await db.execute(delete(model).where(model.thread_id == str(thread_id)))


def _jsonable(metadata: Any) -> dict:
    """Checkpoint metadata as something JSONB will accept.

    It is small and flat in practice — source, step, parents — but it is written
    by LangGraph rather than by us, so anything unexpected is stringified rather
    than allowed to fail a turn that has otherwise succeeded.
    """
    try:
        return json.loads(json.dumps(dict(metadata), default=str))
    except (TypeError, ValueError):
        logger.warning("Checkpoint metadata was not JSON-serialisable; storing a summary")
        return {"unserializable": str(metadata)[:500]}
