"""The PBMS settings exist, default to off, and never show the password."""
from __future__ import annotations

import pytest

from pension_chat.config import Settings


@pytest.fixture(autouse=True)
def _no_pbms_environment(monkeypatch):
    # Some tests import scripts that call load_dotenv(), which copies .env into
    # os.environ; these settings must come only from what each test passes.
    for name in ("RECORD_SOURCE", "PBMS_SQL_SERVER", "PBMS_SQL_DATABASE", "PBMS_SQL_USER", "PBMS_SQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


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
