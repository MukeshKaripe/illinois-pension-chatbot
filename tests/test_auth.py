"""Tests for who is allowed to do what.

The hole these close is small to describe and large in effect. `require_auth`
asks whether somebody is signed in; three endpoints in `api/admin.py` needed to
ask a different question, and used `require_auth` anyway. One of them,
`POST /formulas/{id}/promote`, sets a recipe to `approved` and permits
`execution_policy='authoritative'` — which `_format_calculation` reads to decide
whether a figure is labelled an estimate. So any member's token could change how
figures are presented to every member, past the human review the architecture
doc puts that gate behind.

It was survivable only because registration required a pre-seeded email and the
pilot had two. Self-service signup is what turns it into a hole anyone can walk
through, which is why it is fixed before signup rather than after.
"""

from __future__ import annotations

import ast
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from pension_chat import personas
from pension_chat.api.auth import (
    SignupRequest,
    TokenData,
    require_admin,
)
from pension_chat.config import (
    MIN_JWT_SECRET_LENGTH,
    PLACEHOLDER_JWT_SECRET,
    Settings,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "pension_chat"


# ── The admin role ────────────────────────────────────────────────


def _session_returning(value):
    """A stand-in for `get_admin_session()` yielding one scalar."""

    class _Result:
        def scalar_one_or_none(self):
            return value

    class _Session:
        async def execute(self, _statement):
            return _Result()

    @asynccontextmanager
    async def factory():
        yield _Session()

    return factory


@pytest.mark.asyncio
async def test_an_ordinary_member_is_refused(monkeypatch):
    monkeypatch.setattr("pension_chat.api.auth.get_admin_session", _session_returning(False))
    user = TokenData(account_id=uuid.uuid4(), email="member@example.com")

    with pytest.raises(HTTPException) as raised:
        await require_admin(user)

    assert raised.value.status_code == 403
    # Deliberately the answer an unknown endpoint would give. A member who is
    # not an administrator has no business learning the surface exists.
    assert raised.value.detail == "Not found"


@pytest.mark.asyncio
async def test_a_member_whose_row_has_gone_is_refused(monkeypatch):
    """A valid token outliving its row must not be read as permission."""
    monkeypatch.setattr("pension_chat.api.auth.get_admin_session", _session_returning(None))
    user = TokenData(account_id=uuid.uuid4(), email="ghost@example.com")

    with pytest.raises(HTTPException):
        await require_admin(user)


@pytest.mark.asyncio
async def test_an_administrator_passes_through(monkeypatch):
    monkeypatch.setattr("pension_chat.api.auth.get_admin_session", _session_returning(True))
    user = TokenData(account_id=uuid.uuid4(), email="admin@example.com")

    assert await require_admin(user) is user


def test_every_admin_endpoint_asks_for_an_administrator():
    """Structural, because this is the kind of thing that regresses silently.

    A new endpoint added to `api/admin.py` with `require_auth` copied from its
    neighbours looks exactly like the others and is wrong in the way that
    matters. Read the dependencies out of the source rather than trusting review.
    """
    tree = ast.parse((SRC / "api" / "admin.py").read_text(encoding="utf-8"))

    routes: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        decorated = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put", "patch", "delete"}
            for d in node.decorator_list
        )
        if not decorated:
            continue
        depends: set[str] = set()
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            if (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and default.args
                and isinstance(default.args[0], ast.Name)
            ):
                depends.add(default.args[0].id)
        routes[node.name] = depends

    assert routes, "found no routes in api/admin.py — this test is checking nothing"
    for name, depends in routes.items():
        assert "require_auth" not in depends, (
            f"{name} guards on require_auth, which only asks whether somebody is "
            "signed in. Administrative endpoints need require_admin."
        )
        assert "require_admin" in depends, f"{name} has no administrator check"


# ── The signing key ───────────────────────────────────────────────


