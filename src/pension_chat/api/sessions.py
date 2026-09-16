"""Chat session and message endpoints per §8.

- POST /api/v1/sessions           — create session
- GET  /api/v1/sessions           — list sessions (linked employees only)
- POST /api/v1/sessions/{id}/messages — send message, SSE stream answer
- GET  /api/v1/sessions/{id}/messages — reload conversation
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy import text as sa_text

from pension_chat.api.auth import TokenData, get_current_user
from pension_chat.db.models import Employee, Session, SessionMessage
from pension_chat.db.session import get_admin_session, get_db_session
from pension_chat.sse import (
    sse_done,
    sse_error,
    sse_preamble,
    sse_progress,
    sse_replace,
    sse_status,
    sse_token,
    sse_view,
)

logger = logging.getLogger("pension_chat.api.sessions")

router = APIRouter(prefix="/api/v1", tags=["sessions"])

# How many prior messages are loaded as conversation context for a new turn.
_HISTORY_MESSAGES = 8


def _sections_of(citations: Sequence[Mapping[str, Any]]) -> list[str]:
    """The section numbers in a persisted citation list, in order."""
    return [str(section) for entry in citations if (section := entry.get("section"))]


def _verified_citations(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The provisions the answer relied on and that verification confirmed.

    `state["citations"]` is every section retrieved for the turn; the guardrail
    report names the ones the answer actually cited and had verified. The client
    renders chips from the verified list, so persisting that subset — rather
    than everything retrieved — is what keeps a reloaded conversation's chips
    both present and honest.

    An answer that cited nothing keeps nothing, which is correct: there is
    nothing to attribute.
    """
    retrieved = state.get("citations") or []
    verified = set((state.get("guardrail_report") or {}).get("verified_citations") or [])
    if not verified:
        return []
    return [entry for entry in retrieved if entry.get("section") in verified]


def _audit_trail(state: dict) -> list[dict]:
    """The provenance record stored alongside each assistant message (§7.1).

    This is the audit trail a member's benefit estimate has to be defensible
    against later: which sections were read, which recipe produced the figure,
    whether it was reused or derived, and what verification concluded.
    """
    if not state:
        return []

    entries: list[dict] = []
    for provenance in state.get("recipe_provenance") or []:
        entries.append({"type": "recipe", **provenance})

    for formula_type, result in (state.get("calculation_result") or {}).items():
        entries.append(
            {
                "type": "calculation",
                "formula_type": formula_type,
                "section": result.get("section"),
                "execution_policy": result.get("execution_policy"),
                "knowledge_status": result.get("knowledge_status"),
                "risk_tier": result.get("risk_tier"),
                "result": result.get("base", {}).get("annual_amount"),
                "scenarios": [
                    {"label": s.get("label"), "result": s.get("annual_amount")}
                    for s in result.get("scenarios") or []
                ],
            }
        )

    report = state.get("guardrail_report") or {}
    if report:
        entries.append({"type": "verification", **report})

    if state.get("missing_inputs"):
        entries.append(
            {"type": "missing_inputs", "inputs": [m["name"] for m in state["missing_inputs"]]}
        )

    usage = state.get("token_usage") or {}
    if usage:
        entries.append({"type": "usage", **{k: v for k, v in usage.items() if k != "by_node"}})

    return entries


# ── Schemas ───────────────────────────────────────────────────────

class CreateSessionResponse(BaseModel):
    session_id: str
    session_token: str | None = None


class CreateSessionRequest(BaseModel):
    # Which of the account's profiles the conversation acts as; the primary
    # profile when omitted. Ignored for an anonymous session.
    profile_id: uuid.UUID | None = None


class SessionListItem(BaseModel):
    id: str
    title: str | None
    started_at: str
    last_active_at: str
    # The profile the conversation acts as, for the sidebar's second line.
    profile_id: str | None = None
    profile_label: str | None = None


