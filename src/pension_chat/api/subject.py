"""The conversation's people: chosen from a name's candidates, removed, restored.

A choice card sends back the opaque `ref` it was given; the server re-resolves
it in PBMS (active members only) before storing it. `ref` is the internal MEAB
ID: never shown, carried only so the UI can say which card or chip it means.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import update

from pension_chat.agent.people import MAX_PEOPLE
from pension_chat.api.auth import TokenData, get_current_user
from pension_chat.db.models import Session
from pension_chat.db.session import get_db_session
from pension_chat.records import EmployeeMatch, RecordProvider, RecordUnavailable, current_provider, normalise_meab_id

router = APIRouter(prefix="/api/v1", tags=["subjects"])


class AddPerson(BaseModel):
    ref: str


def roster_entry(match: EmployeeMatch) -> dict[str, Any]:
    return {"ref": match.key, "display_name": match.display_name, "first_name": match.first_name,
            "last_name": match.last_name, "department": match.department, "employer": match.employer,
            "birth_year": match.birth_year}


def add_to_roster(roster: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    return ([entry] + [e for e in roster if e["ref"] != entry["ref"]])[:MAX_PEOPLE]


def remove_from_roster(roster: list[dict[str, Any]], ref: str) -> list[dict[str, Any]]:
    return [e for e in roster if e["ref"] != ref]


def by_recency(roster: list[dict[str, Any]], used: list[str]) -> list[dict[str, Any]]:
    """The people a turn was about move to the front, in the turn's order."""
    front = [e for ref in used for e in roster if e["ref"] == ref]
    return front + [e for e in roster if e["ref"] not in used]


async def resolve_ref(provider: RecordProvider | None, ref: str) -> EmployeeMatch:
    if provider is None:
        raise HTTPException(status_code=404, detail="Employee records are not enabled")
    key = normalise_meab_id(ref)
    if key is None:
        raise HTTPException(status_code=400, detail="Not an employee reference")
    try:
        matches = await provider.find_employees(key)
    except RecordUnavailable:
        raise HTTPException(status_code=503, detail="The fund's records are not answering just now") from None
    if not matches:
        raise HTTPException(status_code=404, detail="No active employee with that reference")
    return matches[0]


async def store_roster(session: Session, roster: list[dict[str, Any]]) -> None:
    from pension_chat.api.sessions import _thread_owner

    owner = _thread_owner(session)
    async with get_db_session(
        employee_id=uuid.UUID(owner["employee_id"]) if owner["employee_id"] else None,
        session_token=owner["session_token"],
    ) as db:
        await db.execute(update(Session).where(Session.id == session.id).values(subjects=roster))
        await db.commit()


@router.get("/sessions/{session_id}/subjects")
async def get_people(session_id: str, request: Request,
                     user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.agent.graph import people_of
    from pension_chat.api.sessions import _authorised_session, _thread_owner

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    roster = list(session.subjects or [])
    active = await people_of(session_id, **_thread_owner(session))
    return {"people": roster, "active": [r for r in active if any(e["ref"] == r for e in roster)]}


@router.post("/sessions/{session_id}/subjects")
async def add_person(session_id: str, body: AddPerson, request: Request,
                     user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    match = await resolve_ref(current_provider(), body.ref)
    roster = add_to_roster(list(session.subjects or []), roster_entry(match))
    await store_roster(session, roster)
    return {"people": roster}


@router.delete("/sessions/{session_id}/subjects/{ref}")
async def remove_person(session_id: str, ref: str, request: Request,
                        user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    roster = remove_from_roster(list(session.subjects or []), ref)
    await store_roster(session, roster)
    return {"people": roster}


@router.delete("/sessions/{session_id}/subjects")
async def clear_people(session_id: str, request: Request,
                       user: TokenData | None = Depends(get_current_user)) -> dict[str, Any]:
    from pension_chat.api.sessions import _authorised_session

    session = await _authorised_session(uuid.UUID(session_id), request, user)
    await store_roster(session, [])
    return {"people": []}
