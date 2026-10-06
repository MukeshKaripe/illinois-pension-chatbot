"""Secrets never print.

A failing assertion once printed the whole Settings object -- both Azure keys,
the Postgres password and the JWT signing key in plain text. Any traceback, log
line or debugger view that shows the settings would do the same. Each secret is
a SecretStr: it prints as asterisks, and code asks for the value by name.
"""

from __future__ import annotations

from pension_chat.config import Settings

SECRETS = {
    "azure_openai_api_key": "openai-key-1234567890abcdef",
    "azure_search_api_key": "search-key-1234567890abcdef",
    "azure_storage_connection_string": "DefaultEndpointsProtocol=https;AccountKey=storage-key-123",
    "pgpassword": "pg-password-1234567890",
    "jwt_secret_key": "jwt-secret-1234567890abcdef1234567890",
}


def _settings() -> Settings:
    return Settings(_env_file=None, azure_openai_endpoint="https://x", azure_search_endpoint="https://x",
                    pghost="h", pguser="u", **SECRETS)


def test_no_secret_appears_when_the_settings_are_printed():
    settings = _settings()
    for shown in (repr(settings), str(settings), str(settings.model_dump())):
        leaked = [name for name, value in SECRETS.items() if value in shown]
        assert not leaked, f"printed in plain text: {leaked}"


def test_the_values_are_still_there_for_the_code_that_needs_them():
    settings = _settings()
    for name, value in SECRETS.items():
        assert getattr(settings, name).get_secret_value() == value
    assert "pg-password-1234567890" in settings.database_url
