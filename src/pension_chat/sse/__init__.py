"""SSE streaming utilities for the chat endpoint.

Implements the event protocol from §8:
- preamble: immediate templated string (no LLM call)
- status: stage updates (planning, retrieving, calculating, answering)
- token: streamed answer tokens
- done: full metadata payload
"""

from __future__ import annotations

import json
from typing import Any

# ── Preamble templates ────────────────────────────────────────────
# Selected by cheap keyword matching — zero provenance risk because
# they make no claim about anything (§8).

_CALCULATION_KEYWORDS = {
    "calculate", "pension", "annuity", "benefit", "retire", "retirement",
    "salary", "years", "how much", "what would", "what will", "estimate",
    "scenario", "what if", "formula", "amount",
}

_GENERAL_PREAMBLES = [
    "Let me check the applicable pension rules for your question...",
    "Looking up the relevant sections of the Illinois Pension Code...",
    "Searching the pension code for information related to your question...",
]

_CALCULATION_PREAMBLES = [
    "Let me check the applicable pension rules and run the calculation for you...",
    "Looking up the relevant formulas and calculating based on the pension code...",
    "Searching the pension code and preparing to calculate your answer...",
]


def select_preamble(question: str) -> str:
    """Select a preamble template based on keyword matching.

    This is deliberately NOT an LLM call — it's a cheap string lookup
    that carries zero provenance risk (§8).
    """
    q_lower = question.lower()
    is_calculation = any(kw in q_lower for kw in _CALCULATION_KEYWORDS)

    if is_calculation:
        # Simple hash-based selection for variety
        idx = hash(question) % len(_CALCULATION_PREAMBLES)
        return _CALCULATION_PREAMBLES[idx]
    else:
        idx = hash(question) % len(_GENERAL_PREAMBLES)
        return _GENERAL_PREAMBLES[idx]


# ── SSE event formatters ─────────────────────────────────────────

def sse_event(event_type: str, data: Any) -> str:
    """Format a single SSE event."""
    if isinstance(data, str):
        json_data = json.dumps({"text": data})
    elif isinstance(data, dict):
        json_data = json.dumps(data, default=str)
    else:
        json_data = json.dumps(data, default=str)

    return f"event: {event_type}\ndata: {json_data}\n\n"


def sse_preamble(question: str) -> str:
    """Emit the preamble event — immediate, before the graph runs."""
    return sse_event("preamble", {"text": select_preamble(question)})


def sse_status(stage: str) -> str:
    """Emit a status event (planning, retrieving, calculating, answering)."""
    return sse_event("status", {"stage": stage})


def sse_progress(text: str, at: float) -> str:
    """Emit one step of the work, as it starts.

    `at` is seconds since the turn began. The client uses it to stay quiet on a
    fast turn: whether a turn was fast is only knowable in hindsight, so the
    server reports every step and the client decides what was worth showing.
    """
    return sse_event("progress", {"text": text, "at": at})


def sse_view(slot: str, markdown: str) -> str:
    """Emit one code-rendered table, as soon as it exists.

    Views are built before generation starts, so they need not wait for it.
    Sending them on `done` put every table behind the whole answer and its
    verification.
    """
    return sse_event("view", {"slot": slot, "markdown": markdown})


def sse_people(active: list[str]) -> str:
    """Who this turn is about, as soon as it is decided -- before any answer text --
    so the header chips change first (spec §4.4)."""
    return sse_event("people", {"active": list(active)})


def sse_token(delta: str) -> str:
    """Emit a single answer token."""
    return sse_event("token", {"delta": delta})


def sse_done(
    message_id: int,
    citations: list[dict] | None = None,
    tool_calls: list[dict] | None = None,
    guardrail_passed: bool = True,
    verified_citations: list[str] | None = None,
    scenarios: list[dict] | None = None,
) -> str:
    """Emit the final done event with full metadata.

    `scenarios` is the conversation's what-ifs after this turn, for the chip
    above the composer (spec §7): conversation state rather than message
    provenance, so it rides beside `tool_calls` rather than inside it.
    """
    return sse_event("done", {
        "message_id": message_id,
        "citations": citations or [],
        "tool_calls": tool_calls or [],
        "guardrail_passed": guardrail_passed,
        "verified_citations": verified_citations or [],
        "scenarios": scenarios or [],
    })


def sse_replace(text: str, reason: dict | None = None) -> str:
    """Replace everything streamed so far for this message.

    Sent when post-generation verification rejects an answer that was already
    streamed. Tokens go out as the model writes them so the member is not left
    staring at a spinner, which means an answer can be on screen before it has
    been checked; this event is how the check takes it back rather than leaving
    an unsupported figure in front of someone about to act on it.
    """
    return sse_event("replace", {"text": text, "reason": reason or {}})


def sse_error(message: str) -> str:
    """Emit an error event."""
    return sse_event("error", {"message": message})
