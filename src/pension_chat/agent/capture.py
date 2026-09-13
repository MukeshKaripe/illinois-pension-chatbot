"""A rejected answer, written down where it can be read again.

Every hard verification failure is a labelled example of the thing this system
exists to prevent — the answer is wrong in a way code could name, and something
named it. Today that is logged at WARNING and dropped, which means the evidence
survives exactly as long as a terminal scrollback.

Both defects found in Phase 3c needed that evidence recovered by hand. The loop
cited Section 11-124 having only seen it in an outline, and diagnosing it took a
live re-run with extra instrumentation. A 400 on `content: null` cost two cases
their first turn and read as an infrastructure blip through two runs, because
the offending message sat several calls upstream of where it surfaced. A capture
file would have held both.

Two rules, and neither is about the file format.

**It must never cost the member their turn.** The failure has already happened
and the fallback is already going out; an exception here would replace a
degraded answer with no answer at all. Everything is wrapped and reported.

**No names.** The file records the session, the questions, the rejected answer
and the failing checks. Not the member's name, their account, or their email.
The pilot's members are synthetic; the habit is not.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pension_chat.agent.guardrails import GuardrailReport
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.capture")

#: Everything a capture may say about who was asking. `session_id` identifies
#: the conversation for anyone re-running it; nothing here identifies a person.
_SAFE_RECORD_KEYS = ("article_number", "tier", "employment_status")

_SESSION_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _enabled() -> bool:
    return bool(get_settings().capture_failures)


def _capture_dir() -> Path:
    return Path(get_settings().capture_dir)


def _turns(state: dict[str, Any]) -> list[dict[str, str]]:
    """The conversation so far, plus the question that failed.

    A rejected answer on turn three is not reproducible from turn three. What
    came before it is most of why it happened.
    """
    turns: list[dict[str, str]] = []
    for message in state.get("messages") or []:
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        kind = getattr(message, "type", "") or ""
        turns.append({"q": content} if kind == "human" else {"a": content})
    if question := str(state.get("question") or "").strip():
        turns.append({"q": question})
    return turns


def record_failure(
    state: dict[str, Any], report: GuardrailReport, session_id: str | None = None
) -> Path | None:
    """Write one rejected answer to the capture directory.

    Returns the path, or ``None`` when capture is off, the answer passed, or
    anything at all went wrong.
    """
    try:
        if report.passed or not _enabled():
            return None

        record = state.get("employee_record") or {}
        session = str(session_id or state.get("session_id") or "unknown")
        document = {
            "session_id": session,
            "captured_at": datetime.now(UTC).isoformat(),
            "member": {k: record[k] for k in _SAFE_RECORD_KEYS if k in record},
            "turns": _turns(state),
            "rejected_answer": str(state.get("final_answer") or ""),
            "failed": list(report.hard_failures),
            # What it cited that it should not have, or quoted that traced to
            # nothing. The one piece of evidence a bare code does not carry.
            "offenders": list(report.unverified_citations) + list(report.unverified_numbers),
            # What it *had* in front of it, beside what it used. A citation to
            # an unread section means nothing without the read set next to it.
            "sections_read": [
                s["section"] for s in (state.get("retrieved_sections") or []) if s.get("section")
            ],
            "coverage": state.get("coverage") or {},
        }

        directory = _capture_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        codes = "-".join(f["code"] for f in report.hard_failures[:2]).lower() or "failure"
        path = directory / f"{stamp}-{_SESSION_SAFE.sub('', session)[:12]}-{codes}.yaml"
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        logger.info("Captured a rejected answer: %s", path)
        return path
    except Exception as exc:  # noqa: BLE001 - never costs the member their turn
        logger.warning("Could not capture the rejected answer: %s", exc)
        return None
