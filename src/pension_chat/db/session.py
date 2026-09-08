"""Async SQLAlchemy engine and session management with RLS support.

Every database connection sets the appropriate RLS session variables
(app.current_employee_id, app.current_session_token) before executing
queries, ensuring row-level security policies work correctly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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
    async with async_session_factory() as session:
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
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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
    async with async_session_factory() as session:
        await session.execute(sa_text("SELECT set_config('app.admin', 'on', true)"))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
