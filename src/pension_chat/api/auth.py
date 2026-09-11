"""JWT-based authentication module.

Replaces Entra External ID with local OAuth2/JWT auth backed by the
employees table in Postgres. Supports:
- User registration (for pilot testing)
- Login with email/password → JWT token
- Token validation dependency for protected endpoints
- Optional auth dependency (some endpoints work anonymously)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from pension_chat import funds, personas
from pension_chat.config import get_settings
from pension_chat.db.session import get_admin_session, get_db_session

settings = get_settings()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Length over composition, per NIST 800-63B: character-class rules push people
# toward "Password1!". The upper bound is bcrypt's, not a policy — see
# `SignupRequest._strong_enough`.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
BCRYPT_MAX_BYTES = 72
# What the greeting says; bounded so a pasted paragraph does not become a heading.
MAX_DISPLAY_NAME = 60

# OAuth2 scheme - tokenUrl points to our login endpoint
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────────────

def _display_name(value: str | None) -> str | None:
    """Whitespace collapsed and bounded; None when not given."""
    if value is None:
        return None
    name = " ".join(value.split())
    if not 1 <= len(name) <= MAX_DISPLAY_NAME:
        raise ValueError(f"Display name must be 1 to {MAX_DISPLAY_NAME} characters.")
    return name


class UserRegister(BaseModel):
    email: str
    password: str
    display_name: str | None = None

    @field_validator("display_name")
    @classmethod
    def _a_name(cls, value: str | None) -> str | None:
        return _display_name(value)


class FieldOption(BaseModel):
    """One field of the profile form, carrying the bounds of the profile's fund."""

    name: str
    label: str
    type: str
    min: Decimal | None = None
    max: Decimal | None = None
    options: list[str] = []
    # A fund's own field, sent and stored under `extra_facts`.
    extra: bool = False


class FundFacts(BaseModel):
    """What a preset or profile shows about its fund (spec §6, Phase 3b)."""

    # The fund, in the words a member uses for their employer.
    member_label: str
    employer_group: str
    support_level: str
    # The edit form is rendered from these, so its bounds are the server's.
    fields: list[FieldOption]


def fund_facts(article: int) -> dict[str, object]:
    """`FundFacts` for an Article, from `funds.py`."""
    fund = funds.for_article(article) or funds.Fund(article, "", f"Article {article}")
    return {
        "member_label": fund.member_label,
        "employer_group": fund.employer_group,
        "support_level": fund.support_level,
        "fields": [
            FieldOption(
                name=spec.name,
                label=spec.label,
                type=spec.type,
                min=spec.min,
                max=spec.max,
                options=list(spec.options),
                extra=spec.name not in funds.COMMON_FIELD_NAMES,
            )
            for spec in fund.fields
        ],
    }


class PersonaOption(FundFacts):
    """One selectable synthetic member, as the signup picker sees it."""

    key: str
    article_number: int
    label: str
    summary: str
    tier: int
    age: int
    birth_date: date
    hire_date: date
    employment_status: str
    total_service_years: Decimal
    current_annual_salary: Decimal
    extra_facts: dict[str, bool | str] = {}