def _settings(**overrides) -> Settings:
    base = {
        "azure_openai_api_key": "x",
        "azure_openai_endpoint": "x",
        "azure_search_api_key": "x",
        "azure_search_endpoint": "x",
        "pghost": "x",
        "pguser": "x",
        "pgpassword": "x",
    }
    return Settings(_env_file=None, **(base | overrides))


def test_development_may_use_the_shipped_secret():
    """Otherwise every test and every checkout needs ceremony to start."""
    for env in ("development", "test"):
        assert _settings(app_env=env).jwt_secret_key == PLACEHOLDER_JWT_SECRET


def test_production_refuses_the_shipped_secret():
    """It is a sentence in English, printed in config.py, signing HS256 tokens.

    Those tokens carry the employee id row-level security is keyed on, so
    forging one reads somebody else's salary history.
    """
    with pytest.raises(ValueError, match="placeholder"):
        _settings(app_env="production")


def test_production_refuses_a_short_secret():
    with pytest.raises(ValueError, match="minimum"):
        _settings(app_env="production", jwt_secret_key="too-short")


def test_production_accepts_a_real_secret():
    secret = "a" * MIN_JWT_SECRET_LENGTH
    assert _settings(app_env="production", jwt_secret_key=secret).jwt_secret_key == secret


# ── Signup validation ─────────────────────────────────────────────


def _signup(**overrides) -> dict:
    return {
        "email": "New.Member@Example.com",
        "password": "a-long-enough-passphrase",
        "persona": "article-11-standard",
    } | overrides


def test_the_email_is_lowercased_to_match_the_index():
    """The unique index is on lower(email), so the payload has to agree.

    Otherwise a duplicate arrives as a constraint violation naming a value the
    member would not recognise, instead of a clean 409.
    """
    assert SignupRequest(**_signup()).email == "new.member@example.com"
    assert SignupRequest(**_signup(email="  Spaced@Example.com  ")).email == (
        "spaced@example.com"
    )


def test_an_unparseable_email_is_refused():
    with pytest.raises(ValidationError):
        SignupRequest(**_signup(email="not-an-address"))


@pytest.mark.parametrize(
    ("password", "why"),
    [
        ("short", "at least"),
        ("a" * 129, "at most 128"),
        # bcrypt truncates at 72 *bytes* without saying so, and a
        # length-over-composition rule is exactly what encourages a long
        # passphrase. Refusing beats silently shortening.
        ("é" * 40, "bytes"),
    ],
)
def test_a_password_bcrypt_would_mangle_is_refused(password, why):
    with pytest.raises(ValidationError, match=why):
        SignupRequest(**_signup(password=password))


def test_a_password_of_exactly_seventy_two_bytes_is_accepted():
    """The bound is bcrypt's, so the last byte that survives must be allowed."""
    assert len(SignupRequest(**_signup(password="a" * 72)).password) == 72


def test_signup_cannot_set_the_fields_that_would_matter():
    """An open endpoint plus a permissive schema is a privilege-escalation path.

    `extra="forbid"` is what makes an `is_admin` in the payload a 422 rather
    than a field silently ignored today and silently honoured the first time
    somebody builds the row by splatting the request.
    """
    for smuggled in ({"is_admin": True}, {"tier": 1}, {"id": "x"}, {"is_synthetic": False}):
        with pytest.raises(ValidationError):
            SignupRequest(**_signup(**smuggled))

    with pytest.raises(ValidationError):
        SignupRequest(**_signup(edits={"tier": 2}))


def test_edits_are_limited_to_the_editable_fields():
    request = SignupRequest(**_signup(edits={"current_annual_salary": "90000.00"}))
    assert set(request.edits.model_dump(exclude_none=True)) == {"current_annual_salary"}
    assert set(request.edits.model_dump()) <= set(personas.EDITABLE_FIELDS)


def test_an_employment_status_outside_the_check_constraint_is_refused():
    with pytest.raises(ValidationError):
        SignupRequest(**_signup(edits={"employment_status": "on sabbatical"}))
