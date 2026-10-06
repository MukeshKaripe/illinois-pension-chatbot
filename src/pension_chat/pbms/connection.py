"""The one door to PBMS: fixed, named, read-only statements, and nothing else.

There is no method that takes SQL text. `fetch` takes a statement *name* from
`queries.REGISTRY`; anything else raises before a connection is touched. Every
connection declares itself read-only (`ApplicationIntent=ReadOnly` and the
driver's read-only access mode), names itself to the DBA
(`APP=pension-chat-readonly`), reads uncommitted so it never holds locks PBMS's
own work could wait on, and gives up on a statement after its timeout.

Errors leave as `PbmsUnavailable` with the statement name and SQLSTATE only --
`from None`, so no driver message (which can echo connection details) rides
along in the traceback.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from pension_chat.agent import instrument
from pension_chat.pbms.queries import REGISTRY, Query
from pension_chat.records import RecordUnavailable

logger = logging.getLogger(__name__)

APPLICATION_NAME = "pension-chat-readonly"
_READ_UNCOMMITTED = "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"


class PbmsUnavailable(RecordUnavailable):
    """PBMS could not answer. The message never carries credentials."""


class UnknownQuery(Exception):  # noqa: N818 -- named for what it is, as in the plan
    """A statement name that is not in the registry."""


def connection_string(*, server: str, database: str, user: str, password: str) -> str:
    def braced(value: str) -> str:
        return "{" + value.replace("}", "}}") + "}"

    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};DATABASE={braced(database)};UID={braced(user)};PWD={braced(password)};"
        "Encrypt=yes;TrustServerCertificate=yes;ApplicationIntent=ReadOnly;"
        f"APP={APPLICATION_NAME};"
    )


def _odbc_connect(conn_str: str, login_timeout: int) -> Any:
    # Imported here: a Postgres-only deployment never loads the driver manager.
    import pyodbc  # type: ignore[import-not-found,unused-ignore]

    return pyodbc.connect(conn_str, timeout=login_timeout, autocommit=True, readonly=True)


def _sqlstate(exc: BaseException) -> str:
    first = exc.args[0] if exc.args else None
    return first if isinstance(first, str) and len(first) <= 8 else type(exc).__name__


def _close_quietly(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        pass


class _Pooled:
    """A pooled connection and the cursor each statement keeps on it."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.cursors: dict[str, Any] = {}

    def close(self) -> None:
        for cursor in self.cursors.values():
            try:
                cursor.close()
            except Exception:
                pass
        _close_quietly(self.conn)


class PbmsClient:
    def __init__(
        self,
        conn_str: str,
        *,
        query_timeout_s: float = 5.0,
        max_connections: int = 8,
        connect: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._conn_str = conn_str
        self._timeout = max(1, int(query_timeout_s))
        self._connect = connect or _odbc_connect
        self._idle: queue.LifoQueue[_Pooled] = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(max_connections)

    def __repr__(self) -> str:
        return f"PbmsClient(app={APPLICATION_NAME!r})"

    async def fetch(self, name: str, **params: Any) -> list[dict[str, Any]]:
        query = REGISTRY.get(name)
        if query is None:
            raise UnknownQuery(name)
        missing = [p for p in query.params if p not in params]
        if missing:
            raise TypeError(f"{name} needs {missing}")
        args = tuple(params[p] for p in query.params)
        timeout = max(1, int(query.timeout_s or self._timeout))
        try:
            async with instrument.timed("pbms", name):
                return await asyncio.wait_for(
                    asyncio.to_thread(self._run, name, query, args, timeout), timeout=timeout * 2 + 1
                )
        except TimeoutError:
            raise PbmsUnavailable(f"PBMS query {name} timed out") from None

    async def warm(self, connections: int = 8) -> None:
        """Open the pool and prepare every lookup statement on each connection.

        pyodbc prepares a statement the first time a cursor runs it -- a second
        round trip to the server (measured from the dev machine: 480 ms against
        245 ms once prepared). Paying that at startup, on inputs that match no one,
        keeps it off the first question. A failure here is logged, never raised:
        the first real query will try again and report properly.
        """
        names = [n for n, q in REGISTRY.items() if n != "name_directory"]
        dummy = {"meab_id": "M-000000", "first_name": "", "last_name": ""}

        def prime() -> None:
            if not self._slots.acquire(timeout=self._timeout):
                return
            try:
                pooled, _ = self._checkout(fresh=True)
                for name in names:
                    query = REGISTRY[name]
                    self._execute(pooled, name, query, tuple(dummy[p] for p in query.params))
                self._idle.put(pooled)
            finally:
                self._slots.release()

        results = await asyncio.gather(*(asyncio.to_thread(prime) for _ in range(connections)),
                                       return_exceptions=True)
        failed = [r for r in results if isinstance(r, BaseException)]
        if failed:
            logger.warning("PBMS pool warm-up: %d of %d connections failed (%s)", len(failed), connections,
                           _sqlstate(failed[0]))

    def _run(self, name: str, query: Query, args: tuple[Any, ...], timeout: int) -> list[dict[str, Any]]:
        if not self._slots.acquire(timeout=self._timeout):
            raise PbmsUnavailable(f"PBMS query {name}: no connection free")
        try:
            # A pooled connection may have been dropped by the server while
            # idle; that costs one retry on a fresh one, not the user's turn.
            for attempt in (1, 2):
                pooled, reused = self._checkout(fresh=attempt == 2)
                pooled.conn.timeout = timeout
                try:
                    rows = self._execute(pooled, name, query, args)
                except Exception as exc:
                    pooled.close()
                    if attempt == 1 and reused:
                        continue
                    raise PbmsUnavailable(f"PBMS query {name} failed ({_sqlstate(exc)})") from None
                pooled.conn.timeout = self._timeout
                self._idle.put(pooled)
                return rows
            raise PbmsUnavailable(f"PBMS query {name} failed")  # unreachable
        finally:
            self._slots.release()

    def _checkout(self, *, fresh: bool) -> tuple[_Pooled, bool]:
        if not fresh:
            try:
                return self._idle.get_nowait(), True
            except queue.Empty:
                pass
        try:
            conn = self._connect(self._conn_str, self._timeout)
            conn.timeout = self._timeout
            conn.execute(_READ_UNCOMMITTED)
        except Exception as exc:
            raise PbmsUnavailable(f"PBMS connection failed ({_sqlstate(exc)})") from None
        return _Pooled(conn), False

    @staticmethod
    def _execute(pooled: _Pooled, name: str, query: Query, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        # One cursor per statement per connection: a reused cursor runs its
        # prepared statement again in one round trip instead of two.
        cursor = pooled.cursors.get(name)
        if cursor is None:
            cursor = pooled.cursors[name] = pooled.conn.cursor()
        cursor.execute(query.sql, args)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _close_all(self) -> None:
        while True:
            try:
                self._idle.get_nowait().close()
            except queue.Empty:
                return

    async def close(self) -> None:
        await asyncio.to_thread(self._close_all)