class PersonaEdits(BaseModel):
    """Changes to a persona, each optional and all bounded.

    `extra="forbid"` is load-bearing rather than tidy: this is an open endpoint,
    and without it a payload carrying `is_admin` or `tier` would be accepted and
    silently ignored today, and accepted and *used* the first time somebody
    builds the Employee row by splatting the payload.
    """

    model_config = ConfigDict(extra="forbid")

    birth_date: date | None = None
    hire_date: date | None = None
    employment_status: Literal["active", "separated", "retired"] | None = None
    total_service_years: Decimal | None = None
    current_annual_salary: Decimal | None = None
    # A fund's own fields, by name. Open by shape, so `personas.validate`
    # refuses any name the profile's fund does not declare.
    extra_facts: dict[str, bool | str] | None = None


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str
    # Optional to the API, so older clients keep working; the sign-up form asks.
    display_name: str | None = None
    persona: str
    edits: PersonaEdits = PersonaEdits()

    @field_validator("display_name")
    @classmethod
    def _a_name(cls, value: str | None) -> str | None:
        return _display_name(value)

    @field_validator("email")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        # The unique index is on lower(email); matching it here keeps the error
        # a clean 409 rather than a constraint violation on a value the member
        # would not recognise as a duplicate.
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def _strong_enough(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        if len(value) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
        if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
            # bcrypt truncates at 72 bytes without saying so, which would
            # silently shorten a long passphrase — and a passphrase is exactly
            # what a length-over-composition rule encourages. Refuse instead.
            raise ValueError(
                f"Password must be at most {BCRYPT_MAX_BYTES} bytes; "
                "accented or non-Latin characters count for more than one."
            )
        return value


class UserLogin(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee_id: str | None = None


class TokenData(BaseModel):
    """Who is signed in: an account (005), not a profile.

    Which profile a request acts as is decided per conversation -- a session
    row names its profile -- so the token carries none.
    """

    account_id: uuid.UUID
    email: str


# ── Helpers ───────────────────────────────────────────────────────

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    # `typ` names what `sub` is. Tokens issued before accounts (005) carried an
    # employee id there, and `get_current_user` refuses anything without this.
    to_encode.update({"exp": expire, "typ": "account"})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> TokenData | None:
    """Decode and validate JWT token. Returns None if no token provided (anonymous)."""
    if token is None:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        account_id = payload.get("sub")
        email = payload.get("email")
        if account_id is None or email is None or payload.get("typ") != "account":
            # A token from before accounts (005) names an employee, not an
            # account. A 401 makes the client sign out cleanly rather than
            # fail later on a lookup that finds nothing.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenData(account_id=uuid.UUID(account_id), email=email)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_auth(user: TokenData | None = Depends(get_current_user)) -> TokenData:
    """Dependency that requires authentication."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(user: TokenData = Depends(require_auth)) -> TokenData:
    """Dependency for endpoints that change what every member is told.

    `require_auth` establishes that somebody is signed in, which is not the same
    question. `POST /formulas/{id}/promote` sets a recipe to `approved` and
    permits `execution_policy='authoritative'` — the gate the architecture doc
    (§7.2) puts human legal and actuarial review behind — and
    `_format_calculation` reads that same field to decide whether a figure is
    labelled an estimate. So a promotion changes how figures are presented for
    every member, and until now any member's token was enough to make one.

    The flag is read from the database on each request rather than carried as a
    token claim. A claim inside a 24-hour non-refreshable token cannot be
    revoked, and these endpoints are cold enough that the round trip is free.
    """
    async with get_admin_session() as session:
        from pension_chat.db.models import Account

        result = await session.execute(
            select(Account.is_admin).where(Account.id == user.account_id)
        )
        if not result.scalar_one_or_none():
            # Deliberately the same shape of answer as an unknown endpoint would
            # give: a member who is not an administrator has no business
            # learning that the administrative surface exists.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not found",
            )
    return user


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/personas", response_model=list[PersonaOption])
async def list_personas():
    """The members someone can sign up as: one per member fund, more for 11 and 12.

    Each carries its fund's support level. A fund whose recipes are untested
    still gets sound statute answers, and a figure may be missing; the level is
    how the screen says so, rather than leaving a member to discover it.
    """
    return [
        PersonaOption(
            key=persona.key,
            article_number=persona.article_number,
            **fund_facts(persona.article_number),
            label=persona.label,
            summary=persona.summary,
            tier=persona.tier,
            age=persona.age_today(),
            birth_date=persona.birth_date,
            hire_date=persona.hire_date,
            employment_status=persona.employment_status,
            total_service_years=persona.total_service_years,
            current_annual_salary=persona.current_annual_salary,
            extra_facts=dict(persona.extra_facts),
        )
        for persona in personas.PERSONAS
    ]


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest):
    """Create an account as one of the synthetic members.

    Signup does not ask anyone to *author* a pension record. Every determination
    downstream rests on the record being evidence independent of what the member
    says — `coverage.py` treats the record and the member's word as two voices —
    and a self-authored record is one voice agreeing with itself. Picking a
    persona keeps them apart.

    Written through `get_db_session(employee_id=...)` rather than
    `get_admin_session()`, with the id generated here. The `employees` policy is
    `id = app.current_employee_id OR admin`, so setting the identity to the row
    about to be inserted satisfies the INSERT's `WITH CHECK`, and the salary
    history policy's subselect resolves to that same row inside the transaction.
    The point is not tidiness: under an admin session a bug in this handler
    could touch any member's row, and this way the database itself makes that
    impossible.
    """
    persona = personas.by_key(body.persona)
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No such persona: {body.persona!r}",
        )

    edited = personas.apply_edits(persona, body.edits.model_dump(exclude_none=True))
    problems = personas.validate(edited)
    if problems:
        # Relational, not per-field: independent ranges still admit someone
        # hired before they were born, and the agent will answer confidently
        # about that because nothing downstream re-checks the record.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"persona": persona.key, "problems": problems},
        )

    new_account_id = uuid.uuid4()
    new_id = uuid.uuid4()
    from pension_chat.db.models import Account, Employee, EmployeeSalaryHistory

    try:
        # Both identities: the account policy admits the new account row, the
        # employee and account policies each admit the new profile.
        async with get_db_session(account_id=new_account_id, employee_id=new_id) as session:
            # Field by field, never Model(**payload): this endpoint is open, and
            # splat construction is a mass-assignment hole straight onto is_admin.
            account = Account(
                id=new_account_id,
                email=body.email,
                hashed_password=hash_password(body.password),
                display_name=body.display_name,
            )
            session.add(account)
            # The account first, then the profile, then the link back: the two
            # rows reference each other, so one has to exist before the other.
            await session.flush()
            session.add(
                Employee(
                    account_id=new_account_id,
                    persona_key=persona.key,
                    label=persona.label,
                    **personas.to_employee_kwargs(edited, employee_id=new_id),
                )
            )
            await session.flush()
            for effective_date, salary in edited.salary_history:
                session.add(
                    EmployeeSalaryHistory(
                        employee_id=new_id,
                        effective_date=effective_date,
                        annual_salary=salary,
                    )
                )
            account.primary_employee_id = new_id
            await session.commit()
    except IntegrityError:
        # Attempted rather than pre-checked. A check-then-insert races, and the
        # pre-check is also what would let someone probe which addresses are
        # already members.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address is already registered. Please log in.",
        ) from None

    return TokenResponse(
        access_token=create_access_token(
            data={"sub": str(new_account_id), "email": body.email}
        ),
        employee_id=str(new_id),
    )


@router.post("/register", response_model=TokenResponse)
async def register(body: UserRegister):
    """Claim one of the pre-seeded pilot members by setting its password.

    Distinct from `/signup`, which creates a new member from a persona. The two
    seeded members exist before anyone registers and carry no password; this is
    how somebody takes one over. Everyone else signs up.

    The `hashed_password IS NULL` condition is what keeps the two apart. It is
    not strictly needed today — a row created by `/signup` always has a password,
    so the 409 below already refuses — but leaving the invariant resting on
    another column's behaviour is how this becomes an account-takeover path the
    first time some other code creates a row without one.
    """
    email = body.email.strip().lower()
    async with get_admin_session() as session:
        from pension_chat.db.models import Account, Employee

        # Since 005 a claim creates an account over the seeded row, so "not yet
        # linked to an account" joins "no password" as the invariant.
        result = await session.execute(
            select(Employee).where(
                func.lower(Employee.email) == email,
                Employee.hashed_password.is_(None),
                Employee.account_id.is_(None),
            )
        )
        employee = result.scalar_one_or_none()

        if employee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No unclaimed pilot record found for this email. "
                       "Use /signup to create an account instead.",
            )

        account = Account(
            email=email,
            hashed_password=hash_password(body.password),
            display_name=body.display_name,
        )
        session.add(account)
        try:
            await session.flush()
        except IntegrityError:
            # An account already uses this address.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account already registered. Please login.",
            ) from None
        employee.account_id = account.id
        account.primary_employee_id = employee.id
        await session.commit()

        token = create_access_token(data={"sub": str(account.id), "email": email})
        return TokenResponse(
            access_token=token,
            employee_id=str(employee.id),
        )


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login with email/password and receive a JWT token."""
    async with get_admin_session() as session:
        from pension_chat.db.models import Account

        result = await session.execute(
            select(Account).where(
                func.lower(Account.email) == form_data.username.strip().lower()
            )
        )
        account = result.scalar_one_or_none()

        if (
            account is None
            or account.hashed_password is None
            or not verify_password(form_data.password, account.hashed_password)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = create_access_token(data={"sub": str(account.id), "email": account.email})
        return TokenResponse(
            access_token=token,
            employee_id=str(account.primary_employee_id) if account.primary_employee_id else None,
        )
