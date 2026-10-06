"""A per-turn record of what the turn did over the network, for the eval suite.

A context variable, like token usage in llm.py: `begin()` starts one for the
turn in flight, and every remote call made inside that turn -- model,
embeddings, search, Postgres -- is timed into it. Nothing is recorded, and
nothing costs anything, when no instrument is in force (production). Nothing
here enters the graph state.

It also records which subsections of each section were actually rendered to
the model (`rendered`). Retrieved is not the same as read: on 2026-09-30,
Section 1-160 was retrieved on every run while its subsection (c) -- the
answer -- never reached the model.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event


@dataclass
class Call:
    kind: str
    label: str
    start: float
    end: float
    #: False for an attempt that raised (a 429 later retried): its time is real,
    #: but it is not a call the turn's budget counts.
    ok: bool = True


@dataclass
class Instrument:
    calls: list[Call] = field(default_factory=list)
    notes: list[tuple[float, str, str]] = field(default_factory=list)
    rendered: dict[str, list[str]] = field(default_factory=dict)
    pending_connect: float | None = None


_current: ContextVar[Instrument | None] = ContextVar("pension_chat_instrument", default=None)


def begin() -> Instrument:
    inst = Instrument()
    _current.set(inst)
    return inst


def current() -> Instrument | None:
    return _current.get()


@asynccontextmanager
async def timed(kind: str, label: str = "") -> AsyncIterator[None]:
    inst = _current.get()
    if inst is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    except BaseException:
        inst.calls.append(Call(kind, label, start, time.perf_counter(), ok=False))
        raise
    inst.calls.append(Call(kind, label, start, time.perf_counter()))


def note(kind: str, detail: str = "") -> None:
    inst = _current.get()
    if inst is not None:
        inst.notes.append((time.perf_counter(), kind, detail))


def rendered(section: str, subsection_ids: list[str]) -> None:
    inst = _current.get()
    if inst is None:
        return
    seen = inst.rendered.setdefault(section, [])
    for sid in subsection_ids:
        if sid not in seen:
            seen.append(sid)


def install_sqlalchemy(engine: Any) -> None:
    """Time every statement and every new connection on this engine.

    SQLAlchemy's async engine runs these events in the caller's context
    (checked 2026-09-30), so the instrument in force is the one for this turn.
    """
    sync = engine.sync_engine

    @event.listens_for(sync, "before_cursor_execute")
    def _before(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        if _current.get() is not None:
            conn.info["_instrument_start"] = time.perf_counter()

    @event.listens_for(sync, "after_cursor_execute")
    def _after(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        inst = _current.get()
        start = conn.info.pop("_instrument_start", None)
        if inst is not None and start is not None:
            inst.calls.append(Call("postgres", statement.split(None, 1)[0].lower(), start, time.perf_counter()))

    @event.listens_for(sync, "do_connect")
    def _do_connect(dialect: Any, conn_rec: Any, cargs: Any, cparams: Any) -> None:
        inst = _current.get()
        if inst is not None:
            inst.pending_connect = time.perf_counter()

    @event.listens_for(sync, "connect")
    def _connect(dbapi_connection: Any, connection_record: Any) -> None:
        inst = _current.get()
        if inst is not None and inst.pending_connect is not None:
            inst.calls.append(Call("postgres_connect", "", inst.pending_connect, time.perf_counter()))
            inst.pending_connect = None
