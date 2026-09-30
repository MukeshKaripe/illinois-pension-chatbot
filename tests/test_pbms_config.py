"""The PBMS settings exist, default to off, and never show the password."""
from __future__ import annotations

from pension_chat.config import Settings


def _settings(**overrides):
    base = dict(
        azure_openai_api_key="k", azure_openai_endpoint="https://x", azure_search_api_key="k",
        azure_search_endpoint="https://x", pghost="h", pguser="u", pgpassword="p",
        _env_file=None,
    )
    return Settings(**{**base, **overrides})


def test_the_record_source_defaults_to_postgres() -> None:
    assert _settings().record_source == "postgres"


def test_the_password_is_a_secret() -> None:
    s = _settings(record_source="pbms", pbms_sql_password="hunter2-xyz")
    assert "hunter2-xyz" not in repr(s)
    assert s.pbms_sql_password is not None
    assert s.pbms_sql_password.get_secret_value() == "hunter2-xyz"
