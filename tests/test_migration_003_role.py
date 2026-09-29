"""Migration 003 must not try to strip BYPASSRLS from a role that has none.

PostgreSQL 16+ refuses `ALTER ROLE CURRENT_USER ...` on your own role. The app
role on the client's server is created without BYPASSRLS, so the statement is
unnecessary there — and it must stay loud on a role that does bypass, because
then every policy in this migration is decoration.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "003_enforce_rls.py"


def _upgrade_sql(monkeypatch) -> list[str]:  # type: ignore[no-untyped-def]
    executed: list[str] = []
    fake_op = types.SimpleNamespace(execute=lambda sql: executed.append(str(sql)))
    import alembic

    monkeypatch.setattr(alembic, "op", fake_op, raising=False)
    spec = importlib.util.spec_from_file_location("m003", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", fake_op)
    module.upgrade()
    return executed


def test_the_role_change_only_runs_for_a_role_that_bypasses(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    role_sql = [s for s in _upgrade_sql(monkeypatch) if "NOBYPASSRLS" in s]
    assert len(role_sql) == 1
    sql = role_sql[0]
    assert "rolbypassrls" in sql and "IF" in sql            # guarded, not unconditional
    assert "RAISE EXCEPTION" in sql                          # and loud when it cannot be done
