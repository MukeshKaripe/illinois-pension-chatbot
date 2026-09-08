"""Tests for which database sessions the code reaches for.

Row-level security was decoration until 003. The application connected as a
role holding BYPASSRLS, which outranks even FORCE ROW LEVEL SECURITY, so every
policy was skipped -- a query with no identity returned every employee and every
session message. Nothing noticed, because `get_db_session()`, the helper that
sets the identity, had no callers at all and would have raised on the first one.

Now that the policies bite, reaching for the wrong session is no longer
invisible: an unscoped read returns nothing rather than everything. That is the
safe direction to fail in, but it is still a bug, and it is the kind that hides
until someone's record is missing. So this pins the routing itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "pension_chat"

# The raw factory sets no identity. It is legitimate only where the tables have
# no policy to satisfy: `db/session.py` builds the scoped helpers out of it, and
# the registry holds statutory knowledge shared by every member, not member data.
MAY_USE_RAW_FACTORY = {
    Path("db/session.py"),
    Path("agent/registry.py"),
}


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _names_used(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


@pytest.mark.parametrize(
    "module", [p for p in _modules() if p.relative_to(SRC) not in MAY_USE_RAW_FACTORY]
)
def test_member_data_is_not_read_through_an_unscoped_session(module):
    assert "async_session_factory" not in _names_used(module), (
        f"{module.relative_to(SRC)} uses the raw session factory, which sets no "
        "identity. Use get_db_session(...) for a member's own data, or "
        "get_admin_session() if the read is genuinely unrestricted."
    )


def test_the_scoped_helper_has_callers_now():
    """It had none, which is why nobody noticed it raised."""
    users = {
        module.relative_to(SRC).as_posix()
        for module in _modules()
        if "get_db_session" in _names_used(module)
    }

    assert "agent/nodes.py" in users, "the member's record must be read scoped"
    assert "agent/checkpointer.py" in users, "checkpoints carry member facts"


def test_the_unrestricted_helper_is_used_deliberately_and_rarely():
    """Every one of these is a decision, so the list is worth keeping short.

    - auth: looks a member up by email, before there is an identity to scope to
    - admin: unrestricted by definition
    - sessions: the access check itself, which must tell "no such session" from
      "not yours"
    - checkpointer: threads that are not session rows, in the eval harness
    - main: the health check
    """
    users = {
        module.relative_to(SRC).as_posix()
        for module in _modules()
        if module.relative_to(SRC) != Path("db/session.py")
        and "get_admin_session" in _names_used(module)
    }

    assert users == {
        "api/auth.py",
        "api/admin.py",
        "api/sessions.py",
        "agent/checkpointer.py",
        "main.py",
    }
