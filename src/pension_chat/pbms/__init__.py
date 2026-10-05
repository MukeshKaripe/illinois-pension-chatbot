"""The client's PBMS database, read-only (Phase 1)."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pension_chat.config import Settings
    from pension_chat.pbms.provider import PbmsProvider

logger = logging.getLogger(__name__)


def build_record_provider(settings: Settings) -> PbmsProvider | None:
    """The PBMS provider when RECORD_SOURCE=pbms, else None. Refuses to start half-configured."""
    if settings.record_source != "pbms":
        return None
    from pension_chat.pbms.connection import PbmsClient, connection_string
    from pension_chat.pbms.provider import PbmsProvider

    names = ("pbms_sql_server", "pbms_sql_database", "pbms_sql_user", "pbms_sql_password")
    unset = [n.upper() for n in names if not getattr(settings, n)]
    if unset:
        raise RuntimeError(f"RECORD_SOURCE=pbms needs {', '.join(unset)} in .env")
    assert settings.pbms_sql_password is not None
    client = PbmsClient(
        connection_string(
            server=str(settings.pbms_sql_server), database=str(settings.pbms_sql_database),
            user=str(settings.pbms_sql_user), password=settings.pbms_sql_password.get_secret_value(),
        ),
        query_timeout_s=settings.pbms_query_timeout_s,
    )
    return PbmsProvider(client)


async def refresh_names_forever(provider: PbmsProvider, *, every_s: float = 6 * 3600,
                                retry_s: float = 60) -> None:
    """Keep the name directory loaded: every 6 hours, or a minute after a failure with none loaded."""
    from pension_chat.records import RecordUnavailable

    await provider.warm()
    while True:
        try:
            count = await provider.load_names()
            logger.info("PBMS name directory loaded: %d names", count)
            wait = every_s
        except RecordUnavailable:
            logger.warning("PBMS name directory could not be loaded; names will not be recognised until it is"
                           if not provider.names_ready else
                           "PBMS name directory could not be refreshed; keeping the previous one")
            wait = every_s if provider.names_ready else retry_s
        await asyncio.sleep(wait)
