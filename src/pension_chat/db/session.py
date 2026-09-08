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
        # Set RLS session variables.
        #
        # Via `set_config(..., is_local => true)` rather than `SET LOCAL`, which
        # takes no bind parameters -- `SET LOCAL app.current_employee_id = $1`
        # is a syntax error, so this helper raised on every call that passed an
        # identity. It had no callers to notice.
        if employee_id:
            await session.execute(
                sa_text("SELECT set_config('app.current_employee_id', :eid, true)"),
                {"eid": str(employee_id)},
            )
        if session_token:
            await session.execute(
                sa_text("SELECT set_config('app.current_session_token', :st, true)"),
                {"st": session_token},
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_admin_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session without RLS restrictions — for admin/system operations."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