class SendMessageRequest(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    citations: list | None = None
    verified_citations: list[str] | None = None
    tool_calls: list | None = None
    guardrail_passed: bool | None = None
    created_at: str


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    body: CreateSessionRequest | None = None,
    user: TokenData | None = Depends(get_current_user),
):
    """Start a new chat session.

    Anonymous if no token — returns a session_token the client must send.
    Signed in, it acts as one of the account's profiles: the one asked for, or
    the account's primary.
    """
    if user is not None:
        return await _create_profile_session(user, body.profile_id if body else None)

    async with get_admin_session() as db:
        session = Session(employee_id=None)
        db.add(session)
        # `flush()` alone is enough: the postgresql+asyncpg dialect flushes
        # single-row inserts with an implicit `RETURNING`, so `id`,
        # `session_token`, `started_at` and `last_active_at` are already
        # populated on `session` here. A `db.refresh()` used to follow this,
        # paying for a second round trip to Azure Postgres (~230-250ms, see
        # architecture doc's latency note) to re-select a row just fetched.
        # `get_admin_session()` also commits on exit, so no explicit commit
        # is needed here either.
        await db.flush()

        return CreateSessionResponse(
            session_id=str(session.id),
            session_token=session.session_token,
        )


async def _create_profile_session(
    user: TokenData, profile_id: uuid.UUID | None
) -> CreateSessionResponse:
    """A signed-in member's new conversation, in one statement.

    The ownership check and the primary-profile fallback are both part of the
    insert, so a signed-in "New chat" costs no more round trips than an
    anonymous one. A profile that is not this account's -- or that does not
    exist -- inserts nothing, which the account policy (005) would refuse anyway.
    """
    async with get_db_session(account_id=user.account_id) as db:
        result = await db.execute(
            sa_text(
                "INSERT INTO sessions (employee_id) "
                "SELECT e.id FROM employees e JOIN accounts a ON a.id = e.account_id "
                "WHERE a.id = :aid "
                "AND e.id = COALESCE(CAST(:pid AS uuid), a.primary_employee_id) "
                "RETURNING id"
            ),
            {"aid": str(user.account_id), "pid": str(profile_id) if profile_id else None},
        )
        new_id = result.scalar_one_or_none()

    if new_id is None:
        raise HTTPException(status_code=404, detail="No such profile")
    return CreateSessionResponse(session_id=str(new_id))


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(
    user: TokenData | None = Depends(get_current_user),
):
    """List the caller's own past sessions.

    Linked employees get their full session history.
    Anonymous/unlinked callers get an empty list (by design, §8).
    """
    if user is None:
        return []

    # Every profile's conversations, under the account policy (005).
    async with get_db_session(account_id=user.account_id) as db:
        result = await db.execute(
            select(Session, Employee.label)
            .join(Employee, Employee.id == Session.employee_id)
            .where(Employee.account_id == user.account_id)
            .order_by(Session.last_active_at.desc())
        )
        rows = result.all()

    return [
        SessionListItem(
            id=str(s.id),
            title=s.title,
            started_at=s.started_at.isoformat(),
            last_active_at=s.last_active_at.isoformat(),
            profile_id=str(s.employee_id),
            profile_label=label,
        )
        for s, label in rows
    ]


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SendMessageRequest,
    request: Request,
    user: TokenData | None = Depends(get_current_user),
):
    """Send a message and stream the answer back via SSE.

    Main chat endpoint. Sets session title from the first user message
    if title is still NULL.
    """
    session_uuid = uuid.UUID(session_id)

    # Only the access check runs before the response opens — it decides between
    # 200 and 403/404, so it cannot be deferred. Everything else (title,
    # last_active, saving the user turn, loading history) happens inside the
    # stream, after the preamble has gone out. Each of those is a round trip to
    # a remote Postgres, and doing all five up front delayed the first visible
    # byte by two to four seconds for work the member is not waiting on.
    # Deliberately unrestricted: this read *is* the authorisation step, and it
    # has to tell a session that does not exist from one that is not yours.
    # Everything after it is scoped.
    async with get_admin_session() as db:
        result = await db.execute(
            select(Session, Employee.account_id)
            .outerjoin(Employee, Employee.id == Session.employee_id)
            .where(Session.id == session_uuid)
        )
        found = result.one_or_none()
        session, owner_account = found if found is not None else (None, None)

        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.employee_id is not None:
            # A profile's conversation belongs to the account that owns the
            # profile (005); the token names the account.
            if user is None or owner_account != user.account_id:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            # Anonymous session — check session_token from header
            provided_token = request.headers.get("X-Session-Token", "")
            if provided_token != session.session_token:
                raise HTTPException(status_code=403, detail="Invalid session token")

        needs_title = session.title is None

    # The profile this conversation acts as: the session's own, not the token's.
    employee_id = session.employee_id

    def _scoped():
        """A session scoped to whoever this request belongs to.

        Authenticated members are scoped by employee id, anonymous ones by the
        session token the client echoes back. Everything reached after the
        access check above goes through here.
        """
        return get_db_session(
            employee_id=employee_id,
            session_token=None if employee_id else session.session_token,
        )


    async def save_user_turn() -> None:
        """Record the member's message and touch the session.

        Nothing downstream reads this back, so it runs as a background task
        rather than delaying the answer by two more round trips.
        """
        title = None
        if needs_title:
            title = body.content[:80].strip()
            if len(body.content) > 80:
                title += "..."

        async with _scoped() as db:
            values: dict = {"last_active_at": datetime.now(UTC)}
            if title is not None:
                values["title"] = title
            # One statement for both column updates rather than two round trips.
            await db.execute(
                update(Session).where(Session.id == session_uuid).values(**values)
            )
            db.add(
                SessionMessage(
                    session_id=session_uuid, role="user", content=body.content
                )
            )
            await db.commit()

    async def load_history() -> list:
        """Recent turns, so the agent can resolve references to earlier ones.

        A session with no title yet has had no previous turn, so the query is
        skipped entirely for the first message of a conversation — which is the
        common case and the one most sensitive to latency.
        """
        if needs_title:
            return []

        async with _scoped() as db:
            prior = await db.execute(
                select(SessionMessage)
                .where(SessionMessage.session_id == session_uuid)
                .order_by(SessionMessage.created_at.desc())
                .limit(_HISTORY_MESSAGES)
            )
            return [
                HumanMessage(content=m.content)
                if m.role == "user"
                else AIMessage(content=m.content)
                for m in reversed(prior.scalars().all())
            ]

    async def save_stopped_answer(text: str, user_turn: asyncio.Task[None] | None) -> None:
        """Keep an answer the member stopped, and say what it is.

        Stop closes the connection, which cancels the stream -- and in a
        cancelled scope every unshielded await is cancelled again, so the
        answer used to vanish on reload while the member's question, written on
        its own task, survived. Shielded here. The row is marked stopped and was
        never verified, so `guardrail_passed` stays NULL rather than claim either.
        """
        with anyio.CancelScope(shield=True):
            try:
                if user_turn is not None:
                    # The question lands before the answer, as on the normal path.
                    await user_turn
                async with _scoped() as db:
                    db.add(
                        SessionMessage(
                            session_id=session_uuid,
                            role="assistant",
                            content=text,
                            tool_calls=[{"type": "stopped"}],
                            guardrail_passed=None,
                        )
                    )
                    await db.commit()
            except Exception:
                logger.exception("Failed to persist a stopped answer")

    async def event_stream():
        """Stream the turn: preamble → status → answer tokens → done.

        Answer tokens are forwarded from the model as they arrive rather than
        being generated in full and then re-chunked with an artificial delay,
        which is what the first implementation did — it made time-to-first-token
        equal to the whole pipeline's runtime plus a sleep.
        """
        from pension_chat.agent.graph import run_agent_stream

        answer_parts: list[str] = []
        replaced_answer: str | None = None
        final_state: dict = {}
        card: dict | None = None
        # The what-ifs the conversation holds after this turn, for the chip.
        scenarios: list[dict] = []
        stream_failed = False
        save_task: asyncio.Task[None] | None = None
        # Set once the stream has run to its end, whichever way. Until then the
        # `finally` below owns persistence: a disconnect skips everything after it.
        completed = False

        try:
            # Templated, keyword-selected, no model call — this is the first
            # thing yielded, so it reaches the browser as soon as the response
            # opens (§8).
            yield sse_preamble(body.content)

            # The write of the member's own message is not something the answer
            # depends on, so it overlaps the agent run instead of preceding it.
            save_task = asyncio.create_task(save_user_turn())
            history = await load_history()

            async for event in run_agent_stream(
                question=body.content,
                session_id=session_id,
                employee_id=str(employee_id) if employee_id else None,
                # Seeds a thread that predates checkpointing; a thread that has
                # its own state ignores this rather than appending a second copy
                # of the conversation to itself.
                history=history,
                # Names the thread's owner, which scopes its checkpoints to
                # the same row-level policy as the transcript. This thread is a
                # real session row, so the policy can resolve it.
                thread_owner={
                    "employee_id": str(employee_id) if employee_id else None,
                    "session_token": (
                        session.session_token if employee_id is None else None
                    ),
                },
            ):
                kind = event["type"]
                if kind == "status":
                    yield sse_status(event["stage"])
                elif kind == "progress":
                    yield sse_progress(event["text"], event["at"])
                elif kind == "view":
                    yield sse_view(event["slot"], event["markdown"])
                elif kind == "token":
                    answer_parts.append(event["text"])
                    yield sse_token(event["text"])
                elif kind == "replace":
                    # Post-generation verification rejected what was streamed;
                    # tell the client to swap it for the safe message.
                    replaced_answer = event["text"]
                    yield sse_replace(event["text"], event.get("reason"))
                elif kind == "error":
                    stream_failed = True
                    logger.error("Agent error: %s", event.get("detail"))
                    yield sse_error(event["message"])
                elif kind == "done":
                    final_state = event.get("state") or {}
                    card = event.get("card")
                    scenarios = event.get("scenarios") or []
            completed = True
        except Exception:
            completed = True
            stream_failed = True
            logger.exception("Error in SSE stream")
            yield sse_error(
                "Something went wrong while answering. Please try again."
            )
        finally:
            # A disconnect (Stop) arrives as CancelledError or GeneratorExit --
            # BaseExceptions, so `except Exception` never sees them and nothing
            # below this block runs. Keep what the member watched stream in.
            if not completed and answer_parts:
                await save_stopped_answer("".join(answer_parts), save_task)

        # The user's turn must land before the assistant's, so that reloading
        # the conversation shows them in order.
        try:
            if save_task is not None:
                await save_task
        except Exception:
            logger.exception("Failed to persist user message")

        answer = replaced_answer or "".join(answer_parts)
        if stream_failed and not answer:
            return

        # The calculation card rides in the audit trail: it is provenance too,
        # and there it needs no column of its own.
        tool_calls = _audit_trail(final_state) + ([card] if card else [])

        # Persist the assistant turn, including the audit metadata the
        # architecture requires (§7.1): citations, and whether the answer passed
        # verification.
        try:
            async with _scoped() as db:
                assistant_msg = SessionMessage(
                    session_id=session_uuid,
                    role="assistant",
                    content=answer,
                    # Only the provisions the answer actually relied on and that
                    # passed verification. `citations` used to hold every
                    # retrieved section, which the client rendered as chips --
                    # six of them under an answer resting on two, implying a
                    # reliance it did not have. Persisting the verified subset is
                    # also what lets a reloaded conversation show its chips at
                    # all: there is no column for the verified list, and this one
                    # is ours to define.
                    citations=_verified_citations(final_state),
                    tool_calls=tool_calls,
                    guardrail_passed=final_state.get("guardrail_passed", True),
                )
                db.add(assistant_msg)
                await db.flush()
                await db.refresh(assistant_msg)
                msg_id = assistant_msg.id
                await db.commit()
        except Exception:
            logger.exception("Failed to persist assistant message")
            msg_id = -1

        yield sse_done(
            message_id=msg_id,
            citations=final_state.get("citations"),
            tool_calls=tool_calls,
            guardrail_passed=final_state.get("guardrail_passed", True),
            verified_citations=(final_state.get("guardrail_report") or {}).get(
                "verified_citations", []
            ),
            scenarios=scenarios,
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _authorised_session(
    session_uuid: uuid.UUID, request: Request, user: TokenData | None
) -> Session:
    """The session, if this caller may use it.

    404 when it does not exist, 403 when it is someone else's. Deliberately
    unrestricted, as in `send_message`: this read *is* the authorisation step,
    and it has to tell those two apart.
    """
    async with get_admin_session() as db:
        result = await db.execute(
            select(Session, Employee.account_id)
            .outerjoin(Employee, Employee.id == Session.employee_id)
            .where(Session.id == session_uuid)
        )
        found = result.one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session, owner_account = found
    if session.employee_id is not None:
        # A profile's conversation belongs to the account that owns the
        # profile (005); the token names the account.
        if user is None or owner_account != user.account_id:
            raise HTTPException(status_code=403, detail="Access denied")
    elif request.headers.get("X-Session-Token", "") != session.session_token:
        raise HTTPException(status_code=403, detail="Invalid session token")
    return session


def _thread_owner(session: Session) -> dict[str, str | None]:
    """Whose checkpoints these are -- the same row-level scoping a turn uses."""
    if session.employee_id is not None:
        return {"employee_id": str(session.employee_id), "session_token": None}
    return {"employee_id": None, "session_token": session.session_token}


@router.get("/sessions/{session_id}/scenarios")
async def get_scenarios(
    session_id: str,
    request: Request,
    user: TokenData | None = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """The what-ifs this conversation is holding, for the chip (spec §7)."""
    session = await _authorised_session(uuid.UUID(session_id), request, user)
    from pension_chat.agent.graph import scenarios_of

    return await scenarios_of(session_id, **_thread_owner(session))


@router.delete("/sessions/{session_id}/scenarios")
async def delete_scenarios(
    session_id: str,
    request: Request,
    label: str | None = None,
    user: TokenData | None = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Forget one scenario (`?label=`), or every one the chip shows.

    Returns what remains, so the chip redraws from the server's word.
    """
    session = await _authorised_session(uuid.UUID(session_id), request, user)
    from pension_chat.agent.graph import forget_scenarios

    return await forget_scenarios(session_id, label=label, **_thread_owner(session))


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    session_id: str,
    request: Request,
    user: TokenData | None = Depends(get_current_user),
):
    """Reload one session's conversation (page refresh / sidebar click)."""
    session_uuid = uuid.UUID(session_id)

    # As above: the access check runs unrestricted, the message read does not.
    async with get_admin_session() as db:
        result = await db.execute(
            select(Session, Employee.account_id)
            .outerjoin(Employee, Employee.id == Session.employee_id)
            .where(Session.id == session_uuid)
        )
        found = result.one_or_none()
        session, owner_account = found if found is not None else (None, None)

        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.employee_id is not None:
            # A profile's conversation belongs to the account that owns the
            # profile (005); the token names the account.
            if user is None or owner_account != user.account_id:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            provided_token = request.headers.get("X-Session-Token", "")
            if provided_token != session.session_token:
                raise HTTPException(status_code=403, detail="Invalid session token")

        # Fetch messages
        result = await db.execute(
            select(SessionMessage)
            .where(SessionMessage.session_id == session_uuid)
            .order_by(SessionMessage.created_at)
        )
        messages = result.scalars().all()

        restored = []
        for m in messages:
            # Read once. What is persisted in `citations` is already the verified
            # subset, so the client's chips survive a reload -- without the
            # second field the chips vanished on every restored conversation,
            # because that is the only field the client renders them from.
            cites = m.citations or []
            restored.append(
                MessageResponse(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    citations=cites,
                    verified_citations=_sections_of(cites),
                    tool_calls=m.tool_calls,
                    guardrail_passed=m.guardrail_passed,
                    created_at=m.created_at.isoformat(),
                )
            )
        return restored
