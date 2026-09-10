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
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel
from sqlalchemy import select, update

from pension_chat.api.auth import TokenData, get_current_user
from pension_chat.db.models import Session, SessionMessage
from pension_chat.db.session import get_admin_session, get_db_session
from pension_chat.sse import (
    sse_done,
    sse_error,
    sse_preamble,
    sse_replace,
    sse_status,
    sse_token,
)

logger = logging.getLogger("pension_chat.api.sessions")

router = APIRouter(prefix="/api/v1", tags=["sessions"])

# How many prior messages are loaded as conversation context for a new turn.
_HISTORY_MESSAGES = 8


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


class SessionListItem(BaseModel):
    id: str
    title: str | None
    started_at: str
    last_active_at: str


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
    user: TokenData | None = Depends(get_current_user),
):
    """Start a new chat session.

    Anonymous if no token — returns a session_token the client must send.
    Bound to employee_id if authenticated.
    """
    async with get_admin_session() as db:
        session = Session(
            employee_id=user.employee_id if user else None,
        )
        db.add(session)
        await db.flush()
        await db.refresh(session)

        session_id = str(session.id)
        session_token = session.session_token if user is None else None

        await db.commit()

        return CreateSessionResponse(
            session_id=session_id,
            session_token=session_token,
        )


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

    async with get_db_session(employee_id=user.employee_id) as db:
        result = await db.execute(
            select(Session)
            .where(Session.employee_id == user.employee_id)
            .order_by(Session.last_active_at.desc())
        )
        sessions = result.scalars().all()

        return [
            SessionListItem(
                id=str(s.id),
                title=s.title,
                started_at=s.started_at.isoformat(),
                last_active_at=s.last_active_at.isoformat(),
            )
            for s in sessions
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
        result = await db.execute(select(Session).where(Session.id == session_uuid))
        session = result.scalar_one_or_none()

        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.employee_id is not None:
            if user is None or user.employee_id != session.employee_id:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            # Anonymous session — check session_token from header
            provided_token = request.headers.get("X-Session-Token", "")
            if provided_token != session.session_token:
                raise HTTPException(status_code=403, detail="Invalid session token")

        needs_title = session.title is None

    employee_id = user.employee_id if user else None

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
        stream_failed = False

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
        except Exception:
            stream_failed = True
            logger.exception("Error in SSE stream")
            yield sse_error(
                "Something went wrong while answering. Please try again."
            )

        # The user's turn must land before the assistant's, so that reloading
        # the conversation shows them in order.
        try:
            await save_task
        except Exception:
            logger.exception("Failed to persist user message")

        answer = replaced_answer or "".join(answer_parts)
        if stream_failed and not answer:
            return

        # Persist the assistant turn, including the audit metadata the
        # architecture requires (§7.1): citations, and whether the answer passed
        # verification.
        try:
            async with _scoped() as db:
                assistant_msg = SessionMessage(
                    session_id=session_uuid,
                    role="assistant",
                    content=answer,
                    citations=final_state.get("citations"),
                    tool_calls=_audit_trail(final_state),
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
            tool_calls=_audit_trail(final_state),
            guardrail_passed=final_state.get("guardrail_passed", True),
            verified_citations=(final_state.get("guardrail_report") or {}).get(
                "verified_citations", []
            ),
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
            select(Session).where(Session.id == session_uuid)
        )
        session = result.scalar_one_or_none()

        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.employee_id is not None:
            if user is None or user.employee_id != session.employee_id:
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

        return [
            MessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                citations=m.citations,
                tool_calls=m.tool_calls,
                guardrail_passed=m.guardrail_passed,
                created_at=m.created_at.isoformat(),
            )
            for m in messages
        ]
