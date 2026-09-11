"""A client disconnect must not leave a dead connection in the pool.

Stop closes the browser's connection, and Starlette cancels the request's
scope. In a cancelled anyio scope every unshielded await is cancelled again, so
the session helpers -- closed by `async with` under that cancellation -- could
not let SQLAlchemy finish rolling back or terminating the connection. The next
request then checked out a closed one and failed with "connection is closed":
reproduced live as a 500 on the POST /sessions straight after a Stop.

The fakes below are cancelled the same way Starlette cancels: by a cancel
scope, not a one-shot task.cancel(), which is what makes the unshielded cleanup
fail rather than quietly succeed.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from sqlalchemy.exc import DisconnectionError

from pension_chat.db import session as db_session

HELPERS = ["get_db_session", "get_admin_session"]


class FakeSession:
    """Records cleanup, and needs a real await to perform each step of it."""

    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def commit(self) -> None:
        self.log.append("commit")

    async def rollback(self) -> None:
        await anyio.sleep(0)
        self.log.append("rollback")

    async def close(self) -> None:
        await anyio.sleep(0)
        self.log.append("close")


@pytest.mark.parametrize("helper", HELPERS)
def test_cleanup_completes_when_the_request_is_cancelled(
    monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    log: list[str] = []
    monkeypatch.setattr(db_session, "async_session_factory", lambda: FakeSession(log))
    open_session = getattr(db_session, helper)

    async def scenario() -> None:
        with anyio.CancelScope() as scope:
            async with open_session():
                scope.cancel()
                # Cancelled here, as an in-flight query would be.
                await anyio.sleep(1)

    anyio.run(scenario)
    assert log == ["rollback", "close"]


class _Driver:
    def __init__(self, closed: bool) -> None:
        self.closed = closed

    def is_closed(self) -> bool:
        return self.closed


class _Adapted:
    """The shape SQLAlchemy hands a pool listener: an adapter around asyncpg."""

    def __init__(self, closed: bool) -> None:
        self.driver_connection = _Driver(closed)


def test_a_closed_pooled_connection_is_refused_at_checkout() -> None:
    # Whatever closed it -- a cancelled cleanup, a dropped network -- the pool
    # must discard it rather than hand it to the next request.
    with pytest.raises(DisconnectionError):
        db_session.discard_closed_connection(_Adapted(closed=True), None, None)


def test_an_open_connection_is_handed_out() -> None:
    assert db_session.discard_closed_connection(_Adapted(closed=False), None, None) is None


@pytest.mark.parametrize("helper", HELPERS)
def test_a_normal_exit_commits_then_closes(monkeypatch: pytest.MonkeyPatch, helper: str) -> None:
    log: list[str] = []
    monkeypatch.setattr(db_session, "async_session_factory", lambda: FakeSession(log))
    open_session = getattr(db_session, helper)

    async def scenario() -> None:
        async with open_session():
            pass

    anyio.run(scenario)
    assert log == ["commit", "close"]
