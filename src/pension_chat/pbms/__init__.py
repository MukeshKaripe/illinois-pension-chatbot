"""The client's PBMS database, read-only (Phase 1)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pension_chat.config import Settings
    from pension_chat.pbms.provider import PbmsProvider


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
