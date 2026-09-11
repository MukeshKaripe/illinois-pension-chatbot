"""A signed-in member's profiles, and who they are (spec §6, Phase 3a).

A profile is an `employees` row belonging to the account. Everything here runs
through `get_db_session(account_id=...)`: the account policies from migration
004 admit the account's own rows and nothing else, so a bug in these handlers
cannot reach another member's profile -- the database refuses it, not this code.

Profiles are made only from presets and edited only within `personas.validate`
bounds, for the reason `personas.py` gives: the record has to stay a voice
independent of the member's, or "your record shows" stops meaning anything.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import exists, select, update
from sqlalchemy.orm import selectinload

from pension_chat import personas
from pension_chat.agent.facts import age_on
from pension_chat.api.auth import PersonaEdits, TokenData, require_auth
from pension_chat.db.models import Account, Employee, EmployeeSalaryHistory
from pension_chat.db.session import get_db_session

router = APIRouter(prefix="/api/v1", tags=["profiles"])


class ProfileItem(BaseModel):
    id: str
    persona_key: str | None
    label: str
    article_number: int
    fund: str
    tier: int
    age: int
    birth_date: date
    hire_date: date
    employment_status: str
    total_service_years: Decimal
    current_annual_salary: Decimal


class MeResponse(BaseModel):
    email: str
    display_name: str | None
    primary_profile_id: str | None
    profiles: list[ProfileItem]


class CreateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: str
    edits: PersonaEdits = PersonaEdits()


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edits: PersonaEdits


def _item(row: Employee) -> ProfileItem:
    template = personas.by_key(row.persona_key) if row.persona_key else None
    return ProfileItem(
        id=str(row.id),
        persona_key=row.persona_key,
        label=row.label or (template.label if template else "Profile"),
        article_number=row.article_number,
        fund=personas.FUND_NAMES.get(row.article_number, f"Article {row.article_number}"),
        tier=row.tier,
        age=age_on(row.birth_date, date.today()),
        birth_date=row.birth_date,
        hire_date=row.hire_date,
        employment_status=row.employment_status,
        total_service_years=Decimal(str(row.total_service_years)),
        current_annual_salary=Decimal(str(row.current_annual_salary)),
    )


def _profile_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such profile") from None


def _refuse(persona_key: str, problems: list[str]) -> HTTPException:
    # Relational, not per-field, as at signup: independent ranges still admit
    # someone hired before they were born.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"persona": persona_key, "problems": problems},
    )


@router.get("/me", response_model=MeResponse)
async def me(user: TokenData = Depends(require_auth)) -> MeResponse:
    """Who is signed in and every profile they have, in one query.

    One call feeds the greeting, the profile chip and the switcher.
    """
    async with get_db_session(account_id=user.account_id) as db:
        result = await db.execute(
            select(Account, Employee)
            .outerjoin(Employee, Employee.account_id == Account.id)
            .where(Account.id == user.account_id)
            .order_by(Employee.created_at)
        )
        rows = result.all()

    if not rows:
        # A valid token outliving its account.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No such account")
    account = rows[0][0]
    return MeResponse(
        email=account.email,
        display_name=account.display_name,
        primary_profile_id=str(account.primary_employee_id) if account.primary_employee_id else None,
        profiles=[_item(employee) for _, employee in rows if employee is not None],
    )


@router.post("/profiles", response_model=ProfileItem, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: CreateProfileRequest, user: TokenData = Depends(require_auth)
) -> ProfileItem:
    """Another profile for this account, from a preset."""
    persona = personas.by_key(body.persona)
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No such persona: {body.persona!r}",
        )
    edited = personas.apply_edits(persona, body.edits.model_dump(exclude_none=True))
    if problems := personas.validate(edited):
        raise _refuse(persona.key, problems)

    new_id = uuid.uuid4()
    async with get_db_session(account_id=user.account_id, employee_id=new_id) as db:
        # Field by field, never Employee(**payload): the account id comes from
        # the token, never from the request.
        row = Employee(
            account_id=user.account_id,
            persona_key=persona.key,
            label=persona.label,
            **personas.to_employee_kwargs(edited, employee_id=new_id),
        )
        db.add(row)
        await db.flush()
        for effective_date, salary in edited.salary_history:
            db.add(
                EmployeeSalaryHistory(
                    employee_id=new_id, effective_date=effective_date, annual_salary=salary
                )
            )
    return _item(row)


@router.patch("/profiles/{profile_id}", response_model=ProfileItem)
async def update_profile(
    profile_id: str, body: UpdateProfileRequest, user: TokenData = Depends(require_auth)
) -> ProfileItem:
    """Edit a profile within bounds. Conversations already under way keep the
    record they started with (spec §6, record pinning); new ones use this."""
    pid = _profile_id(profile_id)
    edits = body.edits.model_dump(exclude_none=True)

    async with get_db_session(account_id=user.account_id) as db:
        result = await db.execute(
            select(Employee)
            .options(selectinload(Employee.salary_history))
            .where(Employee.id == pid)
        )
        # Another account's profile is invisible under the account policy, so it
        # reads exactly like one that does not exist.
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such profile")

        history = sorted(
            ((h.effective_date, h.annual_salary) for h in row.salary_history),
            key=lambda entry: entry[0],
        )
        current = personas.persona_from_row(row, history)
        edited = personas.apply_edits(current, edits)
        if problems := personas.validate(edited):
            raise _refuse(current.key, problems)

        for field in personas.EDITABLE_FIELDS:
            setattr(row, field, getattr(edited, field))
        row.tier = edited.tier
        if "current_annual_salary" in edits:
            # The history moves with the salary (see `personas.apply_edits`), or
            # the pension would barely move when the member changes their pay.
            row.salary_history = [
                EmployeeSalaryHistory(effective_date=when, annual_salary=amount)
                for when, amount in edited.salary_history
            ]
    return _item(row)


@router.post("/profiles/{profile_id}/primary")
async def set_primary(profile_id: str, user: TokenData = Depends(require_auth)) -> dict[str, str]:
    """The profile a new chat uses unless another is chosen. One statement: the
    ownership check is part of the update."""
    pid = _profile_id(profile_id)
    async with get_db_session(account_id=user.account_id) as db:
        result = await db.execute(
            update(Account)
            .where(
                Account.id == user.account_id,
                exists(
                    select(Employee.id).where(
                        Employee.id == pid, Employee.account_id == user.account_id
                    )
                ),
            )
            .values(primary_employee_id=pid)
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such profile")
    return {"primary_profile_id": str(pid)}
