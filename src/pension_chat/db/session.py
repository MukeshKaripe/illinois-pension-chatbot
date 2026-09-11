"""Async SQLAlchemy engine and session management with RLS support.

Every database connection sets the appropriate RLS session variables
(app.current_employee_id, app.current_session_token) before executing
queries, ensuring row-level security policies work correctly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio
from sqlalchemy import event, exc
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pension_chat.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=10,
    max_overflow=5,
    # `pool_pre_ping` issues a liveness SELECT before handing out a pooled
    # connection. Against a remote Azure Postgres that is a full network round
    # trip (~0.5s) on every checkout, and the record fetch sits on the answer's
    # critical path. Recycling connections well inside the server's idle timeout
    # covers the staleness the ping was there to catch.
    pool_pre_ping=settings.db_pool_pre_ping,
    pool_recycle=settings.db_pool_recycle_seconds,
)

@event.listens_for(engine.sync_engine, "checkout")
def discard_closed_connection(
    dbapi_connection: object, connection_record: object, connection_proxy: object
) -> None:
    """Refuse a pooled connection whose socket is already closed.

    `pool_pre_ping` would catch this with a SELECT on every checkout -- a full
    round trip to Azure, which is why it is off. asyncpg knows locally whether
    its connection is closed, so this costs nothing; raising DisconnectionError
    makes the pool discard the connection and hand out another.

    What closes one while it sits in the pool: a request cancelled mid-cleanup
    (Stop, a closed tab) or a dropped network. The next request used to receive
    it and fail -- "connection is closed", a 500 straight after a Stop, even
    with the session helpers' cleanup shielded.
    """
    driver = getattr(dbapi_connection, "driver_connection", None)
    if driver is not None and driver.is_closed():
        raise exc.DisconnectionError("pooled connection was already closed")


async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_db_session(
    employee_id: uuid.UUID | None = None,
    session_token: str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session with RLS variables set.

    Args:
        employee_id: The authenticated employee's UUID (from JWT).
        session_token: Anonymous session token for unauthenticated sessions.
    """
    session = async_session_factory()
    try:
        # Both identities in one statement.
        #
        # Via `set_config(..., is_local => true)` rather than `SET LOCAL`, which
        # takes no bind parameters -- `SET LOCAL app.current_employee_id = $1`
        # is a syntax error, so this helper raised on every call that passed an
        # identity. It had no callers to notice.
        #
        # One round trip rather than two, because the database is ~232ms away
        # and this sits in front of the member's record fetch. An unset identity
        # is passed as the empty string, which the policies read through NULLIF.
        await session.execute(
            sa_text(
                "SELECT set_config('app.current_employee_id', :eid, true),"
                "       set_config('app.current_session_token', :st, true)"
            ),
            {"eid": str(employee_id or ""), "st": session_token or ""},
        )
        yield session
        await session.commit()
    except BaseException:
        # Cancellation included. A client disconnect -- Stop, a closed tab --
        # cancels the request's scope, and in a cancelled scope every unshielded
        # await is cancelled again. Closed by `async with` under that, SQLAlchemy
        # could not finish rolling back or terminating the connection, and the
        # next request checked out a closed one: "connection is closed", a 500
        # straight after a Stop. Shielded, cleanup completes whatever cancelled
        # the work. (tests/test_session_cleanup.py)
        with anyio.CancelScope(shield=True):
            await session.rollback()
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await session.close()


@asynccontextmanager
async def get_admin_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session that can see everything — admin and system operations.

    The escape hatch used to be a role grant: the application connected as a
    role holding BYPASSRLS, so *every* statement skipped the policies and this
    function differed from the scoped one only by omission. It is now an
    explicit, transaction-local setting the policies test for, which means an
    unrestricted read has to say so, and shows up in a diff when it does.

    Legitimate users: the admin API, the health check, and the login path, which
    looks an employee up by email before there is any identity to scope to.
    """
    session = async_session_factory()
    try:
        await session.execute(sa_text("SELECT set_config('app.admin', 'on', true)"))
        yield session
        await session.commit()
    except BaseException:
        # As in get_db_session: cleanup must survive a cancelled request.
        with anyio.CancelScope(shield=True):
            await session.rollback()
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await session.close()
